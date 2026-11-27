"""Audio synthesis/alignment and composition/export (sections 17, 21)."""

from __future__ import annotations

import asyncio

import pytest

from app.audio.alignment import AudioTimeline, NarrationCue
from app.audio.tts import LocalNarrationSynthesizer, wav_duration_seconds
from app.media.composer import Composer, CompositionClip
from app.services.export import ExportService


# --- TTS -------------------------------------------------------------------

def test_local_synthesizer_writes_real_wav(tmp_path):
    synth = LocalNarrationSynthesizer(sample_rate=8000)
    out = tmp_path / "n.wav"
    result = asyncio.run(
        synth.synthesize("hello world", out_path=out, duration_seconds=2.5)
    )
    assert out.exists()
    assert result.is_synthetic is True
    assert result.engine == "local-silent"
    # The file is a real, valid WAV of the exact requested duration.
    assert wav_duration_seconds(out) == pytest.approx(2.5, abs=1e-3)


def test_tts_estimate_is_deterministic():
    synth = LocalNarrationSynthesizer()
    assert synth.estimate_speech_seconds("one two three") == synth.estimate_speech_seconds(
        "one two three"
    )


# --- alignment -------------------------------------------------------------

def test_align_assigns_cue_to_correct_scene():
    timeline = AudioTimeline(total_duration_seconds=8.0)
    timeline.add_cue(NarrationCue("a", 0.0, 4.0))
    timeline.add_cue(NarrationCue("b", 4.0, 8.0))
    scenes = [("s0", 0.0, 4.0), ("s1", 4.0, 4.0)]
    cues = timeline.align_to_scenes(scenes)
    assert cues[0].scene_id == "s0"
    assert cues[1].scene_id == "s1"
    assert timeline.aligned_within(scenes) is True


def test_aligned_within_detects_overflow():
    timeline = AudioTimeline()
    timeline.add_cue(NarrationCue("a", 0.0, 10.0, "s0"))
    scenes = [("s0", 0.0, 4.0)]
    assert timeline.aligned_within(scenes) is False


# --- composer --------------------------------------------------------------

def test_composer_degrades_to_manifest_without_ffmpeg(tmp_path):
    composer = Composer(storage_root=tmp_path)
    composer.ffmpeg_bin = "definitely-not-installed-binary"
    result = composer.compose(
        "p1",
        [CompositionClip("s0", "mock://a", 4.0)],
        resolution="1080x1920",
        fps=30,
        aspect_ratio="9:16",
    )
    assert result.ffmpeg_used is False
    assert result.output_uri == ""
    assert result.manifest["ffmpeg_available"] is False
    assert (tmp_path / "p1" / "manifest.json").exists()


def test_composer_command_includes_audio_and_grade(tmp_path):
    composer = Composer(storage_root=tmp_path)
    cmd = composer.build_command(
        [CompositionClip("s0", "mock://a", 4.0)],
        output_path=tmp_path / "out.mp4",
        resolution="1080x1920",
        fps=30,
        audio_path="/tmp/n.wav",
        grade="warm",
    )
    joined = " ".join(cmd)
    assert "-map 0:v:0" in joined
    assert "-map 1:a:0" in joined
    assert "eq=gamma_r=1.05" in joined  # warm grade applied


def test_composer_unknown_grade_adds_no_filter(tmp_path):
    composer = Composer(storage_root=tmp_path)
    cmd = composer.build_command(
        [CompositionClip("s0", "mock://a", 4.0)],
        output_path=tmp_path / "out.mp4",
        resolution="1080x1920",
        fps=30,
        grade="surprise-me",
    )
    assert "eq=" not in " ".join(cmd)


# --- export service --------------------------------------------------------

def test_export_service_records_job(db, tmp_path):
    from app.models.storyboard import MediaTimeline
    from app.services.projects import ProjectService

    project = ProjectService(db).create_project(title="t")
    db.add(
        MediaTimeline(
            project_id=project.id,
            scene_id="s0",
            start_seconds=0.0,
            duration_seconds=4.0,
            fps=30,
            artifact_uri="mock://a",
        )
    )
    db.flush()
    composer = Composer(storage_root=tmp_path)
    composer.ffmpeg_bin = "definitely-not-installed-binary"
    service = ExportService(db, composer=composer)
    job = service.create_export(project.id)
    assert job.status == "MANIFEST_ONLY"
    assert service.download_path(job.id) is None
    assert len(service.list_exports(project.id)) == 1
