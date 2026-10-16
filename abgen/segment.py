"""Split paragraphs into sentences, and sentences into pieces the model reads well.

Kokoro sounds best on pieces of roughly 100-200 phonemes: very short pieces come out
weak, and long ones (over ~400 phonemes) get rushed. So short sentences of the same
paragraph are read together, and very long sentences are split at clause boundaries.
"""

from __future__ import annotations

import re

TARGET = 220  # preferred piece length in characters (about one phoneme per character)
LIMIT = 330  # never exceed this
SHORT = 45  # pieces shorter than this are bundled with a neighbour when possible

_SENTENCE_END = re.compile(r"(?<=[.!?…])([\"')\]]*)\s+(?=[\"'(\[]*[A-Z0-9])")
# Words ending in a full stop that don't end a sentence (after normalisation most
# titles are already spelled out).
_NO_SPLIT_AFTER = re.compile(r"(?:\b[A-Z]|\bCo|\bInc|\bLtd|\bBros|\bfig|\bcf)\.$")


def split_sentences(text: str) -> list[str]:
    sentences, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        end = m.start() + len(m.group(1))
        if _NO_SPLIT_AFTER.search(text[start:m.start()]):
            continue
        sentences.append(text[start:end].strip())
        start = m.end()
    sentences.append(text[start:].strip())
    return [s for s in sentences if s]


def _split_long(sentence: str, limit: int) -> list[str]:
    """Split a sentence longer than `limit` at the clause boundary nearest its middle."""
    if len(sentence) <= limit:
        return [sentence]
    middle = len(sentence) // 2
    for pattern in (r"[;:]\s", r"\s—\s", r",\s", r"\s(?=(?:and|but|or|which|who|while|when|because)\s)", r"\s"):
        cuts = [m.end() for m in re.finditer(pattern, sentence) if 20 < m.end() < len(sentence) - 20]
        if cuts:
            cut = min(cuts, key=lambda c: abs(c - middle))
            left, right = sentence[:cut].strip(), sentence[cut:].strip()
            return _split_long(left, limit) + _split_long(right, limit)
    return [sentence[:limit], *_split_long(sentence[limit:], limit)]


def chunk(text: str, target: int = TARGET, limit: int = LIMIT) -> list[str]:
    """Pieces of one paragraph, each one or more whole sentences where possible."""
    pieces: list[str] = []
    for sentence in split_sentences(text):
        pieces.extend(_split_long(sentence, limit))
    chunks: list[str] = []
    for piece in pieces:
        if chunks:
            joined = len(chunks[-1]) + 1 + len(piece)
            if joined <= target or (len(chunks[-1]) < SHORT and joined <= limit):
                chunks[-1] += " " + piece
                continue
        chunks.append(piece)
    if len(chunks) >= 2 and len(chunks[-1]) < SHORT and len(chunks[-2]) + 1 + len(chunks[-1]) <= limit:
        last = chunks.pop()
        chunks[-1] += " " + last
    return chunks
