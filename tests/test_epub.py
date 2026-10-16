import pytest

from abgen.epub import read_epub, skipped_by_default
from abgen.pipeline import Pause, Settings, Speech, plan_book
from abgen.prosody import Prosody
from abgen.voices import parse_voice


def test_toc_anchors_split_one_file_into_chapters(epub_factory):
    path = epub_factory(
        {"text.xhtml": """
            <h1 id="c1">CHAPTER I. A BEGINNING</h1>
            <p>It was a dark night.<sup><a epub:type="noteref" href="#n1">1</a></sup> Rain fell.</p>
            <span class="pagenum">[12]</span>
            <p>Second paragraph.</p>
            <hr/>
            <p>After the break.</p>
            <h1 id="c2">CHAPTER II. AN END</h1>
            <p>The <i>very</i> end.</p>
            <aside epub:type="footnote" id="n1"><p>A footnote.</p></aside>"""},
        [("text.xhtml#c1", "Chapter I. A Beginning"), ("text.xhtml#c2", "Chapter II. An End")])
    book = read_epub(str(path))
    assert book.title == "The Test Book" and book.author == "A. N. Author"
    assert [c.title for c in book.chapters] == ["Chapter I. A Beginning", "Chapter II. An End"]
    first = book.chapters[0].blocks
    assert [b.kind for b in first] == ["heading", "para", "para", "break", "para"]
    assert first[1].text == "It was a dark night. Rain fell."  # note marker dropped
    assert all("12" not in b.text for b in first)  # page number dropped
    assert book.chapters[1].blocks[1].text == "The very end."
    assert all("footnote" not in b.text for c in book.chapters for b in c.blocks)


def test_files_without_toc_entry_continue_the_chapter(epub_factory):
    path = epub_factory(
        {"one.xhtml": "<h2>One</h2><p>Start of chapter one.</p>",
         "one_b.xhtml": "<p>Rest of chapter one.</p>",
         "two.xhtml": "<h2>Two</h2><p>Chapter two.</p>"},
        [("one.xhtml", "One"), ("two.xhtml", "Two")], ncx=True)
    book = read_epub(str(path))
    assert [c.title for c in book.chapters] == ["One", "Two"]
    assert [b.text for b in book.chapters[0].blocks] == ["One", "Start of chapter one.", "Rest of chapter one."]


def test_part_title_is_kept_as_heading(epub_factory):
    path = epub_factory(
        {"part.xhtml": '<h1 id="p">PART ONE</h1><h2 id="c">Chapter 1</h2><p>Text.</p>'},
        [("part.xhtml#p", "Part One"), ("part.xhtml#c", "Chapter 1")])
    book = read_epub(str(path))
    assert [c.title for c in book.chapters] == ["Chapter 1"]
    assert [(b.kind, b.text) for b in book.chapters[0].blocks] == [
        ("heading", "PART ONE"), ("heading", "Chapter 1"), ("para", "Text.")]


def test_verse_line_breaks_become_pauses_and_cover_is_found(epub_factory):
    path = epub_factory({"poem.xhtml": "<h2>Poem</h2><p>Roses are red<br/>Violets are blue</p>"},
                        [("poem.xhtml", "Poem")], cover=b"\xff\xd8\xff fake jpeg")
    book = read_epub(str(path))
    assert book.chapters[0].blocks[1].text == "Roses are red, Violets are blue"
    assert book.cover.startswith(b"\xff\xd8") and book.cover_type == "image/jpeg"


def test_default_skips(epub_factory):
    path = epub_factory(
        {"toc.xhtml": "<h2>Contents</h2><p>Chapter 1 ... 3</p>",
         "c1.xhtml": "<h2>Chapter 1</h2><p>Story.</p>",
         "lic.xhtml": "<h2>THE FULL PROJECT GUTENBERG LICENSE</h2><p>Terms.</p>"},
        [("toc.xhtml", "Contents"), ("c1.xhtml", "Chapter 1"), ("lic.xhtml", "The Full Project Gutenberg License")])
    book = read_epub(str(path))
    assert [skipped_by_default(c) for c in book.chapters] == [True, False, True]


ENCRYPTION = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:CipherData><enc:CipherReference URI="OEBPS/{target}"/></enc:CipherData></enc:EncryptedData>
</encryption>"""


def test_drm_protected_epub_is_refused(epub_factory):
    chapter = ({"c1.xhtml": "<p>Story.</p>"}, [("c1.xhtml", "One")])
    encrypted = ENCRYPTION.format(algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc", target="c1.xhtml")
    for extra in ({"META-INF/encryption.xml": encrypted}, {"META-INF/license.lcpl": "{}"}):
        with pytest.raises(ValueError, match="DRM"):
            read_epub(str(epub_factory(*chapter, extra=extra)))
    fonts = ENCRYPTION.format(algorithm="http://www.idpf.org/2008/embedding", target="fonts/serif.otf")
    book = read_epub(str(epub_factory(*chapter, extra={"META-INF/encryption.xml": fonts})))
    assert book.chapters[0].blocks[0].text == "Story."  # obfuscated fonts are not DRM


def test_plan_reads_title_once_and_paces_dialogue(epub_factory):
    path = epub_factory(
        {"c1.xhtml": '<h2>CHAPTER I.</h2><h3>MR. SHERLOCK HOLMES</h3>'
                     '<p>"Who is it?" he asked.</p><p>"Only me," said Mrs. Hudson.</p><p>He sat down.</p>'},
        [("c1.xhtml", "Chapter I. Mr. Sherlock Holmes")])
    book = read_epub(str(path))
    settings = Settings(voice=parse_voice("female"), prosody=Prosody())
    plan = plan_book(book, [1], settings)[0]
    spoken = [i.text for i in plan.items if isinstance(i, Speech)]
    assert spoken[:3] == ["The Test Book. Written by A. N. Author.", "Chapter one.", "Mister Sherlock Holmes."]
    assert spoken[-1] == "End of The Test Book."
    assert sum("Sherlock" in s for s in spoken) == 1  # TOC title not read a second time
    pauses = [i.seconds for i in plan.items if isinstance(i, Pause)]
    assert all(b > 0 for b in pauses)
    assert not any(isinstance(a, Pause) and isinstance(b, Pause)
                   for a, b in zip(plan.items, plan.items[1:]))  # breaks never stack up
    keys = [i.key for i in plan.items if isinstance(i, Speech)]
    assert keys == [i.key for i in plan_book(book, [1], settings)[0].items if isinstance(i, Speech)]
