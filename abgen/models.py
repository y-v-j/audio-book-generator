"""Locate, download and verify the Kokoro model files."""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"

# name -> (size in bytes, sha256). The sizes are the ones GitHub lists for the release
# assets; the hashes were recorded from downloads that matched those sizes.
FILES = {
    "kokoro-v1.0.onnx": (325532387, "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
    "kokoro-v1.0.int8.onnx": (92361271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "voices-v1.0.bin": (28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
MODELS = {"quality": "kokoro-v1.0.onnx", "fast": "kokoro-v1.0.int8.onnx"}
VOICES = "voices-v1.0.bin"


def model_dir() -> Path:
    if os.environ.get("ABGEN_MODEL_DIR"):
        return Path(os.environ["ABGEN_MODEL_DIR"]).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "audio-book-generator" / "models"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure(name: str, quiet: bool = False) -> Path:
    """Path to a model file, downloading and verifying it on first use."""
    size, digest = FILES[name]
    path = model_dir() / name
    if path.exists() and path.stat().st_size == size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    if not quiet:
        print(f"Downloading {name} ({size / 1e6:.0f} MB) ...", file=sys.stderr)
    with urllib.request.urlopen(BASE_URL + name, timeout=60) as response, open(part, "wb") as out:
        done = 0
        while block := response.read(1 << 20):
            out.write(block)
            done += len(block)
            if not quiet:
                print(f"\r  {done * 100 // size:3d}%", end="", file=sys.stderr, flush=True)
    if not quiet:
        print(file=sys.stderr)
    if part.stat().st_size != size or sha256(part) != digest:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: download failed verification (size or SHA-256 mismatch)")
    part.replace(path)
    return path


def verify(name: str) -> bool:
    size, digest = FILES[name]
    path = model_dir() / name
    return path.exists() and path.stat().st_size == size and sha256(path) == digest
