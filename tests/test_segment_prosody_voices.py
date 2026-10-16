import pytest

from abgen.cli import parse_selection
from abgen.prosody import PAUSES, Prosody, is_dialogue
from abgen.segment import LIMIT, chunk, split_sentences
from abgen.voices import catalogue, describe, parse_voice


def test_split_sentences_keeps_quotes_and_initials():
    text = 'Mister J. H. Watson came. "Who is it?" he asked. "Go!" She left.'
    assert split_sentences(text) == ['Mister J. H. Watson came.', '"Who is it?" he asked.', '"Go!"', 'She left.']


def test_chunks_bundle_short_sentences_and_respect_limit():
    text = "Yes. " * 3 + "This is a longer sentence that goes on for a while. " * 6 + "No."
    pieces = chunk(text.strip())
    assert all(len(p) <= LIMIT for p in pieces)
    assert pieces[0].startswith("Yes. Yes. Yes. This")  # short ones are not left alone
    assert pieces[-1].endswith("No.") and len(pieces[-1]) > 10
    assert " ".join(pieces) == text.strip()


def test_short_last_sentence_joins_the_one_before():
    first = "This sentence is long enough that the next one, short as it is, should not be added to it at all."
    first = first + " " + first  # over TARGET, so a third sentence starts a new piece
    assert chunk(first + " Indeed.") == [first + " Indeed."]


def test_long_sentence_is_split_at_a_clause():
    sentence = ("He walked along the river for hours, thinking about the letter and the strange man who had "
                "brought it to the door that morning in the rain; and when at last he turned for home the lamps "
                "were lit, the fog had come down over the water, and the whole of London seemed to have gone "
                "quiet, as if it, too, were waiting for something that none of them could name.")
    assert len(sentence) > LIMIT
    pieces = chunk(sentence)
    assert len(pieces) >= 2 and all(len(p) <= LIMIT for p in pieces)
    assert pieces[0].endswith((",", ";"))
    assert " ".join(pieces) == sentence


def test_pauses_are_varied_but_repeatable():
    p = Prosody()
    a = [p.pause("paragraph", str(i)) for i in range(20)]
    assert len(set(a)) > 10
    low, high = PAUSES["paragraph"]
    assert all(low <= x <= high for x in a)
    assert a == [Prosody().pause("paragraph", str(i)) for i in range(20)]
    assert Prosody(expressive=False).pause("paragraph", "x") == round((low + high) / 2, 3)


def test_pace():
    p = Prosody(speed=1.0)
    assert p.pace("heading", "x") == 0.92
    speeds = {p.pace("para", f"paragraph {i}") for i in range(30)}
    assert len(speeds) > 5 and all(0.97 <= s <= 1.05 for s in speeds)
    assert Prosody(speed=1.0, expressive=False).pace("para", '"Hi," she said.') == 1.0
    assert is_dialogue('"Hello," he said.') and not is_dialogue("Hello, he said.")


def test_voices():
    assert describe("bf_emma").gender == "female" and describe("bf_emma").lang == "en-gb"
    assert describe("am_michael").accent == "American English"
    assert describe("jf_alpha") is None  # Japanese needs another phonemizer
    assert parse_voice("male").parts == (("am_michael", 1.0),)
    blend = parse_voice("am_fenrir:40, am_michael:60")
    assert blend.parts[0][0] == "am_michael" and abs(blend.parts[0][1] - 0.6) < 1e-9
    assert blend.label == "am_michael 60% + am_fenrir 40%"
    assert blend.lang == "en-us"
    with pytest.raises(ValueError):
        parse_voice("zz_nobody")
    with pytest.raises(ValueError):
        parse_voice("af_heart:-1")
    names = [v.name for v in catalogue(["bm_george", "af_heart", "af_sky", "jf_alpha"])]
    assert names == ["af_heart", "af_sky", "bm_george"]


def test_chapter_selection():
    assert parse_selection("1-3,5,9-", 10, []) == [1, 2, 3, 5, 9, 10]
    assert parse_selection(None, 10, [2, 3]) == [2, 3]
    assert parse_selection("-2", 10, []) == [1, 2]
    with pytest.raises(ValueError):
        parse_selection("12", 10, [])
    with pytest.raises(ValueError):
        parse_selection("a-b", 10, [])
