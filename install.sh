#!/usr/bin/env bash
# Set up audio-book-generator for the current user:
#   1. a conda environment (default name: py-ebook) with Python 3.12, from conda-forge only
#   2. this project installed into it (editable, so code changes apply at once)
#   3. an 'abgen' command in ~/.local/bin
#   4. the Kokoro model files (about 350 MB) in ~/.local/share/audio-book-generator/models
#
# Usage: ./install.sh [--env NAME] [--no-models] [--fast-model]
set -euo pipefail

ENV_NAME=py-ebook
MODELS=1
FAST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift ;;
        --no-models) MODELS=0 ;;
        --fast-model) FAST="--fast" ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"

CONDA="${CONDA_EXE:-}"
for candidate in "$CONDA" "$(command -v conda || true)" "$HOME/miniforge3/bin/conda" "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then CONDA="$candidate"; break; fi
done
[ -x "${CONDA:-}" ] || { echo "conda not found; install Miniforge first: https://github.com/conda-forge/miniforge" >&2; exit 1; }

ENV_DIR="$("$CONDA" env list | awk -v n="$ENV_NAME" '$1 == n {print $NF}')"
if [ -z "$ENV_DIR" ]; then
    echo "Creating conda environment '$ENV_NAME' (Python 3.12, conda-forge) ..."
    "$CONDA" create -y -q -n "$ENV_NAME" --override-channels -c conda-forge python=3.12 pip
    ENV_DIR="$("$CONDA" env list | awk -v n="$ENV_NAME" '$1 == n {print $NF}')"
fi
PY="$ENV_DIR/bin/python"
"$PY" -c 'import sys; assert (3, 10) <= sys.version_info[:2] < (3, 14), "need Python 3.10-3.13"' \
    || { echo "environment '$ENV_NAME' has an unsupported Python (kokoro-onnx needs 3.10-3.13)" >&2; exit 1; }

echo "Installing audio-book-generator into '$ENV_NAME' ..."
"$PY" -m pip install -q --no-input -e "$HERE[test]"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/abgen" <<EOF
#!/bin/sh
exec "$ENV_DIR/bin/abgen" "\$@"
EOF
chmod +x "$HOME/.local/bin/abgen"
echo "Command installed: ~/.local/bin/abgen"

command -v ffmpeg >/dev/null || echo "Note: ffmpeg is needed to write the audiobook file; please install it."
command -v pdftotext >/dev/null || echo "Note: poppler-utils (pdftotext, pdftoppm) is needed for PDFs; please install it."
command -v tesseract >/dev/null || echo "Note: tesseract (with English data) is needed for scanned PDFs; please install it."

if [ "$MODELS" = 1 ]; then
    "$HOME/.local/bin/abgen" models $FAST
fi
echo "Done. Try:  abgen voices   then   abgen preview --voice female"
