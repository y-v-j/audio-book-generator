"""Command line: abgen voices | preview | info | epub | text | convert | models."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, models
from .epub import read_epub, skipped_by_default
from .prosody import Prosody
from .textnorm import load_lexicon
from .voices import ALIASES, DEFAULT_VOICE, GRADES, catalogue, parse_voice

SAMPLE_TEXT = (
    'It was a cold, still evening in late November, and the fog lay thick upon the street. '
    '"Are you quite certain?" she asked, lowering her voice. '
    'He hesitated for a moment — then nodded slowly, and said nothing at all.'
)
WORDS_PER_MINUTE = 200  # Kokoro at speed 1.0, measured on a 49-minute chapter (before pauses)


def _lexicon_path(arg: str | None) -> str | None:
    if arg:
        return arg
    default = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "audio-book-generator" / "lexicon.txt"
    return str(default) if default.exists() else None


def _settings(args, voice_spec):
    from .pipeline import Settings

    room = None if str(args.room_tone).lower() == "off" else float(args.room_tone)
    return Settings(voice=voice_spec, prosody=Prosody(speed=args.speed, expressive=not args.plain),
                    model="fast" if args.fast else "quality", intro=not args.no_intro,
                    lexicon=load_lexicon(_lexicon_path(args.lexicon)), room_tone_db=room)


def parse_selection(spec: str | None, count: int, default: list[int]) -> list[int]:
    """'3-7,10,12-' -> chapter numbers (1-based)."""
    if not spec:
        return default
    numbers: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)|(\d+)", part)
        if not m:
            raise ValueError(f"bad chapter selection {part!r}; use e.g. 1-5,8,10-")
        if m.group(3):
            lo = hi = int(m.group(3))
        else:
            lo = int(m.group(1) or 1)
            hi = int(m.group(2) or count)
        for n in range(lo, hi + 1):
            if not 1 <= n <= count:
                raise ValueError(f"chapter {n} does not exist (the book has {count})")
            if n not in numbers:
                numbers.append(n)
    return numbers


def _voice(args):
    try:
        return parse_voice(args.voice)
    except ValueError as error:
        raise SystemExit(f"abgen: {error}")


# --- commands ------------------------------------------------------------------------------
def cmd_voices(args) -> int:
    names = sorted(GRADES)
    if args.all:
        import numpy as np

        names = sorted(np.load(models.ensure(models.VOICES)).files)
    print(f"{'voice':<12} {'gender':<7} {'accent':<22} grade")
    for v in catalogue(names, english_only=not args.all):
        alias = next((a for a, n in ALIASES.items() if n == v.name), "")
        print(f"{v.name:<12} {v.gender:<7} {v.accent:<22} {v.grade:<3} {'(' + alias + ')' if alias else ''}")
    print("\nGrades are the model author's estimate of each voice's training data (A best).")
    print("Blend voices for a voice of your own, e.g.  --voice am_michael:60,am_fenrir:40")
    return 0


def _player() -> list[str] | None:
    for cmd in (["pw-play"], ["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def cmd_preview(args) -> int:
    import numpy as np

    from . import audio, engine
    from .pipeline import Settings, plan_chapter
    from .epub import Block, Chapter

    voice = _voice(args)
    settings = _settings(args, voice)
    settings.intro = False
    chapter = Chapter("Preview", [Block("para", args.text or SAMPLE_TEXT)])
    plan = plan_chapter(1, chapter, settings, announce_title=False)
    tts = engine.load(models.ensure(models.MODELS[settings.model]), models.ensure(models.VOICES),
                      threads=max(1, (os.cpu_count() or 2) // 2))
    out = Path(args.out) if args.out else Path.home() / ".cache" / "audio-book-generator" / "previews" / f"{voice.label.replace(' ', '')}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for item in plan.items:
        if hasattr(item, "text"):
            parts.append(engine.speak(tts, item.text, voice.key, voice.parts, voice.lang, item.speed))
        else:
            parts.append(np.zeros(int(item.seconds * audio.SAMPLE_RATE), dtype=np.int16))
    audio.write_wav(out, np.concatenate(parts))
    print(f"Saved {out}")
    player = _player()
    if player and not args.no_play:
        subprocess.run([*player, str(out)])
    return 0


def _load(path: str, args=None):
    """Read an EPUB, or a PDF (text-based or scanned) with the OCR options in `args`."""
    if not Path(path).expanduser().exists():
        raise SystemExit(f"abgen: no such file: {path}")
    try:
        if Path(path).suffix.lower() == ".pdf":
            from .pdf import PdfReport, progress_printer, read_pdf

            report = PdfReport()
            book = read_pdf(Path(path).expanduser(), ocr=getattr(args, "ocr", "auto"),
                            lang=getattr(args, "ocr_lang", "eng"), dpi=getattr(args, "dpi", 300),
                            workers=getattr(args, "ocr_workers", None), cache_dir=_ocr_cache(path),
                            progress=progress_printer(), report=report,
                            title=getattr(args, "title", None), author=getattr(args, "author", None))
            print(f"PDF: {report.pages} pages ({report.text_pages} with text, {report.ocr_pages} read by OCR); "
                  f"{report.removed_lines} header/footer/page-number lines removed; "
                  f"chapters from {report.chapter_source}.", file=sys.stderr)
            return book
        book = read_epub(path)
        if getattr(args, "title", None):
            book.title = args.title
        if getattr(args, "author", None):
            book.authors = [a.strip() for a in re.split(r";| and | & ", args.author) if a.strip()]
        return book
    except SystemExit:
        raise
    except Exception as error:  # zipfile.BadZipFile, missing container.xml, broken PDF ...
        raise SystemExit(f"abgen: could not read {path}: {error}")


def _ocr_cache(path: str) -> Path:
    from .pdf import file_id
    from .pipeline import cache_root

    return cache_root() / "ocr" / file_id(Path(path).expanduser())


def cmd_info(args) -> int:
    book = _load(args.book, args)
    print(f"{book.title}\nby {book.author}   (language: {book.language}, cover: {'yes' if book.cover else 'no'})\n")
    total = 0
    for n, chapter in enumerate(book.chapters, 1):
        skip = skipped_by_default(chapter)
        minutes = chapter.words / (WORDS_PER_MINUTE * args.speed) * 1.08  # + pauses
        if not skip:
            total += minutes
        mark = "  skipped" if skip else ""
        print(f"{n:4d}  {chapter.title[:60]:<60} {chapter.words:>7,} words  ~{minutes:5.1f} min{mark}")
    print(f"\nAbout {total / 60:.1f} hours of audio at speed {args.speed}. "
          f"Skipped chapters can be included with --chapters.")
    return 0


def _selection(args, book) -> list[int]:
    default = [n for n, c in enumerate(book.chapters, 1) if not skipped_by_default(c)]
    try:
        numbers = parse_selection(args.chapters, len(book.chapters), default)
    except ValueError as error:
        raise SystemExit(f"abgen: {error}")
    if not numbers:
        raise SystemExit("abgen: no chapters selected")
    return numbers


def cmd_text(args) -> int:
    from .pipeline import Pause, plan_book

    book = _load(args.book, args)
    settings = _settings(args, _voice(args))
    for plan in plan_book(book, _selection(args, book), settings):
        print(f"\n=== {plan.number}. {plan.title}")
        for item in plan.items:
            if isinstance(item, Pause):
                print(f"   [pause {item.seconds:.2f}s]")
            else:
                print(f"   ({item.speed:.3f}) {item.text}")
    return 0


def cmd_epub(args) -> int:
    from .epubwrite import write_epub

    source = Path(args.book).expanduser().resolve()
    book = _load(str(source), args)
    numbers = _selection(args, book) if args.chapters else list(range(1, len(book.chapters) + 1))
    out = Path(args.output).expanduser() if args.output else source.with_suffix(".epub")
    if out.resolve() == source:
        raise SystemExit("abgen: the output would overwrite the input; choose another name with -o")
    write_epub(book, out, numbers)
    print(f"{book.title} by {book.author}: {len(numbers)} chapter(s), "
          f"{sum(book.chapters[n - 1].words for n in numbers):,} words -> {out}")
    return 0


def cmd_convert(args) -> int:
    from .pipeline import convert

    source = Path(args.book).expanduser().resolve()
    book = _load(str(source), args)
    numbers = _selection(args, book)
    settings = _settings(args, _voice(args))
    if args.save_epub is not None:
        from .epubwrite import write_epub

        epub_out = Path(args.save_epub).expanduser() if args.save_epub else source.with_suffix(".epub")
        if epub_out.resolve() == source:
            raise SystemExit("abgen: --save-epub would overwrite the input; give it another name")
        write_epub(book, epub_out, list(range(1, len(book.chapters) + 1)))
        print(f"Saved the EPUB: {epub_out}")
    if args.format == "mp3":
        out = Path(args.output).expanduser() if args.output else source.with_name(source.stem + " (audiobook)")
    else:
        out = Path(args.output).expanduser() if args.output else source.with_suffix(".m4b")
    print(f"{book.title} by {book.author}: {len(numbers)} chapter(s) -> {out}")
    try:
        outputs = convert(source, book, numbers, settings, out, fmt=args.format, workers=args.workers,
                          keep_cache=args.keep_cache, bitrate=args.bitrate)
    except KeyboardInterrupt:
        print("\nStopped. Finished speech is kept; run the same command again to resume.", file=sys.stderr)
        return 130
    print("Done:", *outputs, sep="\n  ")
    return 0


def cmd_models(args) -> int:
    names = [models.VOICES, models.MODELS["quality"]] + ([models.MODELS["fast"]] if args.fast else [])
    for name in names:
        path = models.ensure(name)
        status = "ok" if models.verify(name) else "CHECKSUM MISMATCH"
        print(f"{name:<24} {status}  {path}")
    return 0


# --- argument parsing -------------------------------------------------------------------------
def _voice_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("-v", "--voice", default=DEFAULT_VOICE,
                   help="voice name, alias (female, male, female-uk, male-uk) or blend 'a:60,b:40' "
                        f"(default: {DEFAULT_VOICE} = {ALIASES[DEFAULT_VOICE]})")
    p.add_argument("-s", "--speed", type=float, default=0.94,
                   help="reading speed; 1.0 is the model's natural pace (default 0.94)")
    p.add_argument("--plain", action="store_true", help="identical pauses and pace everywhere (no variation)")
    p.add_argument("--fast", action="store_true", help="use the smaller int8 model (a little faster, slightly lower quality)")
    p.add_argument("--lexicon", help="pronunciation file of 'word = how to say it' lines "
                                     "(default: ~/.config/audio-book-generator/lexicon.txt if present)")
    p.add_argument("--no-intro", action="store_true", help="don't announce the title and author at the start and end")
    p.add_argument("--room-tone", default="-72", metavar="DB",
                   help="faint background noise level in dBFS, or 'off' (default -72)")


def _book_argument(p: argparse.ArgumentParser) -> None:
    p.add_argument("book", help="an EPUB, or a PDF (text-based or scanned)")
    g = p.add_argument_group("PDF options")
    g.add_argument("--ocr", choices=("auto", "always", "never"), default="auto",
                   help="auto: OCR only pages without text (default); always: OCR every page; never: text only")
    g.add_argument("--ocr-lang", default="eng",
                   help="Tesseract language(s), e.g. eng, fra, eng+deu (default eng; see 'tesseract --list-langs')")
    g.add_argument("--dpi", type=int, default=300, help="resolution for OCR (default 300; 200 is faster)")
    g.add_argument("--ocr-workers", type=int, help="pages recognised in parallel (default: number of CPU threads)")
    g.add_argument("--title", help="the book's title, if the file doesn't say (or says it wrongly)")
    g.add_argument("--author", help="the author(s), e.g. 'Jane Austen' or 'A and B'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abgen",
        description="Turn a PDF (text or scanned) into a proper EPUB, and an EPUB or PDF into a naturally "
                    "narrated audiobook.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("voices", help="list the available voices")
    p.add_argument("--all", action="store_true", help="include non-English voices")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("preview", help="hear a voice read a short passage")
    _voice_options(p)
    p.add_argument("--text", help="what to read (default: a short passage with dialogue)")
    p.add_argument("-o", "--out", help="where to save the WAV")
    p.add_argument("--no-play", action="store_true", help="only save the file")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("info", help="list a book's chapters")
    _book_argument(p)
    p.add_argument("-s", "--speed", type=float, default=0.94)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("epub", help="turn a PDF (text or scanned) into a proper EPUB")
    _book_argument(p)
    p.add_argument("-o", "--output", help="the EPUB to write (default: next to the PDF)")
    p.add_argument("-c", "--chapters", help="only these chapters, e.g. 1-5,8 (default: all)")
    p.set_defaults(func=cmd_epub)

    p = sub.add_parser("text", help="show exactly what will be spoken, with pauses")
    _book_argument(p)
    p.add_argument("-c", "--chapters", help="chapters to show, e.g. 1-3,7 (numbers from 'abgen info')")
    _voice_options(p)
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("convert", help="make the audiobook (from an EPUB or a PDF)")
    _book_argument(p)
    p.add_argument("-o", "--output", help="output .m4b file, or folder for --format mp3 (default: next to the book)")
    p.add_argument("-c", "--chapters", help="chapters to include, e.g. 1-5,8,10- (default: all but contents/licence pages)")
    p.add_argument("-f", "--format", choices=("m4b", "mp3"), default="m4b",
                   help="one M4B audiobook with chapters (default), or one MP3 per chapter")
    p.add_argument("--save-epub", nargs="?", const="", metavar="EPUB",
                   help="when reading a PDF, also save it as an EPUB (default name: next to the PDF)")
    p.add_argument("-w", "--workers", type=int, help="parallel synthesis processes (default: one per CPU core, memory permitting)")
    p.add_argument("--bitrate", default="64k", help="AAC bitrate for M4B (default 64k, plenty for speech)")
    p.add_argument("--keep-cache", action="store_true", help="keep the synthesized pieces after encoding")
    _voice_options(p)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("models", help="download and verify the model files")
    p.add_argument("--fast", action="store_true", help="also fetch the smaller int8 model")
    p.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "speed") and not 0.5 <= args.speed <= 2.0:
        raise SystemExit("abgen: --speed must be between 0.5 and 2.0")
    if getattr(args, "voice", None):
        _voice(args)  # fail early on a bad voice name
    return args.func(args)
