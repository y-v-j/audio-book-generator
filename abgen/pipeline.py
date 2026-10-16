"""From a parsed book to a finished audiobook: plan, synthesize (resumable), assemble, encode."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from num2words import num2words

from . import __version__, audio, engine, models
from .epub import BARE_TITLE
from .epub import Block, Book, Chapter
from .prosody import CHAPTER_LEAD, CHAPTER_TAIL, Prosody, is_dialogue
from .segment import chunk
from .textnorm import normalize, roman_to_int
from .voices import VoiceSpec


@dataclass
class Speech:
    text: str
    speed: float
    key: str = ""


@dataclass
class Pause:
    seconds: float


@dataclass
class ChapterPlan:
    number: int  # position in the book (1-based), as shown by 'abgen info'
    title: str
    items: list = field(default_factory=list)

    @property
    def speech(self) -> list[Speech]:
        return [i for i in self.items if isinstance(i, Speech)]

    def add_pause(self, seconds: float) -> None:
        # Two breaks in a row (paragraph end, then a scene break) keep only the longer.
        if self.items and isinstance(self.items[-1], Pause):
            self.items[-1].seconds = max(self.items[-1].seconds, seconds)
        else:
            self.items.append(Pause(seconds))

    def say(self, text: str, speed: float) -> None:
        if text:
            self.items.append(Speech(text, speed))


@dataclass
class Settings:
    voice: VoiceSpec
    prosody: Prosody
    model: str = "quality"  # or "fast"
    intro: bool = True
    lexicon: dict[str, str] = field(default_factory=dict)
    room_tone_db: float | None = -72.0


def spoken_heading(text: str, first: bool, lexicon: dict[str, str]) -> str:
    """A heading as read aloud. A bare numeral ('IV.') opening a chapter is read as
    'Chapter Four.'; inside a chapter (a story in parts) it is 'Part Two.'."""
    m = BARE_TITLE.match(text)
    if m:
        value = m.group(1)
        n = int(value) if value.isdigit() else roman_to_int(value.upper())
        if n:
            return f"{'Chapter' if first else 'Part'} {num2words(n)}."
    return normalize(text, heading=True, lexicon=lexicon)


def _headline(blocks: list[Block]) -> tuple[list[str], int]:
    """Consecutive headings at the start of a chapter, read as one title."""
    titles = []
    for block in blocks:
        if block.kind != "heading":
            break
        titles.append(block.text)
    return titles, len(titles)


def plan_chapter(number: int, chapter: Chapter, settings: Settings, book: Book | None = None,
                 first: bool = False, last: bool = False, announce_title: bool = True) -> ChapterPlan:
    p, lex = settings.prosody, settings.lexicon
    plan = ChapterPlan(number, chapter.title)
    plan.add_pause(CHAPTER_LEAD)
    if first and settings.intro and book is not None:
        intro = f"{book.title}. Written by {book.author}." if book.authors else f"{book.title}."
        plan.say(normalize(intro, heading=True, lexicon=lex), p.pace("heading", ""))
        plan.add_pause(p.pause("intro", book.title))

    titles, skip = _headline(chapter.blocks)
    if not titles and announce_title:
        titles = [chapter.title]
    for i, title in enumerate(titles):
        plan.say(spoken_heading(title, i == 0, lex), p.pace("heading", title))
        if i < len(titles) - 1:
            plan.add_pause(p.pause("sentence", title))
    if titles:
        plan.add_pause(p.pause("heading", chapter.title))

    blocks = chapter.blocks[skip:]
    for i, block in enumerate(blocks):
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if block.kind == "break":
            plan.add_pause(p.pause("scene", f"{number}:{i}"))
        elif block.kind == "heading":
            plan.add_pause(p.pause("section", block.text))
            plan.say(spoken_heading(block.text, False, lex), p.pace("heading", block.text))
            plan.add_pause(p.pause("heading", block.text))
        else:
            text = normalize(block.text, lexicon=lex)
            speed = p.pace("para", text)  # one tempo for the whole paragraph
            pieces = chunk(text)
            for j, piece in enumerate(pieces):
                plan.say(piece, speed)
                if j < len(pieces) - 1:
                    plan.add_pause(p.pause("sentence", piece))
            quick = is_dialogue(block.text) and nxt is not None and nxt.kind == "para" and is_dialogue(nxt.text)
            plan.add_pause(p.pause("dialogue" if quick else "paragraph", text[:60]))

    if last and settings.intro and book is not None:
        plan.add_pause(p.pause("scene", "end"))
        plan.say(normalize(f"End of {book.title}.", heading=True, lexicon=lex), p.pace("heading", "end"))
    plan.add_pause(CHAPTER_TAIL)
    return plan


def plan_book(book: Book, numbers: list[int], settings: Settings) -> list[ChapterPlan]:
    plans = [plan_chapter(n, book.chapters[n - 1], settings, book, first=(n == numbers[0]),
                          last=(n == numbers[-1]), announce_title=book.chapters[n - 1].announce)
             for n in numbers]
    model = models.MODELS[settings.model]
    for plan in plans:
        for item in plan.speech:
            ident = f"{model}|{settings.voice.key}|{settings.voice.lang}|{item.speed:.3f}|{item.text}"
            item.key = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:24]
    return plans


# --- work folder ---------------------------------------------------------------------------
def cache_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "audio-book-generator"


def book_id(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Progress:
    def __init__(self, total_chars: int, done_chars: int, stream=sys.stderr):
        self.total, self.done, self.start_done = total_chars, done_chars, done_chars
        self.audio_seconds = 0.0
        self.started = time.time()
        self.stream = stream
        self.tty = stream.isatty()
        self.last = 0.0

    def update(self, chars: int, samples: int, chapter: str) -> None:
        self.done += chars
        self.audio_seconds += samples / audio.SAMPLE_RATE
        now = time.time()
        if not self.tty and now - self.last < 30 and self.done < self.total:
            return
        self.last = now
        rate = (self.done - self.start_done) / max(1e-6, now - self.started)
        eta = (self.total - self.done) / rate if rate > 0 else 0
        pct = 100 * self.done / max(1, self.total)
        line = f"{pct:5.1f}%  speech {_clock(self.audio_seconds)}  ETA {_clock(eta)}  {chapter[:40]}"
        print(("\r" + line.ljust(90)) if self.tty else line, end="" if self.tty else "\n",
              file=self.stream, flush=True)

    def finish(self) -> None:
        if self.tty:
            print(file=self.stream)


def synthesize(plans: list[ChapterPlan], settings: Settings, chunk_dir: Path, workers: int,
               log: Callable[[str], None] = print) -> None:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    seen, jobs, titles = set(), [], {}
    total = done = 0
    for plan in plans:
        for item in plan.speech:
            if item.key in seen:
                continue
            seen.add(item.key)
            total += len(item.text)
            out = chunk_dir / f"{item.key}.wav"
            if out.exists():
                done += len(item.text)
                continue
            titles[item.key] = plan.title
            jobs.append(engine.Job(item.key, item.text, settings.voice.key, settings.voice.parts,
                                   settings.voice.lang, item.speed, str(out)))
    if not jobs:
        log("All speech is already synthesized.")
        return
    if done:
        log(f"Resuming: {done * 100 // max(1, total)}% was done in an earlier run.")
    model_path = models.ensure(models.MODELS[settings.model])
    voices_path = models.ensure(models.VOICES)
    log(f"Synthesizing {len(jobs)} pieces with {workers} worker{'s' if workers > 1 else ''} "
        f"(voice {settings.voice.label}, speed {settings.prosody.speed}).")
    progress = Progress(total, done)
    try:
        engine.run(jobs, model_path, voices_path, workers,
                   lambda job, samples: progress.update(len(job.text), samples, titles[job.key]))
    finally:
        progress.finish()


def assemble(plans: list[ChapterPlan], settings: Settings, chunk_dir: Path,
             chapter_dir: Path) -> tuple[list[Path], list[float]]:
    chapter_dir.mkdir(parents=True, exist_ok=True)
    wavs, durations = [], []
    for plan in plans:
        path = chapter_dir / f"{plan.number:04d}.wav"
        writer = audio.ChapterWriter(path, settings.room_tone_db, seed=plan.number)
        for item in plan.items:
            if isinstance(item, Speech):
                writer.speech(audio.read_wav(chunk_dir / f"{item.key}.wav"))
            else:
                writer.silence(item.seconds)
        durations.append(writer.close())
        wavs.append(path)
    return wavs, durations


def convert(epub_path: Path, book: Book, numbers: list[int], settings: Settings, out: Path,
            fmt: str = "m4b", workers: int | None = None, keep_cache: bool = False,
            bitrate: str = "64k", log: Callable[[str], None] = print) -> list[Path]:
    plans = plan_book(book, numbers, settings)
    work = cache_root() / book_id(epub_path)
    chunk_dir, chapter_dir = work / "pieces", work / "chapters"
    synthesize(plans, settings, chunk_dir, workers or engine.default_workers(), log)

    log("Assembling chapters ...")
    wavs, durations = assemble(plans, settings, chunk_dir, chapter_dir)
    tags = {
        "title": book.title,
        "album": book.title,
        "artist": book.author,
        "album_artist": book.author,
        "genre": "Audiobook",
        "comment": f"Narrated by the Kokoro voice {settings.voice.label}; made with audio-book-generator {__version__}",
    }
    titles = [plan.title for plan in plans]
    log(f"Encoding {fmt.upper()} ({_clock(sum(durations))}) ...")
    if fmt == "mp3":
        outputs = audio.encode_mp3(wavs, titles, out, tags, book.cover, book.cover_type)
    else:
        audio.encode_m4b(wavs, titles, durations, out, tags, book.cover, book.cover_type, bitrate)
        outputs = [out]
    if keep_cache:
        log(f"Kept the work folder: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return outputs
