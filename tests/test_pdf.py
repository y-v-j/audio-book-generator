import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from abgen.epub import read_epub, skipped_by_default
from abgen.epubwrite import write_epub
from abgen.pdf import (Line, OutlineEntry, Page, Para, choose_outline, is_junk, join_lines,
                       merge_across_pages, merge_drop_caps, read_pdf, remove_running_heads)

from .pdfmaker import make_pdf, sample_book_pages

needs_poppler = pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler-utils not installed")


def line(text, y, x0=0.1, x1=0.9, h=0.02, conf=96.0):
    return Line(text, x0, y, x1, y + h, conf=conf)


# --- clean-up rules ------------------------------------------------------------------------------
def test_running_heads_and_page_numbers_are_removed():
    pages = [Page(n, [line("THE TEST BOOK", 0.03), line(f"Body text on page {n} goes here.", 0.3),
                      line(str(20 + n), 0.95)], "text") for n in range(1, 6)]
    pages[2].lines.insert(1, line("A HEADING NEAR THE TOP THAT IS UNIQUE", 0.05))
    removed = remove_running_heads(pages)
    assert removed == 10
    assert [l.text for l in pages[0].lines] == ["Body text on page 1 goes here."]
    assert "A HEADING NEAR THE TOP THAT IS UNIQUE" in [l.text for l in pages[2].lines]


def test_hyphenation():
    vocab = Counter({"well-known": 3, "beginning": 1})
    assert join_lines(["the begin-", "ning of it"], vocab) == "the beginning of it"
    assert join_lines(["a well-", "known face"], vocab) == "a well-known face"
    assert join_lines(["Anglo-", "Saxon kings"], vocab) == "Anglo-Saxon kings"
    assert join_lines(["he paused—", "then went on"], vocab) == "he paused—then went on"
    assert join_lines(["one line", "and another"], vocab) == "one line and another"


def test_ocr_junk():
    h = 0.012
    assert is_junk(Line("be", 0, 0, 0.1, 0.06, conf=46), "ocr", h)
    assert is_junk(Line("Af", 0, 0, 0.1, 0.074, conf=90), "ocr", h)  # giant letters in a drawing
    assert is_junk(Line("~~ Ls ~", 0, 0, 0.1, 0.017, conf=56), "ocr", h)
    assert not is_junk(Line("is continually", 0, 0, 0.3, 0.012, conf=95), "ocr", h)
    assert not is_junk(Line("* * *", 0, 0, 0.1, 0.012, conf=40), "ocr", h)  # scene break
    assert not is_junk(Line("be", 0, 0, 0.1, 0.012, conf=46), "text", h)  # text layer is trusted


def test_drop_caps():
    paras = [Para([], 1, "heading", "S"), Para([], 1, "para", "ince its first publication much has changed.")]
    assert [p.text for p in merge_drop_caps(paras)] == ["Since its first publication much has changed."]
    paras = [Para([], 1, "heading", "I"), Para([], 1, "para", "T is a singular fact that he came.")]
    assert [p.text for p in merge_drop_caps(paras)] == ["It is a singular fact that he came."]
    paras = [Para([], 1, "heading", "I"), Para([], 1, "para", "The war began in spring.")]
    assert len(merge_drop_caps(paras)) == 2  # a real 'I' heading


def test_continuations_are_joined():
    paras = [Para([], 1, "para", "least of all the baker, who had lived"),
             Para([], 2, "para", "there for forty years."),
             Para([], 2, "para", "The bread"),
             Para([], 2, "para", "was still warm."),
             Para([], 2, "para", "He left.")]
    assert [p.text for p in merge_across_pages(paras, Counter())] == [
        "least of all the baker, who had lived there for forty years.",
        "The bread was still warm.", "He left."]


