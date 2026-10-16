"""Rewrite book text the way a narrator would say it.

The speech model reads what it is given literally: "Mr." becomes "Mister" followed by a
full stop (a sentence-ending pause mid-sentence), "1881" becomes "one thousand eight
hundred eighty-one", and "CHAPTER IV" is read letter by letter. This module expands
abbreviations, numbers, years, money and Roman numerals before synthesis.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from num2words import num2words

_CHARS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"', "″": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "­": "", "﻿": "", "⁠": "",
    "†": "", "‡": "", "¶": "", "•": "", "·": " ", "™": "", "®": "", "©": "",
})

_FRACTIONS = {"½": ("a half", "half"), "¼": ("a quarter", "quarter"), "¾": ("three quarters", "three quarters"),
              "⅓": ("a third", "third"), "⅔": ("two thirds", "two thirds"), "⅛": ("an eighth", "eighth")}
_FRACTION = re.compile(r"(\d*)\s?([½¼¾⅓⅔⅛])")


def _fraction(m: re.Match) -> str:
    alone, after_number = _FRACTIONS[m.group(2)]
    if not m.group(1):
        return alone
    return f"{m.group(1)} and {alone}"


_MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "Jun": "June",
           "Jul": "July", "Aug": "August", "Sep": "September", "Sept": "September", "Oct": "October",
           "Nov": "November", "Dec": "December"}


def _say(n: int) -> str:
    return num2words(n).replace(",", "")


def _say_year(n: int) -> str:
    return num2words(n, to="year").replace(",", "")


def _say_ordinal(n: int) -> str:
    return num2words(n, to="ordinal").replace(",", "")


def _digits(s: str) -> str:
    names = "zero one two three four five six seven eight nine".split()
    return " ".join(names[int(d)] for d in s)


# --- abbreviations -------------------------------------------------------------------
_NAME = r"(?=\s+[A-Z])"  # only before a capitalised name, e.g. "Dr. Watson"
_ABBREVIATIONS = [
    (r"\bMr\.?(?=\s+(?:and|&)\s+M)", "Mister"),  # "Mr. and Mrs. Rucastle"
    (r"\bDr\.?(?=\s+(?:and|&)\s+M)", "Doctor"),
    (r"\b(" + "|".join(_MONTHS) + r")\.(?=\s*[\dA-Za-z])", lambda m: _MONTHS[m.group(1)]),
    (rf"\bMr\.?{_NAME}", "Mister"),
    (rf"\bMrs\.?{_NAME}", "Missus"),
    (rf"\bMs\.?{_NAME}", "Miz"),
    (rf"\bMessrs\.?{_NAME}", "Messieurs"),
    (rf"\bDr\.?{_NAME}", "Doctor"),
    (rf"\bProf\.{_NAME}", "Professor"),
    (rf"\bCapt\.{_NAME}", "Captain"),
    (rf"\bCol\.{_NAME}", "Colonel"),
    (rf"\bGen\.{_NAME}", "General"),
    (rf"\bLt\.{_NAME}", "Lieutenant"),
    (rf"\bSgt\.{_NAME}", "Sergeant"),
    (rf"\bRev\.{_NAME}", "Reverend"),
    (rf"\bHon\.{_NAME}", "Honourable"),
    (rf"\bGov\.{_NAME}", "Governor"),
    (rf"\bMt\.{_NAME}", "Mount"),
    (rf"\bSt\.{_NAME}", "Saint"),
    (r"(?<=[a-z]) St\.(?=\s*$)", " Street."),
    (r"(?<=[a-z]) St\.(?=\s*[a-z,;:)])", " Street"),
    (r"\bJr\.", "Junior"),
    (r"\bSr\.", "Senior"),
    (r"\bEsq\.", "Esquire"),
    (r"\bvs?\.(?=\s)", "versus"),
    (r"\betc\.(?=\s+[A-Z]|\s*$)", "et cetera."),
    (r"\betc\.", "et cetera"),
    (r"\be\.\s?g\.,?", "for example,"),
    (r"\bi\.\s?e\.,?", "that is,"),
    (r"\bNo\.(?=\s*\d)", "number"),
    (r"\bNos\.(?=\s*\d)", "numbers"),
    (r"\bVol\.(?=\s*[\dIVXLC])", "volume"),
    (r"\bCh\.(?=\s*[\dIVXLC])", "chapter"),
    (r"\bpp\.(?=\s*\d)", "pages"),
    (r"\bp\.(?=\s*\d)", "page"),
    (r"\bapprox\.", "approximately"),
    (r"\b([ap])\.\s?m\.(?=\s+[A-Z]|\s*$)", lambda m: f"{m.group(1).upper()} M."),
    (r"\b([ap])\.\s?m\.", lambda m: f"{m.group(1).upper()} M"),
    (r"&", " and "),
    (r"§\s*", "section "),
    (r"#(?=\d)", "number "),
    (r"(\d)\s*%", r"\1 percent"),
]
_ABBREVIATIONS = [(re.compile(p), r) for p, r in _ABBREVIATIONS]


# --- Roman numerals --------------------------------------------------------------------
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int | None:
    """Value of a well-formed Roman numeral, or None (so 'MIX' or 'DIM' won't pass as words)."""
    if not s or any(c not in _ROMAN_VALUES for c in s):
        return None
    total = 0
    for a, b in zip(s, s[1:] + " "):
        v = _ROMAN_VALUES[a]
        total += -v if b != " " and _ROMAN_VALUES[b] > v else v
    return total if 0 < total < 4000 and int_to_roman(total) == s else None


def int_to_roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                       (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += sym
            n -= value
    return out


_SECTION_WORDS = ("Chapter", "Part", "Book", "Volume", "Section", "Act", "Scene", "Canto", "Letter",
                  "Stave", "Appendix", "Lecture", "Lesson", "Article")
# "Chapter IV" or "CHAPTER IV", but not "the letter X" in running text.
_SECTION_ROMAN = re.compile(
    r"\b(" + "|".join(w for s in _SECTION_WORDS for w in (s, s.upper())) + r")\s+([IVXLCDM]+)\b(\.?)")
_NAME_ROMAN = re.compile(r"\b([A-Z][a-z]+)\s+(II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII)\b")
# Rulers and popes are "Henry the Eighth"; anything else is a plain number
# ("World War Two", "Apollo Eleven").
_REGNAL_NAMES = set("""Henry George Edward William Richard Charles James Louis Elizabeth Mary Victoria
Anne John Philip Peter Catherine Frederick Ferdinand Napoleon Pius Leo Gregory Innocent Benedict
Clement Boniface Urban Alexander Nicholas Ivan Gustav Gustavus Christian Olaf Harald Haakon Alfonso
Pedro Francis Franz Joseph Leopold Rudolf Otto Wilhelm Ludwig Constantine Paul Ramesses Ptolemy
Amenhotep Thutmose Cleopatra Edmund Robert David Malcolm Kenneth Duncan Stephen Alfred Sancho
Casimir Sigismund Vladislaus Boleslaus Bela Andrew Manuel Michael Basil Leopold Umberto Victor
Amadeus Selim Mehmed Murad Suleiman Mahmud Abdul""".split())
_LEADING_ROMAN = re.compile(r"^([IVXLCDM]+)([.:]\s*|\s*$)")


def _section(m: re.Match) -> str:
    n = roman_to_int(m.group(2))
    return f"{m.group(1).capitalize()} {_say(n)}{m.group(3)}" if n else m.group(0)


def _name_roman(m: re.Match) -> str:
    n = roman_to_int(m.group(2))
    if not n:
        return m.group(0)
    if m.group(1) in _REGNAL_NAMES:
        return f"{m.group(1)} the {_say_ordinal(n)}"
    return f"{m.group(1)} {_say(n)}"


# --- numbers -----------------------------------------------------------------------------
def _money(amount: str, cents: str | None, unit: str, units: str, small: str, smalls: str) -> str:
    n = int(amount.replace(",", ""))
    out = f"{_say(n)} {unit if n == 1 else units}"
    if cents and int(cents):
        c = int(cents.ljust(2, "0"))
        out += f" and {_say(c)} {small if c == 1 else smalls}"
    return out


def _plural_tens(words: str) -> str:
    """'nineteen sixty' -> 'nineteen sixties' (for decades)."""
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


def _old_money(m: re.Match) -> str:
    """£5 10s 6d -> five pounds, ten shillings and sixpence (pre-decimal British money)."""
    pounds, shillings, pence = m.group(1), m.group(2), m.group(3)
    parts = []
    if pounds:
        n = int(pounds.replace(",", ""))
        parts.append(f"{_say(n)} {'pound' if n == 1 else 'pounds'}")
    if shillings:
        n = int(shillings)
        parts.append(f"{_say(n)} {'shilling' if n == 1 else 'shillings'}")
    if pence:
        n = int(pence)
        parts.append(f"{_say(n)} {'penny' if n == 1 else 'pence'}")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


_NUMBER_RULES = [
    (re.compile(r"£\s?(\d[\d,]*)\s+(\d{1,2})s\.?(?:\s*(\d{1,2})d\.?)?(?=\W|$)"), _old_money),
    (re.compile(r"(?<![£\w])()(\d{1,2})s\.?\s*(\d{1,2})d\.?(?=\W|$)"), _old_money),
    (re.compile(r"(?<![£\w])()(\d{1,2})s\.(?=[\s,;:)'\"]|$)()"), _old_money),       # "8s." alone
    (re.compile(r"(?<![£\w])()()(\d{1,2})d\.(?=[\s,;:)'\"]|$)"), _old_money),       # "8d." alone
    (_FRACTION, _fraction),
    (re.compile(r"\b(1[1-9]\d0|20[0-9]0)'?s\b"), lambda m: _plural_tens(_say_year(int(m.group(1))))),
    (re.compile(r"(?<!\w)'?([2-9]0)'?s\b"), lambda m: _plural_tens(_say(int(m.group(1))))),
    (re.compile(r"£\s?(\d[\d,]*)(?:\.(\d{1,2}))?"),
     lambda m: _money(m.group(1), m.group(2), "pound", "pounds", "penny", "pence")),
    (re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?"),
     lambda m: _money(m.group(1), m.group(2), "dollar", "dollars", "cent", "cents")),
    (re.compile(r"€\s?(\d[\d,]*)(?:\.(\d{2}))?"),
     lambda m: _money(m.group(1), m.group(2), "euro", "euros", "cent", "cents")),
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"), lambda m: _say_ordinal(int(m.group(1)))),
    # 1914-1918 / 1914–1918 -> "nineteen fourteen to nineteen eighteen"
    (re.compile(r"\b(1[1-9]\d\d|20\d\d)\s*[-–]\s*(1[1-9]\d\d|20\d\d)\b"),
     lambda m: f"{_say_year(int(m.group(1)))} to {_say_year(int(m.group(2)))}"),
    (re.compile(r"\b(\d+)\s*[-–]\s*(\d+)\b"), r"\1 to \2"),  # pages 10-12
    # Four-digit numbers from 1100 to 2099 that stand alone are read as years
    # ("eighteen eighty-one"); this also gives the natural "fifteen hundred" for 1500.
    (re.compile(r"(?<![\d,.£$€])\b(1[1-9]\d\d|20\d\d)\b(?![\d,]|\.\d)"),
     lambda m: _say_year(int(m.group(1)))),
    (re.compile(r"\b(\d{1,2}):(\d{2})\b"),
     lambda m: _say(int(m.group(1))) + ("" if m.group(2) == "00"
                                        else " oh " + _say(int(m.group(2))) if m.group(2)[0] == "0"
                                        else " " + _say(int(m.group(2))))),
    (re.compile(r"\b(\d+)\.(\d+)\b"), lambda m: f"{_say(int(m.group(1)))} point {_digits(m.group(2))}"),
    (re.compile(r"\b\d{1,3}(?:,\d{3})+\b"), lambda m: _say(int(m.group(0).replace(",", "")))),
    (re.compile(r"\b\d{10,}\b"), lambda m: _digits(m.group(0))),
    (re.compile(r"\b\d+\b"), lambda m: _say(int(m.group(0)))),
]


# --- punctuation and clean-up -------------------------------------------------------------
_LINKS = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\w+")
_NOTE_MARKERS = re.compile(r"\s?\[(?:\d+|[a-z]|\*|†)\]")  # footnote markers left in the text
_CLEANUP = [
    (re.compile(r"\s*(?:--+|–)\s*"), " — "),
    (re.compile(r"(?<=[\w,;])\s+-\s+(?=\w)"), " — "),  # a spaced hyphen used as a dash
    (re.compile(r"\s*—\s*"), " — "),
    (re.compile(r"\.\s?\.\s?\.(?:\s?\.)?|…"), "…"),
    (re.compile(r"[*_~^|<>{}\\]"), " "),
    (re.compile(r"\(\s*\)|\[\s*\]"), ""),
    (re.compile(r"\s+([,;:.!?…)])"), r"\1"),
    (re.compile(r"([,;:])(?=[^\s\d\"'])"), r"\1 "),
    (re.compile(r"\s{2,}"), " "),
]


def _decaps(text: str) -> str:
    """'A SCANDAL IN BOHEMIA' -> 'A Scandal In Bohemia', so it isn't read as letters."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4 or sum(c.isupper() for c in letters) / len(letters) < 0.6:
        return text
    return re.sub(r"[A-Za-z][A-Za-z']*", lambda m: m.group(0).capitalize(), text)


def load_lexicon(path: str | Path | None) -> dict[str, str]:
    """Read 'word = how to say it' lines (comments start with #)."""
    lexicon: dict[str, str] = {}
    if not path:
        return lexicon
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            word, _, say = line.partition("=")
            if word.strip() and say.strip():
                lexicon[word.strip()] = say.strip()
    return lexicon


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    for word, say in sorted(lexicon.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", say, text, flags=re.I)
    return text


def normalize(text: str, heading: bool = False, lexicon: dict[str, str] | None = None) -> str:
    """Text as a narrator would say it. Headings also get a closing full stop."""
    text = unicodedata.normalize("NFC", text).translate(_CHARS)
    text = _LINKS.sub("", text)
    text = _NOTE_MARKERS.sub("", text)  # before numbers are spelled out
    text = " ".join(text.split())
    if lexicon:
        text = apply_lexicon(text, lexicon)
    if heading:
        # Roman numerals first (they rely on capitals), then 'MR. SHERLOCK HOLMES' ->
        # 'Mr. Sherlock Holmes' so the abbreviation rules below apply.
        text = _SECTION_ROMAN.sub(_section, text)
        m = _LEADING_ROMAN.match(text)
        if m and roman_to_int(m.group(1)):
            text = _say(roman_to_int(m.group(1))).capitalize() + ". " + text[m.end():]
        text = _decaps(text)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    text = _SECTION_ROMAN.sub(_section, text)
    text = _NAME_ROMAN.sub(_name_roman, text)
    for pattern, replacement in _NUMBER_RULES:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _CLEANUP:
        text = pattern.sub(replacement, text)
    text = text.strip(" ,;:")
    if heading and text and text[-1] not in ".!?…":
        text += "."
    return text
