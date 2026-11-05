"""Speech in / speech out for LatoChan, all in memory (audio is never written to disk).

  stt(audio_bytes)        -> {"text", "language", "seconds"}     faster-whisper on the CPU (int8)
  tts(text)               -> {"ogg", "seconds", "waveform"}      Piper on the CPU, encoded as Ogg/Opus 48 kHz mono
                                                                  (what Discord voice messages use) + the 256-byte
                                                                  waveform preview Discord shows, base64
"""
from __future__ import annotations

import base64
import io
import os
import re
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

VOICES = Path(os.environ.get("VOICE_DIR", "/var/lib/latovoice/voices"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DIR = os.environ.get("WHISPER_DIR", "/var/lib/latovoice/whisper")
THREADS = int(os.environ.get("VOICE_THREADS", "8"))

_whisper = None
_voices: dict = {}


def whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS,
                                download_root=WHISPER_DIR)
    return _whisper


def stt(audio: bytes, max_seconds: float = 180, hint: str = "") -> dict:
    model = whisper()
    pcm = decode16k(audio, max_seconds)
    segs, info = model.transcribe(pcm, beam_size=1, vad_filter=True, condition_on_previous_text=False,
                                  initial_prompt=hint[:300] or None)   # names/words to spell right
    text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "language": info.language, "seconds": round(info.duration, 1)}


def decode16k(audio: bytes, max_seconds: float) -> np.ndarray:
    """Any audio file (ogg/opus, m4a, mp3, wav...) -> float32 mono 16 kHz, in memory. Stops past max_seconds."""
    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out, n, limit = [], 0, int(max_seconds * 16000)
    with av.open(io.BytesIO(audio), "r") as c:
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                a = f.to_ndarray().reshape(-1)
                out.append(a)
                n += len(a)
            if n > limit:
                raise ValueError(f"too long (over {max_seconds:.0f}s)")
        for f in rs.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        raise ValueError("no audio")
    return np.concatenate(out).astype(np.float32) / 32768.0


def voice(name: str):
    if name not in _voices:
        from piper import PiperVoice
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise ValueError("bad voice name")
        _voices[name] = PiperVoice.load(str(VOICES / f"{name}.onnx"))
    return _voices[name]


def clean_for_speech(text: str) -> str:
    text = re.sub(r"(?m)^-# .*$", "", text)                     # subtext lines (system notes)
    text = re.sub(r"<a?:(\w+):\d+>|:(\w+):", "", text)           # custom emotes
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`~|>#]+", "", text)                       # markdown
    text = re.sub(r"\[\[.*?\]\]", "", text)
    text = re.sub(r"[^\w\s.,!?'’\-:;()%&/@]", "", text)          # emoji etc.
    return re.sub(r"\s+", " ", text).strip()[:600]


def tts(text: str, name: str, speed: float = 1.0) -> dict:
    v = voice(name)
    from piper import SynthesisConfig
    cfg = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed)))
    chunks = list(v.synthesize(clean_for_speech(text) or "hm", syn_config=cfg))
    rate = chunks[0].sample_rate
    pcm = np.concatenate([c.audio_int16_array for c in chunks]).astype(np.int16)
    return {"ogg": to_ogg_opus(pcm, rate), "seconds": round(len(pcm) / rate, 2), "waveform": waveform(pcm)}


def to_ogg_opus(pcm: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        st = out.add_stream("libopus", rate=48000, layout="mono")
        st.bit_rate = 48000
        rs = av.AudioResampler(format="s16", layout="mono", rate=48000)
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.time_base = rate, Fraction(1, rate)
        for f in rs.resample(frame) + rs.resample(None):
            for p in st.encode(f):
                out.mux(p)
        for p in st.encode(None):
            out.mux(p)
    return buf.getvalue()


def waveform(pcm: np.ndarray, points: int = 256) -> str:
    a = np.abs(pcm.astype(np.float32))
    parts = np.array_split(a, points) if len(a) >= points else [a] * points
    peaks = np.array([p.max() if len(p) else 0 for p in parts])
    peaks = peaks / (peaks.max() or 1) * 255
    return base64.b64encode(peaks.astype(np.uint8).tobytes()).decode()