def test_outline_depth():
    entries = [OutlineEntry("Book", 1, 0)] + [OutlineEntry(f"Ch {i}", 2 + 10 * i, 1) for i in range(20)]
    chosen = choose_outline(entries, 210)
    assert len(chosen) == 21
    entries += [OutlineEntry(f"Sub {i}", 3 + i, 2) for i in range(200)]
    assert len(choose_outline(entries, 210)) == 21  # sections too small to be chapters


# --- whole PDFs ---------------------------------------------------------------------------------------
@needs_poppler
def test_text_pdf_to_chapters(tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf", sample_book_pages(), title="The Test Book", author="Jane Doe")
    book = read_pdf(pdf, ocr="never", cache_dir=tmp_path / "ocr")
    assert book.title == "The Test Book" and book.authors == ["Jane Doe"]
    assert [c.title for c in book.chapters] == ["Chapter I. The Beginning", "Chapter II. The Stranger"]
    first = book.chapters[0].blocks
    assert [b.kind for b in first[:2]] == ["heading", "heading"]
    paras = [b.text for b in first if b.kind == "para"]
    assert paras[0] == "It was a cold morning when the beginning of the story took place in the old town by the river."
    assert paras[1].startswith("Nobody expected") and paras[1].endswith("knew every face in the street.")
    assert "The well-known bell over the door rang twice." in paras
    every = " ".join(b.text for c in book.chapters for b in c.blocks)
    assert "THE TEST BOOK" not in every.upper().replace("CHAPTER", "") or "TEST BOOK" not in every
    assert not any(b.text.strip().isdigit() for c in book.chapters for b in c.blocks)


@needs_poppler
def test_bookmarks_give_chapters(tmp_path):
    pdf = make_pdf(tmp_path / "marked.pdf", sample_book_pages(),
                   bookmarks=[("The Beginning", 1), ("The Stranger", 3)])
    book = read_pdf(pdf, ocr="never", cache_dir=tmp_path / "ocr")
    assert [c.title for c in book.chapters] == ["The Beginning", "The Stranger"]
    assert book.title == "Marked"  # no metadata: from the file name


@needs_poppler
def test_pdf_to_epub_round_trip(tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf", sample_book_pages(), title="The Test Book", author="Jane Doe")
    book = read_pdf(pdf, ocr="never", cache_dir=tmp_path / "ocr")
    out = write_epub(book, tmp_path / "book.epub")
    again = read_epub(str(out))
    assert again.title == "The Test Book" and again.author == "Jane Doe"
    assert [c.title for c in again.chapters] == [c.title for c in book.chapters]
    assert [b.text for b in again.chapters[0].blocks] == [b.text for b in book.chapters[0].blocks]
    assert again.cover is not None and again.cover_type == "image/jpeg"
    assert not any(skipped_by_default(c) for c in again.chapters)
    import zipfile

    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


@pytest.mark.slow
@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("magick") and shutil.which("pdftoppm")),
                    reason="needs tesseract, ImageMagick and poppler")
def test_scanned_pdf_is_read_by_ocr(tmp_path):
    text_pdf = make_pdf(tmp_path / "text.pdf", sample_book_pages())
    subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(text_pdf), str(tmp_path / "pg")], check=True)
    pngs = sorted(tmp_path.glob("pg-*.png"))
    scan = tmp_path / "scan.pdf"
    subprocess.run(["magick", *map(str, pngs), "-density", "300", str(scan)], check=True)
    book = read_pdf(scan, ocr="auto", dpi=300, cache_dir=tmp_path / "ocr")
    assert [c.title for c in book.chapters] == ["Chapter I. The Beginning", "Chapter II. The Stranger"]
    paras = [b.text for c in book.chapters for b in c.blocks if b.kind == "para"]
    text = " ".join(paras)
    assert "beginning of the story" in text  # hyphenated word rejoined
    assert "who had lived there for forty years" in text  # paragraph joined across the page break
    assert "TEST BOOK" not in text and not any(p.strip().isdigit() for p in paras)
    assert len(paras) == 7
