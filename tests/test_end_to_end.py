"""Runs the real model on a two-chapter book. Needs the model files (abgen models)."""

import json
import shutil
import subprocess

import pytest

from abgen import models
from abgen.cli import main

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _need_models():
    if not (models.model_dir() / models.MODELS["quality"]).exists():
        pytest.skip("model files not downloaded")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")


def test_convert_two_chapters_to_m4b(epub_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = epub_factory(
        {"c1.xhtml": "<h2>Chapter 1</h2><p>It was a fine morning in 1887.</p>",
         "c2.xhtml": "<h2>Chapter 2</h2><p>\"Are you ready?\" asked Dr. Watson.</p>"},
        [("c1.xhtml", "Chapter 1"), ("c2.xhtml", "Chapter 2")])
    out = tmp_path / "book.m4b"
    assert main(["convert", str(path), "-o", str(out), "--workers", "2"]) == 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(out)],
                           capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Chapter 1", "Chapter 2"]
    assert float(info["format"]["duration"]) > 5
    assert info["format"]["tags"]["title"] == "The Test Book"
    assert not (tmp_path / "cache" / "audio-book-generator").exists() or \
        not any((tmp_path / "cache" / "audio-book-generator").iterdir())  # work folder removed
