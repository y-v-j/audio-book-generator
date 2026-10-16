"""Assemble chapter audio and encode the finished audiobook with ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000
FADE_SECONDS = 0.008
TARGET_LUFS = -18.0  # typical for spoken-word audiobooks
PEAK_LIMIT = 0.70  # about -3 dBFS


# --- samples -------------------------------------------------------------------------------
def fade(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Short raised-cosine fades so trimmed pieces start and end without a click."""
    n = int(FADE_SECONDS * sr)
    if len(audio) <= 2 * n:
        return audio
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(audio.dtype)
    audio = audio.copy()
    audio[:n] *= ramp
    audio[-n:] *= ramp[::-1]
    return audio


def to_int16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)


def write_wav(path: Path, samples: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.astype(np.int16).tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def room_tone(n: int, level_db: float, rng: np.random.Generator) -> np.ndarray:
    """Faint pink noise at `level_db` dBFS RMS, like the background of a quiet studio."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.arange(len(spectrum), dtype=np.float64)
    freqs[0] = 1.0
    noise = np.fft.irfft(spectrum / np.sqrt(freqs), n)
    rms = np.sqrt(np.mean(noise ** 2)) or 1.0
    return (noise / rms * 10 ** (level_db / 20)).astype(np.float32)


class ChapterWriter:
    """Streams one chapter to a WAV file: speech pieces, pauses and room tone."""

    def __init__(self, path: Path, room_tone_db: float | None, seed: int = 0):
        self.path = path
        self.room_tone_db = room_tone_db
        self.rng = np.random.default_rng(seed)
        self.frames = 0
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

    def _write(self, audio: np.ndarray) -> None:
        if self.room_tone_db is not None:
            audio = audio + room_tone(len(audio), self.room_tone_db, self.rng)
        self._wav.writeframes(to_int16(audio).tobytes())
        self.frames += len(audio)

    def speech(self, samples: np.ndarray) -> None:
        self._write(samples.astype(np.float32) / 32767)

    def silence(self, seconds: float) -> None:
        self._write(np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32))

    def close(self) -> float:
        self._wav.close()
        return self.frames / SAMPLE_RATE


# --- ffmpeg --------------------------------------------------------------------------------
def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg was not found; install it to create the audiobook file")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + result.stderr[-2000:])
    return result


def aac_encoder() -> str:
    encoders = subprocess.run([ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "libfdk_aac" if re.search(r"\blibfdk_aac\b", encoders) else "aac"


def _concat_list(wavs: list[Path], folder: Path) -> Path:
    listing = folder / "concat.txt"
    listing.write_text("".join("file '{}'\n".format(str(p).replace("'", "'\\''")) for p in wavs))
    return listing


def loudness_gain(wavs: list[Path], work: Path) -> float:
    """Gain in dB that brings the whole book to TARGET_LUFS (one constant gain, no pumping)."""
    listing = _concat_list(wavs, work)
    result = _run(["-f", "concat", "-safe", "0", "-i", str(listing),
                   "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.S)
    if not match:
        return 0.0
    measured = float(json.loads(match.group(0))["input_i"])
    if measured < -70:  # silence
        return 0.0
    return round(TARGET_LUFS - measured, 2)


def _filters(gain_db: float) -> str:
    return f"volume={gain_db}dB,alimiter=limit={PEAK_LIMIT}:level=false"


def _escape_meta(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _cover_file(cover: bytes | None, cover_type: str | None, folder: Path) -> Path | None:
    if not cover or not cover_type or cover_type not in ("image/jpeg", "image/png"):
        return None
    path = folder / ("cover.jpg" if cover_type == "image/jpeg" else "cover.png")
    path.write_bytes(cover)
    return path


def encode_m4b(wavs: list[Path], titles: list[str], durations: list[float], out: Path,
               tags: dict[str, str], cover: bytes | None, cover_type: str | None,
               bitrate: str = "64k") -> None:
    """One .m4b file with chapter markers, cover art and tags."""
    with tempfile.TemporaryDirectory(prefix="abgen-") as tmp:
        work = Path(tmp)
        gain = loudness_gain(wavs, work)
        listing = _concat_list(wavs, work)
        meta = [";FFMETADATA1"] + [f"{k}={_escape_meta(v)}" for k, v in tags.items()]
        start = 0
        for title, seconds in zip(titles, durations):
            end = start + int(round(seconds * 1000))
            meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={_escape_meta(title)}"]
            start = end
        metafile = work / "metadata.txt"
        metafile.write_text("\n".join(meta) + "\n", encoding="utf-8")
        args = ["-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(metafile)]
        cover_path = _cover_file(cover, cover_type, work)
        if cover_path:
            args += ["-i", str(cover_path)]
        args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover_path:
            args += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        args += ["-af", _filters(gain), "-ar", str(SAMPLE_RATE), "-ac", "1",
                 "-c:a", aac_encoder(), "-b:a", bitrate, "-movflags", "+faststart",
                 "-f", "mp4", str(out.with_name(out.name + ".part"))]
        _run(args)
        out.with_name(out.name + ".part").replace(out)


def safe_filename(name: str, limit: int = 120) -> str:
    name = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", " ", name)
    return " ".join(name.split())[:limit].strip(" .") or "untitled"


def encode_mp3(wavs: list[Path], titles: list[str], folder: Path, tags: dict[str, str],
               cover: bytes | None, cover_type: str | None, quality: int = 5) -> list[Path]:
    """One MP3 per chapter, named '001 - Title.mp3', with matching title and track tags."""
    folder.mkdir(parents=True, exist_ok=True)
    outputs = []
    with tempfile.TemporaryDirectory(prefix="abgen-") as tmp:
        work = Path(tmp)
        gain = loudness_gain(wavs, work)
        cover_path = _cover_file(cover, cover_type, work)
        width = max(3, len(str(len(wavs))))
        for i, (wav, title) in enumerate(zip(wavs, titles), 1):
            name = f"{i:0{width}d} - {safe_filename(title)}"
            out = folder / f"{name}.mp3"
            args = ["-y", "-i", str(wav)]
            if cover_path:
                args += ["-i", str(cover_path), "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                         "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
            args += ["-af", _filters(gain), "-ar", str(SAMPLE_RATE), "-ac", "1",
                     "-c:a", "libmp3lame", "-q:a", str(quality), "-id3v2_version", "3"]
            for key, value in {**tags, "title": name, "track": f"{i}/{len(wavs)}"}.items():
                args += ["-metadata", f"{key}={value}"]
            args += [str(out)]
            _run(args)
            outputs.append(out)
    return outputs
