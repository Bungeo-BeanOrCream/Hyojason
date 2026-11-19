"""STT 앞 잡음 제거(denoise.py) 오프라인 평가: 깨끗한 음성 + 배경 잡음 → SI-SDR 개선량과 처리 속도.

    cd server/voice
    python scripts/eval_denoise.py <깨끗한 음성 wav 폴더> [--noise-dir <잡음 wav 폴더>] [--snr 0 5 10]

- wav는 16 kHz mono 16-bit만 받는다. 실제 통화 녹음은 동의를 받은 것만 쓰고, 이 스크립트는 파일을 만들지 않는다.
- 잡음 폴더가 없으면 합성 배경 잡음 5종(백색·핑크, 선풍기 험, 그릇 소리, 차 소리, 음악)을 쓴다.
- SI-SDR은 신호 복원 지표다. RTZR 인식률은 worker `--no-denoise`와 비교해 실제 통화에서 따로 잰다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hyojason_voice.denoise import SAMPLE_RATE, StreamingDenoiser, load_session  # noqa: E402


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{path.name}: 16 kHz mono 16-bit wav만 받습니다")
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def synthetic_noises(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    t = np.arange(n) / SAMPLE_RATE
    white = rng.standard_normal(n)
    pink = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) / np.sqrt(np.arange(n // 2 + 1) + 1), n)
    hum = sum(np.sin(2 * np.pi * 120 * k * t + rng.uniform(0, 6)) / k for k in range(1, 8))
    fan = hum + 0.5 * np.convolve(white, np.ones(8) / 8, "same")
    clatter = np.zeros(n)
    for start in rng.integers(0, n, size=max(1, n // 4000)):  # 0.25초마다 하나 꼴의 짧은 충돌음
        length = min(n - start, 1600)
        decay = np.exp(-np.arange(length) / 200) * np.sin(2 * np.pi * rng.uniform(1500, 4000) * np.arange(length) / SAMPLE_RATE)
        clatter[start:start + length] += decay * rng.uniform(0.5, 1)
    brown = np.cumsum(white)
    brown -= np.convolve(brown, np.ones(4001) / 4001, "same")  # 느린 표류 제거
    traffic = brown * (1 + 0.5 * np.sin(2 * np.pi * 0.3 * t))
    chords = [(220, 277, 330), (196, 247, 294), (175, 220, 262)]
    music = np.zeros(n)
    for i, chord in enumerate(chords * (int(t[-1]) + 1)):
        part = (t >= i * 0.7) & (t < (i + 1) * 0.7)
        music[part] = sum(np.sin(2 * np.pi * f * t[part]) for f in chord)
    return {"white+pink": white + 3 * pink, "fan_hum": fan, "clatter": clatter, "traffic": traffic, "music": music}


def si_sdr(estimate: np.ndarray, clean: np.ndarray) -> float:
    clean = clean - clean.mean()
    estimate = estimate - estimate.mean()
    target = np.dot(estimate, clean) / np.dot(clean, clean) * clean
    return float(10 * np.log10(np.sum(target ** 2) / np.sum((estimate - target) ** 2)))


def mix_at(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    noise = noise[: len(clean)]
    scale = np.sqrt(np.mean(clean ** 2) / (np.mean(noise ** 2) * 10 ** (snr_db / 10)))
    noisy = clean + scale * noise
    return noisy / max(1.0, np.abs(noisy).max() / 0.99)  # 클리핑 방지 (clean도 같은 비율은 SI-SDR에 무관)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("clean_dir", type=Path)
    p.add_argument("--noise-dir", type=Path)
    p.add_argument("--snr", type=float, nargs="+", default=[0.0, 5.0, 10.0])
    p.add_argument("--mix", type=float, default=1.0, help="잡음 제거 결과 비율 (worker의 HYOJASON_VOICE_DENOISE_MIX)")
    a = p.parse_args()

    session = load_session()
    cleans = [(f.name, read_wav(f)) for f in sorted(a.clean_dir.glob("*.wav"))]
    if not cleans:
        raise SystemExit("깨끗한 음성 wav가 없습니다")
    rng = np.random.default_rng(0)
    rows: dict[tuple[str, float], list[tuple[float, float]]] = {}
    seconds, busy = 0.0, 0.0
    for _name, clean in cleans:
        if a.noise_dir:
            noises = {f.stem: np.resize(read_wav(f), len(clean)) for f in sorted(a.noise_dir.glob("*.wav"))}
        else:
            noises = synthetic_noises(len(clean), rng)
        for kind, noise in noises.items():
            for snr in a.snr:
                noisy = mix_at(clean, noise, snr)
                denoiser = StreamingDenoiser(session, mix=a.mix)
                started = time.perf_counter()
                out = [denoiser.process(noisy[i:i + 320]) for i in range(0, len(noisy), 320)]  # 20ms씩
                out.append(denoiser.flush())
                busy += time.perf_counter() - started
                seconds += len(noisy) / SAMPLE_RATE
                enhanced = np.concatenate(out)
                rows.setdefault((kind, snr), []).append((si_sdr(noisy, clean), si_sdr(enhanced, clean)))

    print(f"clean {len(cleans)}개, mix={a.mix}")
    print(f"{'noise':<12}{'SNR':>5}  {'noisy':>7}  {'denoised':>8}  {'gain':>6}  (SI-SDR dB, 평균)")
    gains = []
    for (kind, snr), values in rows.items():
        before, after = (float(np.mean(v)) for v in zip(*values))
        gains.append(after - before)
        print(f"{kind:<12}{snr:>5.0f}  {before:>7.2f}  {after:>8.2f}  {after - before:>+6.2f}")
    print(f"평균 개선 {np.mean(gains):+.2f} dB, 최저 {min(gains):+.2f} dB")
    print(f"실시간 계수 {busy / seconds:.3f} (1 스트림, 1 스레드, 1보다 작아야 실시간)")


if __name__ == "__main__":
    main()
