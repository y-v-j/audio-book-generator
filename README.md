# audio-book-generator

Two tools in one, and you choose what you want:

1. **PDF → EPUB:** turn a PDF, text-based or a scanned book, into a proper EPUB with chapters, a table of contents and a cover. Scanned pages are read with OCR and cleaned up: running headers, page numbers and OCR noise removed, split words rejoined, paragraphs rebuilt.
2. **EPUB (or PDF) → audiobook:** a narration that sounds read, not recited, as a single `.m4b` with chapters and cover art, or one MP3 per chapter.

Everything runs on your own computer with free voices: no account, no internet once the voices are downloaded.

[![tests](https://github.com/y-v-j/audio-book-generator/actions/workflows/tests.yml/badge.svg)](https://github.com/y-v-j/audio-book-generator/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![OCR](https://img.shields.io/badge/OCR-Tesseract%205-4b8bbe?style=flat-square)
![Voices](https://img.shields.io/badge/Voices-Kokoro%2082M%20(Apache--2.0)-f59e0b?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Linux-191926?style=flat-square&logo=linux&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-c4b5fd?style=flat-square)

## Features

### PDF to EPUB

- **Text and scanned PDFs:** pages with text are read directly; pages that are only pictures are read with [Tesseract](https://github.com/tesseract-ocr/tesseract) OCR. You can force either (`--ocr always|never`).
- **Cleaned up like a typesetter would:** running headers and footers, page numbers, and OCR noise from pictures and page edges are removed; words split at line ends are rejoined (real hyphens like "well-known" are kept); lines become paragraphs again, also across page breaks; large decorative first letters ("S" + "ince") are put back; "* * *" or "§" become scene breaks.
- **Real chapters:** from the PDF's bookmarks when it has them, otherwise from headings such as "CHAPTER III" or "Prologue". Contents pages and title pages are left out of the audiobook by default.
- **Valid EPUB 3:** passes the W3C's EPUBCheck with no errors or warnings; includes an EPUB 2 table of contents for older readers and the first page as the cover.
- **Light on memory:** one page at a time, several in parallel, and every page's OCR is cached, so running a second command on the same PDF is instant.

### Audiobooks

- **Male and female voices:** 28 English voices (American and British) from the open [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) model, with friendly names (`female`, `male`, `female-uk`, `male-uk`). Blend two voices into one of your own: `--voice am_michael:60,am_fenrir:40`.
- **Hear before you commit:** `abgen preview` reads a short passage with dialogue in any voice, at any speed.
- **Read the way a narrator would:** abbreviations, numbers, dates, money, Roman numerals and headings are rewritten for speech before the voice sees them (details below).
- **Natural pacing:** pauses grow with the break (sentence, paragraph, scene, chapter), quick dialogue moves faster, headings are read a little slower, and no two pauses are identical.
- **Follows the book's structure:** chapters come from the table of contents, chapters that span several files or share one file are handled, and a story told in parts ("I.", "II.", "III.") stays one chapter. Contents pages, copyright pages and the Project Gutenberg licence are skipped unless you ask for them.
- **Skips what isn't read aloud:** footnote markers, page numbers, images, tables and links.
- **Proper audiobook files:** `.m4b` with named chapters, the book's cover, title and author tags, and even loudness across the whole book (-18 LUFS, peaks limited to -3 dBFS).
- **Resumable:** every finished sentence is saved as it's made. Stop with Ctrl+C and run the same command again to carry on.
- **Fixable pronunciation:** a simple lexicon file (`Hermione = Her-my-oh-nee`) for names the voice gets wrong.

## How it makes synthetic speech sound human

A speech model reads exactly what it's given. Most of the "robot" in robot narration comes from what happens around the model, so this project works on that:

| Problem | What a naive converter does | What this does |
|---|---|---|
| `Mr. Holmes` | "Mister." (full stop: a sentence-ending pause mid-sentence) | "Mister Holmes" |
| `in 1881` | "one thousand eight hundred eighty-one" | "eighteen eighty-one" |
| `CHAPTER IV.` | "C-H-A-P-T-E-R I-V" or "Chapter I V" | "Chapter Four." |
| `£5 10s 6d`, `4½ per cent`, `the 1890s` | digits and symbols | "five pounds, ten shillings and six pence", "four and a half per cent", "the eighteen nineties" |
| Henry VIII, World War II | "Henry V I I I" | "Henry the eighth", "World War two" |
| Pauses | the same gap everywhere, like a metronome | 0.30–0.46 s between sentences, 0.62–0.92 s between paragraphs, 0.42–0.62 s in quick dialogue, about 2 s at a scene break, varied within each range |
| Pace | one fixed speed | slightly slower than the model's default (0.94), headings slower still, small drift between paragraphs, dialogue a shade quicker |
| Phrasing | one sentence at a time (the model sounds weak on short phrases) or whole paragraphs (it rushes long input) | sentences grouped into pieces of 100–330 characters, the length the model reads best; long sentences split at a clause |
| Silence | dead digital silence between sentences | faint room tone (-72 dBFS) underneath, like a quiet recording booth |
| Joins | clicks where pieces meet | 8 ms fades at every join |

All variation is seeded from the text itself, so running the same book twice gives the same audio.

## Voices

The grades are the model author's estimate of the quality and amount of training audio behind each voice. Voices sound different to different people, so listen to a few with `abgen preview`.

| Voice | | Accent | Grade |
|---|---|---|---|
| `af_heart` (**female**, default) | female | American | A |
| `af_bella` | female | American | A- |
| `af_nicole` | female | American | B- |
| `bf_emma` (**female-uk**) | female | British | B- |
| `am_michael` (**male**) | male | American | C+ |
| `am_fenrir`, `am_puck` | male | American | C+ |
| `bm_george` (**male-uk**), `bm_fable` | male | British | C |

`abgen voices` lists all 28. The male voices are graded lower than the best female ones; a blend such as `am_michael:60,am_fenrir:40` often sounds smoother than either alone.

## Requirements

- Linux (tested on Fedora 44), 8 GB RAM or more (each synthesis worker uses about 0.8 GB)
- Python 3.10–3.13 (`kokoro-onnx` doesn't support Python 3.14 yet). The install script makes a [conda](https://github.com/conda-forge/miniforge) environment with Python 3.12 for you.
- `ffmpeg` (for the `.m4b` / `.mp3` files)
- For PDFs: `poppler-utils` (`pdftotext`, `pdftoppm`) and, for scanned PDFs, `tesseract` with the language data you need (`tesseract --list-langs`)
- About 350 MB for the voice model, plus temporary space while converting (about 170 MB per hour of audio)

No GPU is needed.

## Installation

1. Install the system tools:

   ```bash
   sudo apt install ffmpeg poppler-utils tesseract-ocr                              # Debian, Ubuntu, Mint
   sudo dnf install ffmpeg-free poppler-utils tesseract tesseract-langpack-eng      # Fedora
   sudo pacman -S ffmpeg poppler tesseract tesseract-data-eng                       # Arch
   ```

   Add Tesseract language packs for books in other languages (e.g. `tesseract-ocr-fra` / `tesseract-langpack-fra`).

2. Get the code and run the installer:

   ```bash
   git clone https://github.com/y-v-j/audio-book-generator.git
   cd audio-book-generator
   ./install.sh
   ```

This creates the conda environment **py-ebook** (Python 3.12, conda-forge only), installs the project into it, adds an `abgen` command to `~/.local/bin`, and downloads the model files (verified by SHA-256) to `~/.local/share/audio-book-generator/models`. Options: `--env NAME`, `--no-models`, `--fast-model` (also fetch the smaller int8 model).

Check it works with `abgen voices` and `abgen preview --voice male`.

Manual installation, with conda:

```bash
conda create -n py-ebook --override-channels -c conda-forge python=3.12 pip
conda activate py-ebook
pip install -e '.[test]'
abgen models
```

or with a plain virtual environment (Python 3.10–3.13):

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
abgen models
```

### Where files go

| What | Where |
|---|---|
| Voice model (about 350 MB) | `~/.local/share/audio-book-generator/models` (or `$ABGEN_MODEL_DIR`) |
| Work in progress and OCR results | `~/.cache/audio-book-generator` (finished books clean up after themselves; `--keep-cache` keeps them) |
| Your pronunciation fixes | `~/.config/audio-book-generator/lexicon.txt` |
| EPUBs and audiobooks | next to the input file, or wherever `-o` says |

### Uninstall

```bash
conda env remove -n py-ebook
rm ~/.local/bin/abgen
rm -r ~/.local/share/audio-book-generator ~/.cache/audio-book-generator ~/.config/audio-book-generator
```

## Usage

### PDF to EPUB

```bash
abgen info scan.pdf                            # pages read, chapters found, estimated audio length
abgen epub scan.pdf                            # -> scan.epub next to the PDF
abgen epub scan.pdf -o "My Book.epub" --title "My Book" --author "A. Writer"
abgen epub scan.pdf --ocr always --ocr-lang eng+fra --dpi 300
```

The first command on a scanned PDF runs the OCR (about 1.5 s per page on a 4-core laptop) and caches it; later commands on the same file are instant.

| PDF option | Meaning |
|---|---|
| `--ocr auto\|always\|never` | `auto` (default): OCR only pages without text; `always`: OCR every page (for PDFs whose text layer is poor); `never`: text only |
| `--ocr-lang` | Tesseract language(s), e.g. `eng`, `fra`, `eng+deu` (default `eng`) |
| `--dpi` | resolution for OCR (default 300; 200 is faster on clean scans) |
| `--ocr-workers` | pages read in parallel (default: number of CPU threads) |
| `--title`, `--author` | for PDFs that don't say what they are (also work for EPUBs) |

These options work with `info`, `epub`, `text` and `convert`.

### Audiobooks

```bash
abgen voices                                   # list voices
abgen preview --voice male                     # hear a voice (saves and plays a short WAV)
abgen preview --voice bf_emma --speed 0.9 --text "It was the best of times."
abgen info book.epub                           # chapters, word counts, estimated length
abgen text book.epub -c 3                      # exactly what will be spoken, with every pause
abgen convert book.epub                        # -> book.m4b next to the EPUB
abgen convert book.epub --voice male-uk -o ~/Audiobooks/book.m4b
abgen convert book.epub -c 1-3                 # only chapters 1 to 3 (numbers from 'abgen info')
abgen convert book.epub -f mp3                 # one MP3 per chapter: "book (audiobook)/001 - Title.mp3"
abgen convert scan.pdf --save-epub             # straight from a PDF, keeping the EPUB too
```

Options for `preview`, `text` and `convert`:

| Option | Meaning |
|---|---|
| `-v, --voice` | voice name, alias, or blend `a:60,b:40` (default `female`) |
| `-s, --speed` | reading speed, 0.5–2.0; 1.0 is the model's own pace (default 0.94) |
| `--plain` | no variation in pauses and pace |
| `--fast` | the smaller int8 model: about 15% faster, slightly lower quality |
| `--lexicon FILE` | pronunciation fixes (default `~/.config/audio-book-generator/lexicon.txt` if it exists) |
| `--no-intro` | don't announce the title and author at the start and "End of …" at the end |
| `--room-tone DB` | background level in dBFS, or `off` (default -72) |

Only for `convert`: `-c/--chapters` (e.g. `1-5,8,10-`), `-f/--format m4b|mp3`, `-o/--output`, `-w/--workers N`, `--bitrate` (default 64k AAC, plenty for speech), `--keep-cache`, `--save-epub [FILE]` (when the input is a PDF).

### How long it takes

Synthesis runs on the CPU. On a 4-core laptop processor with four workers it takes about 0.6 seconds of computing per second of speech, so a 10-hour book takes roughly 6 hours. Long books are best left running overnight. If you stop the conversion, run the same command again and it continues from where it stopped.

### Pronunciation fixes

Copy `lexicon.example.txt` to `~/.config/audio-book-generator/lexicon.txt` and add lines like:

```
Hermione = Her-my-oh-nee
221B = two twenty-one B
```

Spell the word the way it sounds. Check it with `abgen preview --text "Hermione smiled."`.

## Troubleshooting

- **A scanned PDF reads badly:** OCR can only be as good as the scan. Try `--dpi 400` for small print, or `--ocr always` if the PDF's own text layer is poor. Check the result with `abgen text scan.pdf -c 3` or open the EPUB.
- **A PDF has the wrong title or no author:** pass `--title` and `--author`.
- **A chapter is missing or split oddly:** run `abgen info book.epub`. Chapters marked *skipped* (contents, copyright, licence) can be included with `-c`. The chapter list comes from the book's own table of contents.
- **A word is mispronounced:** add it to the lexicon (above).
- **Too slow or too fast:** change `--speed` in steps of 0.03–0.05.
- **Out of memory:** use fewer workers, e.g. `-w 2`.
- **"ffmpeg was not found":** install ffmpeg; synthesis results are kept, so just run the command again afterwards.

## Project layout

```
abgen/
  cli.py        command line (voices, preview, info, epub, text, convert, models)
  epub.py       reads the EPUB: table of contents, chapters, paragraphs, headings, scene breaks
  pdf.py        reads a PDF: text layer or OCR, clean-up, paragraphs, headings, chapters
  epubwrite.py  writes a valid EPUB 3 (with EPUB 2 table of contents)
  textnorm.py   rewrites text for speech (abbreviations, numbers, money, Roman numerals, lexicon)
  segment.py    sentences and model-sized pieces
  prosody.py    pauses and pace
  voices.py     voice list, grades, aliases and blends
  engine.py     Kokoro synthesis in parallel worker processes
  pipeline.py   plan -> synthesize (resumable) -> assemble chapters -> encode
  audio.py      fades, room tone, loudness, M4B/MP3 encoding with ffmpeg
  models.py     model download and SHA-256 verification
tests/          unit tests, plus slow tests that run OCR and the speech model (pytest -m slow)
install.sh      environment, command and model set-up
```

Run the tests with `pytest -m "not slow"` (fast, no model needed) or `pytest` (also runs OCR and a real two-chapter conversion; needs the model files, `ffmpeg`, `tesseract` and ImageMagick).

## Use responsibly

Convert books you own or that are free to use, such as public-domain titles from [Project Gutenberg](https://www.gutenberg.org/) or [Standard Ebooks](https://standardebooks.org/). EPUBs and audiobooks you make from copyrighted books are for your own use; sharing them may break copyright law where you live. The tool doesn't remove DRM and won't open DRM-protected EPUBs.

## Acknowledgements

This project stands on the work of others:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad: the speech model and all the voices (Apache-2.0)
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) by thewh1teagle: runs Kokoro with ONNX Runtime and hosts the model files this project downloads (MIT)
- [espeak-ng](https://github.com/espeak-ng/espeak-ng) (GPL-3.0) and [phonemizer](https://github.com/bootphon/phonemizer) (GPL-3.0+): turn words into sounds; installed by pip with kokoro-onnx
- [ONNX Runtime](https://github.com/microsoft/onnxruntime) (MIT) and [NumPy](https://numpy.org/) (BSD-3-Clause)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache-2.0) and [Poppler](https://poppler.freedesktop.org/) (GPL-2.0/3.0): reading PDFs and scans
- [pypdf](https://github.com/py-pdf/pypdf) (BSD-3-Clause): PDF bookmarks and metadata
- [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (MIT) and [lxml](https://lxml.de/) (BSD-3-Clause): reading EPUBs
- [num2words](https://github.com/savoirfairelinux/num2words) (LGPL-2.1): numbers into words
- [FFmpeg](https://ffmpeg.org/) (LGPL-2.1+/GPL): loudness, AAC/MP3 encoding and M4B chapters
- [EPUBCheck](https://github.com/w3c/epubcheck) by the W3C (BSD-3-Clause): used to validate the EPUBs this project writes
- [Project Gutenberg](https://www.gutenberg.org/): public-domain books used for testing

None of these are included in this repository. Python packages are installed by pip and the voice model is downloaded on first use; each keeps its own licence. espeak-ng and phonemizer are GPL-licensed, so a bundle that ships them together with this code (for example a single executable) must follow the GPL.

## License

[MIT](LICENSE) © 2026 Yogesh
