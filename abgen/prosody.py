"""Pauses and pace: what makes synthesized reading sound like a person rather than a metronome.

A human narrator doesn't leave identical gaps everywhere. Pauses grow with the size of
the break (sentence < paragraph < scene < chapter), quick exchanges of dialogue move
faster, headings are read a touch slower, and nothing repeats exactly. All variation is
seeded from the text itself, so a re-run produces identical audio (and reuses the cache).
"""

from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass

# Seconds of silence, as (shortest, longest); expressive mode picks a value in between.
PAUSES = {
    "sentence": (0.30, 0.46),   # between pieces of the same paragraph
    "paragraph": (0.62, 0.92),
    "dialogue": (0.42, 0.62),   # between two lines of quick dialogue
    "heading": (1.10, 1.45),    # after a chapter or section heading
    "section": (0.95, 1.20),    # before a heading in the middle of a chapter
    "scene": (1.55, 2.05),      # scene break (* * * or a rule)
    "intro": (1.40, 1.70),      # after the book's opening announcement
}
CHAPTER_LEAD = 0.6  # silence at the start of each chapter file
CHAPTER_TAIL = 1.8  # silence at the end of each chapter file

_DIALOGUE = re.compile(r"^\s*[\"“‘']")


def is_dialogue(text: str) -> bool:
    return bool(_DIALOGUE.match(text))


@dataclass
class Prosody:
    speed: float = 0.94  # Kokoro's 1.0 is ~185 words a minute; audiobooks are ~150-165
    expressive: bool = True

    def _rng(self, key: str) -> random.Random:
        return random.Random(zlib.crc32(key.encode("utf-8")))

    def pause(self, kind: str, key: str = "") -> float:
        low, high = PAUSES[kind]
        if not self.expressive:
            return round((low + high) / 2, 3)
        return round(self._rng(kind + key).uniform(low, high), 3)

    def pace(self, kind: str, text: str) -> float:
        """Speaking speed for one piece of text."""
        speed = self.speed
        if kind == "heading":
            speed *= 0.92
        elif self.expressive:
            # Small, smooth variation between paragraphs, the way a reader's
            # tempo drifts; dialogue is a shade quicker than narration.
            speed *= self._rng("pace" + text[:80]).uniform(0.975, 1.025)
            if is_dialogue(text):
                speed *= 1.02
        return round(min(2.0, max(0.5, speed)), 3)
