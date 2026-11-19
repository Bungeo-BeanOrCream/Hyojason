"""STT 앞 배경 잡음 제거(denoise.py) — 스트리밍 정합성, 정렬, 잡음 감쇠, 실패 시 원음 유지."""

import numpy as np
import pytest
from livekit import rtc

from hyojason_voice import denoise
from hyojason_voice.denoise import (
    HOP,
    SAMPLE_RATE,
    FrameDenoiser,
    ModelUnavailable,
    StreamingDenoiser,
    denoiser_factory,
    load_session,
)


@pytest.fixture(scope="module")
def session():
    return load_session()


def _noise(seconds: float, dbfs: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(SAMPLE_RATE * seconds)) * 10 ** (dbfs / 20)).astype(np.float32)


def _run(denoiser: StreamingDenoiser, x: np.ndarray, chunks) -> np.ndarray:
    out, i = [], 0
    for n in chunks:
        out.append(denoiser.process(x[i:i + n]))
        i += n
    out.append(denoiser.process(x[i:]))
    out.append(denoiser.flush())
    return np.concatenate(out)


def test_stream_output_does_not_depend_on_chunk_sizes(session):
    """LiveKit 프레임 크기(10ms·20ms·불규칙)와 상관없이 같은 결과가 나와야 한다."""
    x = _noise(1.0, -30)
    whole = _run(StreamingDenoiser(session), x, [])
    rng = np.random.default_rng(1)
    ragged = _run(StreamingDenoiser(session), x, rng.integers(1, 700, size=40).tolist())
    np.testing.assert_allclose(ragged, whole, atol=1e-6)


@pytest.mark.parametrize("length", [1, HOP - 1, HOP, 3 * HOP + 17, SAMPLE_RATE])
def test_flush_returns_exactly_the_input_length(session, length):
    x = _noise(length / SAMPLE_RATE, -30)[:length]
    assert len(_run(StreamingDenoiser(session), x, [])) == length


def test_dry_mix_is_time_aligned_with_input(session):
    """mix=0이면 모델을 거쳐도 원래 소리 그대로여야 한다 → 출력이 입력과 같은 시각에 맞는지 확인."""
    x = _noise(0.5, -20)
    y = _run(StreamingDenoiser(session, mix=0.0), x, [160] * 20)
    np.testing.assert_allclose(y, x, atol=1e-6)


def test_int16_in_int16_out(session):
    pcm = (_noise(0.2, -30) * 32768).astype(np.int16)
    out = StreamingDenoiser(session).process(pcm)
    assert out.dtype == np.int16


def test_steady_noise_is_attenuated_and_silence_stays_silent(session):
    noisy = _run(StreamingDenoiser(session), _noise(2.0, -30), [])
    reduction_db = 10 * np.log10(np.mean(_noise(2.0, -30)[SAMPLE_RATE:] ** 2) / np.mean(noisy[SAMPLE_RATE:] ** 2))
    assert reduction_db > 15, reduction_db
    silent = _run(StreamingDenoiser(session), np.zeros(SAMPLE_RATE, np.float32), [])
    assert np.max(np.abs(silent)) < 1e-4


def test_mix_must_be_a_ratio(session):
    with pytest.raises(ValueError):
        StreamingDenoiser(session, mix=1.5)


def test_checksum_mismatch_is_refused():
    with pytest.raises(ModelUnavailable):
        load_session(sha256="0" * 64)


def test_factory_is_off_by_setting_or_when_the_model_cannot_load(monkeypatch):
    assert denoiser_factory("off") is None
    assert denoiser_factory("unknown-engine") is None

    def broken(*_args, **_kwargs):
        raise ModelUnavailable("model file is missing")

    monkeypatch.setattr(denoise, "load_session", broken)
    assert denoiser_factory("gtcrn") is None  # worker는 원래 프레임을 그대로 보낸다


def test_factory_reads_mix_from_environment(monkeypatch):
    monkeypatch.setenv(denoise.ENV_MIX, "0.8")
    make = denoiser_factory("gtcrn")
    assert make is not None and make()._denoiser._mix == np.float32(0.8)


def _frames_48k(seconds: float):
    rng = np.random.default_rng(2)
    samples = (rng.standard_normal(int(48000 * seconds)) * 300).astype(np.int16)
    for i in range(0, len(samples), 480):  # LiveKit AudioStream 기본: 48 kHz mono 10ms
        chunk = samples[i:i + 480]
        yield rtc.AudioFrame(chunk.tobytes(), 48000, 1, len(chunk))


def test_livekit_frames_are_resampled_to_16k_and_denoised(session):
    cleaner = FrameDenoiser(session)
    out = [frame for item in _frames_48k(1.0) for frame in cleaner.push(item)]
    assert out and all(f.sample_rate == SAMPLE_RATE and f.num_channels == 1 for f in out)
    total = sum(f.samples_per_channel for f in out)
    assert SAMPLE_RATE - 3 * HOP <= total <= SAMPLE_RATE  # 리샘플러·창 지연만큼만 늦다
    assert not cleaner.bypassed


def test_model_error_mid_call_keeps_sending_16k_original_audio(session):
    class Failing:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("onnxruntime failure")

    cleaner = FrameDenoiser(Failing())
    out = [frame for item in _frames_48k(0.3) for frame in cleaner.push(item)]
    assert cleaner.bypassed
    assert out and all(f.sample_rate == SAMPLE_RATE for f in out)  # STT 스트림 표본율이 바뀌지 않는다
