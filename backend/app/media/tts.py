"""Local text-to-speech with eSpeak NG (GPL-3.0, free, runs in-process, no network, no key, no cost).
Robotic voice by design: it is a free baseline so every project can get narration; it is not a neural voice."""
from __future__ import annotations
import ctypes
import threading
import wave
from pathlib import Path

_lock = threading.Lock()
_lib = None
_rate = 22050
_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)


class TTSUnavailable(Exception):
    pass


def _load():
    global _lib, _rate
    if _lib:
        return _lib
    try:
        import espeakng_loader
        lib = ctypes.CDLL(espeakng_loader.get_library_path())
        lib.espeak_Initialize.restype = ctypes.c_int
        rate = lib.espeak_Initialize(1, 0, espeakng_loader.get_data_path().encode(), 0)   # 1 = retrieval (no audio device)
        if rate <= 0:
            raise TTSUnavailable("espeak init failed")
        _rate, _lib = rate, lib
        return lib
    except TTSUnavailable:
        raise
    except Exception as e:
        raise TTSUnavailable(f"eSpeak NG is not available: {type(e).__name__}") from e


def available() -> bool:
    try:
        _load()
        return True
    except TTSUnavailable:
        return False


def synthesize(text: str, dest: Path, voice: str = "en-us", wpm: int = 150) -> float:
    """Write a mono 16-bit WAV and return its duration in seconds."""
    text = (text or "").strip()[:1000]
    if not text:
        raise ValueError("empty text")
    with _lock:
        lib = _load()
        lib.espeak_SetVoiceByName(voice.encode())
        lib.espeak_SetParameter(1, int(wpm), 0)       # espeakRATE
        pcm = bytearray()

        def cb(wav, n, ev):
            if wav and n > 0:
                pcm.extend(ctypes.string_at(wav, n * 2))
            return 0
        keep = _CB(cb)
        lib.espeak_SetSynthCallback(keep)
        b = text.encode()
        lib.espeak_Synth(b, len(b) + 1, 0, 0, 0, 1, None, None)   # espeakCHARS_UTF8 = 1
        lib.espeak_Synchronize()
        lib.espeak_SetSynthCallback(None)
    if not pcm:
        raise TTSUnavailable("no audio produced")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(_rate); w.writeframes(bytes(pcm))
    return len(pcm) / 2 / _rate
