"""Text-to-speech adapters (README section 17).

A TTS adapter turns narration text into a single master audio track. Real TTS
engines are network/hosted services and are only wired once credentials and
official docs exist; until then the default is a **local, deterministic
synthesizer** that writes a real, valid WAV file of the correct duration. It is
clearly labelled and produces no speech — it is a timing bed, not fake audio
passed off as real narration.
"""

from __future__ import annotations

import struct
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SpeechTiming:
    """One spoken span with exact start/end times."""

    text: str
    start_seconds: float
    end_seconds: float


@dataclass
class SynthesisResult:
    audio_uri: str
    duration_seconds: float
    sample_rate: int
    timings: list[SpeechTiming]
    is_synthetic: bool = True
    engine: str = "local-silent"


class TTSAdapter(ABC):
    """Interface every narration engine must satisfy."""

    engine_id: str = "abstract"
    is_real: bool = False

    @abstractmethod
    async def synthesize(
        self, text: str, *, out_path: Path, duration_seconds: float
    ) -> SynthesisResult:
        """Render ``text`` into an audio file at ``out_path``."""

    def estimate_speech_seconds(self, text: str, *, words_per_minute: float = 150.0) -> float:
        """Estimate speech duration from word count (deterministic)."""
        words = max(1, len(text.split()))
        return round(words / words_per_minute * 60.0, 3)


class LocalNarrationSynthesizer(TTSAdapter):
    """Deterministic, offline synthesizer.

    Writes a silent mono WAV of the requested duration using only the standard
    library. This is honest scaffolding: it gives the pipeline a real audio
    artifact with exact timing so alignment and muxing can be built and tested,
    without pretending to be a speech engine.
    """

    engine_id = "local-silent"
    is_real = False

    def __init__(self, *, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate

    async def synthesize(
        self, text: str, *, out_path: Path, duration_seconds: float
    ) -> SynthesisResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames = max(1, int(round(duration_seconds * self.sample_rate)))
        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(struct.pack("<" + "h" * 1, 0) * frames)
        timings = [
            SpeechTiming(text=text, start_seconds=0.0, end_seconds=round(duration_seconds, 3))
        ]
        return SynthesisResult(
            audio_uri=str(out_path),
            duration_seconds=round(duration_seconds, 3),
            sample_rate=self.sample_rate,
            timings=timings,
            is_synthetic=True,
            engine=self.engine_id,
        )


def wav_duration_seconds(path: str | Path) -> float:
    """Read the exact duration of a WAV file (for tests/verification)."""
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def _silence_check(path: str | Path) -> bool:
    with wave.open(str(path), "rb") as wav:
        data = wav.readframes(wav.getnframes())
    return all(b == 0 for b in data)
