"""STT 앞단 배경 잡음 제거: 승인된 마이크 PCM에서 배경 잡음을 줄여 RTZR로 넘긴다.

N-04가 받은 프레임(동의·트랙 검사 통과분)만 거친다. 다른 사람 목소리는 거르지 않는다(배경 잡음만).

모델: GTCRN 스트리밍 ONNX (Rong et al., ICASSP 2024, MIT, DNS3 학습, 약 48K 파라미터).
16 kHz, n_fft 512, hop 256(16ms), sqrt-Hann 창. 프레임마다 캐시 3개를 이어 받아 상태를 유지한다.
알고리즘 지연은 창 길이 512 샘플(32ms)이다. 모델 파일은 `models/`에 두고 SHA-256으로 확인한다.

- PCM과 캐시는 메모리에만 두고 트랙이 끝나면 버린다. 파일·로그에 쓰지 않는다(B02.2).
- 모델을 못 올리면(onnxruntime 없음, 파일 손상) 경고만 남기고 원래 프레임을 그대로 보낸다 → STT는 계속 돈다.
- `HYOJASON_VOICE_DENOISE=off`로 끈다. `HYOJASON_VOICE_DENOISE_MIX`(0–1, 기본 1.0)는 잡음 제거 결과의 비율이다.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
import hashlib
import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger("hyojason.denoise")

SAMPLE_RATE = 16000
N_FFT = 512
HOP = 256
MODEL_PATH = Path(__file__).with_name("models") / "gtcrn_simple.onnx"
MODEL_SHA256 = "b4718df6228e7bdf1a8a435cf98f838636eb2fd331acabf86ba87c5192ebcb87"
ENV_MODE = "HYOJASON_VOICE_DENOISE"
ENV_MIX = "HYOJASON_VOICE_DENOISE_MIX"

# torch.hann_window(512)의 주기 창에 제곱근: 분석·합성 창을 곱하면 Hann이 되고 50% 겹침에서 합이 1이다.
_WINDOW = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)
_CACHE_SHAPES = {
    "conv_cache": (2, 1, 16, 16, 33),
    "tra_cache": (2, 3, 1, 1, 16),
    "inter_cache": (2, 1, 33, 16),
}


class ModelUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=4)
def load_session(path: str = str(MODEL_PATH), sha256: str = MODEL_SHA256):
    """ONNX 세션을 한 번만 올린다. 세션은 상태가 없어 트랙끼리 공유하고, 캐시는 트랙마다 따로 둔다."""
    try:
        import onnxruntime as ort
    except ImportError as error:
        raise ModelUnavailable("onnxruntime is not installed") from error
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ModelUnavailable("model file is missing") from error
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ModelUnavailable("model checksum mismatch")
    options = ort.SessionOptions()
    # 모델이 작아(프레임당 1ms 미만) 스레드를 늘리면 오히려 두 역할 스트림끼리 경합한다.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(data, options, providers=["CPUExecutionProvider"])


class StreamingDenoiser:
    """16 kHz mono PCM을 아무 길이로 넣으면 처리된 샘플을 순서대로 돌려준다(hop 단위)."""

    def __init__(self, session, *, mix: float = 1.0) -> None:
        if not 0.0 <= mix <= 1.0:
            raise ValueError("mix must be within [0, 1]")
        self._session = session
        self._mix = np.float32(mix)
        self.reset()

    def reset(self) -> None:
        self._caches = {name: np.zeros(shape, np.float32) for name, shape in _CACHE_SHAPES.items()}
        self._history = np.zeros(HOP, np.float32)  # 직전 hop 입력 (분석 창의 앞 절반)
        self._tail = np.zeros(HOP, np.float32)  # 직전 프레임 합성 결과의 뒤 절반 (겹쳐 더하기)
        self._pending = np.zeros(0, np.float32)
        self._skip = HOP  # 첫 출력 hop은 시작 전 0 패딩 구간이라 버린다 → 출력이 입력과 같은 시각에 맞는다
        self.frames = 0

    def process(self, pcm: np.ndarray) -> np.ndarray:
        """int16 또는 float32(−1..1) 샘플. 반환형은 입력형과 같다."""
        is_int = pcm.dtype == np.int16
        x = pcm.astype(np.float32) / 32768.0 if is_int else pcm.astype(np.float32, copy=False)
        self._pending = np.concatenate((self._pending, x.reshape(-1)))
        n = len(self._pending) // HOP
        out = np.empty(n * HOP, np.float32)
        for i in range(n):
            out[i * HOP:(i + 1) * HOP] = self._step(self._pending[i * HOP:(i + 1) * HOP])
        self._pending = self._pending[n * HOP:]
        out, self._skip = out[self._skip:], max(0, self._skip - len(out))
        return _to_int16(out) if is_int else out

    def flush(self) -> np.ndarray:
        """남은 입력을 0으로 채워 끝까지 내보낸다. 입력 전체와 같은 길이가 되도록 자른다."""
        remaining = len(self._pending) + HOP - self._skip
        out = self.process(np.zeros(2 * HOP - len(self._pending) % HOP, np.float32))
        return out[:max(0, remaining)]

    def _step(self, hop: np.ndarray) -> np.ndarray:
        frame = np.concatenate((self._history, hop)) * _WINDOW
        spec = np.fft.rfft(frame)
        mix = np.stack((spec.real, spec.imag), axis=-1).astype(np.float32)[None, :, None, :]
        enh, conv, tra, inter = self._session.run(None, {"mix": mix, **self._caches})
        self._caches = {"conv_cache": conv, "tra_cache": tra, "inter_cache": inter}
        enhanced = np.fft.irfft(enh[0, :, 0, 0] + 1j * enh[0, :, 0, 1], N_FFT).astype(np.float32) * _WINDOW
        out = self._tail + enhanced[:HOP]
        self._tail = enhanced[HOP:]
        dry = self._history  # 이번 출력 구간과 같은 시각의 원래 입력
        self._history = hop.copy()
        self.frames += 1
        if self._mix < 1.0:
            out = self._mix * out + (1.0 - self._mix) * dry
        return out


def _to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int16)


class FrameDenoiser:
    """LiveKit `rtc.AudioFrame`(보통 48 kHz) → 16 kHz로 바꿔 잡음을 줄인 프레임 목록. 트랙 하나에 하나."""

    def __init__(self, session, *, mix: float = 1.0) -> None:
        self._denoiser = StreamingDenoiser(session, mix=mix)
        self._resampler = None
        self._input_rate: int | None = None
        # 통화 중 모델 오류가 나면 잡음 제거만 멈춘다. STT 스트림은 중간에 표본율이 바뀌면 안 되므로
        # 16 kHz 변환은 계속하고 원래 소리를 보낸다.
        self.bypassed = False

    def push(self, frame) -> list:
        from livekit import rtc

        if frame.num_channels != 1:
            raise ValueError("denoiser expects mono frames")
        if self._resampler is None or frame.sample_rate != self._input_rate:
            self._input_rate = frame.sample_rate
            self._resampler = (None if frame.sample_rate == SAMPLE_RATE else
                               rtc.AudioResampler(frame.sample_rate, SAMPLE_RATE, num_channels=1))
        frames = [frame] if self._resampler is None else self._resampler.push(frame)
        out = []
        for item in frames:
            pcm = np.frombuffer(item.data, dtype=np.int16)
            if not self.bypassed:
                try:
                    pcm = self._denoiser.process(pcm)
                except Exception as error:  # onnxruntime 실행 오류 등. PCM은 로그에 남기지 않는다
                    log.warning("denoise failed (%s), sending original audio for this track", type(error).__name__)
                    self.bypassed = True
            if len(pcm):
                out.append(rtc.AudioFrame(pcm.tobytes(), SAMPLE_RATE, 1, len(pcm)))
        return out


def denoiser_factory(mode: str | None = None, mix: str | None = None) -> Callable[[], FrameDenoiser] | None:
    """환경 설정으로 트랙별 잡음 제거기를 만드는 함수를 돌려준다. 끄거나 못 올리면 None(원래 프레임 사용)."""
    mode = (mode if mode is not None else os.environ.get(ENV_MODE, "gtcrn")).strip().lower()
    if mode in {"off", "0", "false", "none"}:
        log.info("denoise off")
        return None
    if mode != "gtcrn":
        log.warning("unknown %s=%s, denoise off", ENV_MODE, mode)
        return None
    try:
        ratio = float(mix if mix is not None else os.environ.get(ENV_MIX, "1.0"))
        if not 0.0 <= ratio <= 1.0:
            raise ValueError
    except ValueError:
        log.warning("invalid %s, using 1.0", ENV_MIX)
        ratio = 1.0
    try:
        session = load_session()
    except ModelUnavailable as error:
        log.warning("denoise unavailable (%s), sending original audio", error)
        return None
    log.info("denoise gtcrn mix=%.2f", ratio)
    return lambda: FrameDenoiser(session, mix=ratio)
