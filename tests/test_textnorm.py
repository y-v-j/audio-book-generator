import pytest

from abgen.textnorm import apply_lexicon, int_to_roman, load_lexicon, normalize, roman_to_int


@pytest.mark.parametrize("text, expected", [
    ("Mr. Holmes met Dr. Watson.", "Mister Holmes met Doctor Watson."),
    ("Mrs Hudson and Mr Holmes", "Missus Hudson and Mister Holmes"),
    ("St. Clair lived on Baker St.", "Saint Clair lived on Baker Street."),
    ("tea, cake, etc. Then we left.", "tea, cake, et cetera. Then we left."),
    ("small things, e.g. pins", "small things, for example, pins"),
    ("Smith & Sons", "Smith and Sons"),
])
def test_abbreviations(text, expected):
    assert normalize(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("in 1881.", "in eighteen eighty-one."),
    ("in 2025 and 1500 men", "in twenty twenty-five and fifteen hundred men"),
    ("from 1914–1918", "from nineteen fourteen to nineteen eighteen"),
    ("the 22nd of June", "the twenty-second of June"),
    ("pages 10-12", "pages ten to twelve"),
    ("1,500 men", "one thousand five hundred men"),
    ("3.75%", "three point seven five percent"),
    ("at 10:30 p.m.", "at ten thirty P M."),
    ("at 7:05", "at seven oh five"),
    ("£5 10s 6d", "five pounds, ten shillings and six pence"),
    ("£1.", "one pound."),
    ("$3.50", "three dollars and fifty cents"),
    ("the 1890s and the '80s", "the eighteen nineties and the eighties"),
    ("rooms 8s., sherry, 8d.", "rooms eight shillings, sherry, eight pence"),
    ("paying 4½ per cent", "paying four and a half per cent"),
    ("½ a mile", "a half a mile"),
    ("Oct. 4th", "October fourth"),
    ("Mr. and Mrs. Rucastle", "Mister and Missus Rucastle"),
])
def test_numbers(text, expected):
    assert normalize(text) == expected


def test_roman_numerals():
    assert normalize("CHAPTER IV. THE RED-HEADED LEAGUE", heading=True) == "Chapter Four. The Red-Headed League."
    assert normalize("IV. A CASE OF IDENTITY", heading=True) == "Four. A Case Of Identity."
    assert normalize("Henry VIII and George III") == "Henry the eighth and George the third"
    assert normalize("World War II") == "World War two"
    # Not section numbers in running text
    assert normalize("the letter X was faint") == "the letter X was faint"
    assert normalize("I went home", heading=True) == "I went home."


def test_roman_round_trip():
    for n in (1, 4, 9, 14, 40, 90, 400, 1994, 3999):
        assert roman_to_int(int_to_roman(n)) == n
    assert roman_to_int("IIII") is None
    assert roman_to_int("MIXED") is None


def test_punctuation_cleanup():
    out = normalize("He said—no, he whispered--“Come here…” [12]")
    assert out == 'He said — no, he whispered — "Come here…"'
    assert normalize("Wait. . . what?") == "Wait… what?"
    assert normalize("see https://example.com now") == "see now"


def test_heading_gets_full_stop():
    assert normalize("The Five Orange Pips", heading=True) == "The Five Orange Pips."


def test_lexicon(tmp_path):
    path = tmp_path / "lex.txt"
    path.write_text("# names\nHermione = Her-my-oh-nee\nSt John = Sinjun\n")
    lexicon = load_lexicon(path)
    assert lexicon == {"Hermione": "Her-my-oh-nee", "St John": "Sinjun"}
    assert apply_lexicon("Hermione met St John.", lexicon) == "Her-my-oh-nee met Sinjun."
    assert normalize("hermione", lexicon=lexicon) == "Her-my-oh-nee"
