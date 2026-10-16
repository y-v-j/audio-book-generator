"""Kokoro voice catalogue, quality grades, and voice specs (one voice or a blend)."""

from __future__ import annotations

from dataclasses import dataclass

# Voice name prefix -> (espeak-ng language code, accent). Japanese and Mandarin voices
# need a different phonemizer, so they are not offered.
LANGUAGES = {
    "a": ("en-us", "American English"),
    "b": ("en-gb", "British English"),
    "e": ("es", "Spanish"),
    "f": ("fr-fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "p": ("pt-br", "Brazilian Portuguese"),
}

# Overall grades published with the model (hexgrad/Kokoro-82M, VOICES.md). They estimate
# the quality and amount of training audio behind each voice, not personal taste.
GRADES = {
    "af_heart": "A", "af_bella": "A-", "af_nicole": "B-", "af_aoede": "C+", "af_kore": "C+",
    "af_sarah": "C+", "af_alloy": "C", "af_nova": "C", "af_sky": "C-", "af_jessica": "D",
    "af_river": "D",
    "am_fenrir": "C+", "am_michael": "C+", "am_puck": "C+", "am_echo": "D", "am_eric": "D",
    "am_liam": "D", "am_onyx": "D", "am_santa": "D-", "am_adam": "F+",
    "bf_emma": "B-", "bf_isabella": "C", "bf_alice": "D", "bf_lily": "D",
    "bm_fable": "C", "bm_george": "C", "bm_lewis": "D+", "bm_daniel": "D",
}
_GRADE_ORDER = ["A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F+", "F", "?"]

# Friendly names for the best-graded voice of each kind.
ALIASES = {
    "female": "af_heart",
    "male": "am_michael",
    "female-uk": "bf_emma",
    "male-uk": "bm_george",
}
DEFAULT_VOICE = "female"


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str
    accent: str
    lang: str
    grade: str


def describe(name: str) -> Voice | None:
    """Gender, accent and grade from a Kokoro voice name such as 'bf_emma'."""
    if len(name) < 4 or name[2] != "_" or name[0] not in LANGUAGES or name[1] not in "fm":
        return None
    lang, accent = LANGUAGES[name[0]]
    gender = "female" if name[1] == "f" else "male"
    return Voice(name, gender, accent, lang, GRADES.get(name, "?"))


def grade_rank(grade: str) -> int:
    return _GRADE_ORDER.index(grade) if grade in _GRADE_ORDER else len(_GRADE_ORDER)


def catalogue(available: list[str], english_only: bool = True) -> list[Voice]:
    """The usable voices among `available`, best English voices first."""
    voices = [v for v in map(describe, available) if v]
    if english_only:
        voices = [v for v in voices if v.lang.startswith("en")]
    return sorted(voices, key=lambda v: (v.lang != "en-us", v.lang, v.gender, grade_rank(v.grade), v.name))


@dataclass(frozen=True)
class VoiceSpec:
    """One voice, or a weighted blend of voices. Weights sum to 1, largest first."""

    parts: tuple[tuple[str, float], ...]

    @property
    def lang(self) -> str:
        # The dominant voice decides the pronunciation rules.
        return describe(self.parts[0][0]).lang

    @property
    def key(self) -> str:
        return ",".join(f"{name}:{weight:.3f}" for name, weight in self.parts)

    @property
    def label(self) -> str:
        if len(self.parts) == 1:
            return self.parts[0][0]
        return " + ".join(f"{name} {round(weight * 100)}%" for name, weight in self.parts)


def parse_voice(spec: str, available: set[str] | None = None) -> VoiceSpec:
    """Parse 'af_heart', 'male', or a blend such as 'am_michael:60,am_fenrir:40'."""
    spec = ALIASES.get(spec.strip().lower(), spec.strip())
    parts: dict[str, float] = {}
    for item in spec.replace("+", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, weight = item.partition(":")
        name = ALIASES.get(name.strip().lower(), name.strip())
        try:
            value = float(weight) if weight.strip() else 1.0
        except ValueError:
            raise ValueError(f"bad weight in {item!r}; use name:weight, e.g. af_bella:60") from None
        if value <= 0:
            raise ValueError(f"weight must be positive in {item!r}")
        if describe(name) is None:
            raise ValueError(f"unknown voice {name!r}; run 'abgen voices' to list them")
        if available is not None and name not in available:
            raise ValueError(f"voice {name!r} is not in the installed voice file")
        parts[name] = parts.get(name, 0.0) + value
    if not parts:
        raise ValueError("no voice given")
    total = sum(parts.values())
    ordered = sorted(((n, w / total) for n, w in parts.items()), key=lambda p: (-p[1], p[0]))
    return VoiceSpec(tuple(ordered))
