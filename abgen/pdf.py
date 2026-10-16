"""Read a PDF, text-based or scanned, into a Book of chapters, headings and paragraphs.

Text comes from the PDF's own text layer where a page has one (pdftotext), and from OCR
(Tesseract) for scanned pages. Both give lines with positions, which are then cleaned up
the way a typesetter would:

* running headers, footers and page numbers are removed (lines at the top or bottom of
  the page that repeat across pages);
* OCR noise from pictures and page edges is dropped (low confidence, few real words);
* words split at line ends are rejoined, keeping real hyphens ("well-known");
* lines become paragraphs by indentation and spacing, and a paragraph that runs over a
  page break is joined back together;
* larger or all-capital lines become headings, "* * *" or "§" become scene breaks;
* chapters come from the PDF's bookmarks or, without them, from headings like
  "CHAPTER III".

Pages are rendered and recognised one at a time in parallel worker processes, so memory
use stays small even for large scans, and every page's OCR result is cached.
"""

from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .epub import BARE_TITLE, Block, Book, Chapter, _SKIP_TITLES, display_title

OCR_VERSION = 1  # bump when the OCR settings change, to ignore old cached pages
TESSERACT_LANGS = {"eng": "en", "fra": "fr", "deu": "de", "spa": "es", "ita": "it", "por": "pt",
                   "nld": "nl", "pol": "pl", "rus": "ru", "tur": "tr", "ces": "cs", "ell": "el"}


@dataclass
class Line:
    text: str
    x0: float  # position as a fraction of the page width / height
    y0: float
    x1: float
    y1: float
    block: int = 0
    par: int = 0
    conf: float = 100.0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Page:
    number: int  # 1-based
    lines: list[Line]
    source: str  # "text" (the PDF's own text) or "ocr"
    aspect: float = 1.4  # height / width


@dataclass
class Para:
    lines: list[Line]
    page: int
    kind: str = "para"  # "para", "heading" or "break"
    text: str = ""
    tall: bool = False  # printed noticeably larger than the body text


# --- tools ----------------------------------------------------------------------------------
def _tool(name: str, package: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"'{name}' was not found; install {package}")
    return path


def _run(args: list[str], env: dict | None = None) -> bytes:
    result = subprocess.run(args, capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr.decode(errors='replace')[-500:]}")
    return result.stdout


def file_id(path: Path) -> str:
    import hashlib

    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


# --- text layer -----------------------------------------------------------------------------
def text_layer(path: Path) -> list[tuple[list[Line], float]]:
    """Lines of every page from the PDF's own text, with the page's height/width ratio."""
    from lxml import etree

    out = _run([_tool("pdftotext", "poppler-utils"), "-bbox-layout", "-enc", "UTF-8", str(path), "-"])
    root = etree.fromstring(out, parser=etree.XMLParser(recover=True, huge_tree=True))
    pages = []
    for page in root.iter("{*}page"):
        w, h = float(page.get("width")), float(page.get("height"))
        lines = []
        for b, block in enumerate(page.iter("{*}block")):
            for line in block.iter("{*}line"):
                text = " ".join(w_.text for w_ in line.iter("{*}word") if w_.text)
                if text.strip():
                    lines.append(Line(text, float(line.get("xMin")) / w, float(line.get("yMin")) / h,
                                      float(line.get("xMax")) / w, float(line.get("yMax")) / h, b, b))
        pages.append((lines, h / w))
    return pages


# --- OCR ------------------------------------------------------------------------------------
def _roman_fix(token: str) -> str:
    """'Il' -> 'II', 'lV' -> 'IV': OCR mistakes a capital I for 'l' or '|' in numerals."""
    from .textnorm import roman_to_int

    candidate = token.replace("l", "I").replace("|", "I")
    return candidate if candidate != token and roman_to_int(candidate) else token


def fix_ocr_numerals(text: str) -> str:
    text = re.sub(r"\b(CHAPTER|Chapter|PART|Part|BOOK|Book|SECTION|Section|ACT|Act)\s+([IVXLCDMl|]+)(?![A-Za-z])",
                  lambda m: f"{m.group(1)} {_roman_fix(m.group(2))}", text)
    m = re.fullmatch(r"([IVXLCDMl|]+)(\.?)", text.strip())
    return _roman_fix(m.group(1)) + m.group(2) if m else text


def parse_tsv(tsv: str) -> tuple[list[Line], float]:
    """Lines from Tesseract's TSV output (words with boxes and confidence)."""
    width = height = 1.0
    boxes: dict[tuple, tuple] = {}
    words: dict[tuple, list] = {}
    order: list[tuple] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 11:
            continue
        level, block, par, line = int(cols[0]), int(cols[2]), int(cols[3]), int(cols[4])
        left, top, w, h, conf = int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9]), float(cols[10])
        text = cols[11] if len(cols) > 11 else ""
        key = (block, par, line)
        if level == 1:
            width, height = max(1, w), max(1, h)
        elif level == 4:
            boxes[key] = (left, top, left + w, top + h)
            order.append(key)
        elif level == 5 and text.strip():
            words.setdefault(key, []).append((text.strip(), conf))
    lines = []
    for key in order:
        found = words.get(key)
        if not found:
            continue
        confs = [c for _, c in found if c >= 0]
        x0, y0, x1, y1 = boxes[key]
        text = " ".join(t for t, _ in found)
        text = re.sub(r"(?<!\S)\|(?!\S)", "I", text)  # OCR reads a lone capital I as '|'
        text = re.sub(r"[{}]", "", text)  # specks read as braces
        text = fix_ocr_numerals(text)
        lines.append(Line(text, x0 / width, y0 / height, x1 / width, y1 / height,
                          key[0], key[0] * 1000 + key[1], sum(confs) / len(confs) if confs else 0.0))
    return lines, height / width


def ocr_page(pdf: Path, number: int, dpi: int, lang: str, cache_dir: Path) -> tuple[list[Line], float]:
    cache = cache_dir / f"p{number:05d}-{dpi}-{lang}-v{OCR_VERSION}.tsv"
    if cache.exists():
        return parse_tsv(cache.read_text(encoding="utf-8"))
    pdftoppm = _tool("pdftoppm", "poppler-utils")
    tesseract = _tool("tesseract", "tesseract (and the language data, e.g. tesseract-langpack-eng)")
    with tempfile.TemporaryDirectory(prefix="abgen-ocr-") as tmp:
        base = Path(tmp) / "page"
        _run([pdftoppm, "-f", str(number), "-l", str(number), "-r", str(dpi), "-gray", "-png",
              "-singlefile", str(pdf), str(base)])
        # One thread per Tesseract process: the pages already run in parallel.
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        tsv = _run([tesseract, f"{base}.png", "stdout", "-l", lang, "tsv"], env=env).decode("utf-8", "replace")
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_cache = cache.with_name(cache.name + ".tmp")
    tmp_cache.write_text(tsv, encoding="utf-8")
    tmp_cache.replace(cache)
    return parse_tsv(tsv)


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def read_pages(path: Path, ocr: str = "auto", lang: str = "eng", dpi: int = 300, workers: int | None = None,
               cache_dir: Path | None = None, progress: Callable[[int, int], None] | None = None) -> list[Page]:
    """Every page's lines, from the text layer or OCR ('auto' uses OCR where a page has no text)."""
    total = page_count(path)
    layer = text_layer(path) if ocr != "always" else []
    pages: list[Page | None] = [None] * total
    todo = []
    for i in range(total):
        lines, aspect = layer[i] if i < len(layer) else ([], 1.4)
        if ocr == "never" or (ocr == "auto" and sum(len(l.text) for l in lines) >= 60):
            pages[i] = Page(i + 1, lines, "text", aspect)
        else:
            todo.append(i + 1)
    if todo:
        cache_dir = cache_dir or Path(tempfile.gettempdir()) / "abgen-ocr"
        workers = workers or os.cpu_count() or 2
        done = 0
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(ocr_page, path, n, dpi, lang, cache_dir): n for n in todo}
            for future in as_completed(futures):
                n = futures[future]
                lines, aspect = future.result()
                pages[n - 1] = Page(n, lines, "ocr", aspect)
                done += 1
                if progress:
                    progress(done, len(todo))
    return [p for p in pages if p is not None]


# --- clean-up -------------------------------------------------------------------------------
_BREAK_LINE = re.compile(r"^[\s*·•⁂~#§=_\-–—.oO]{1,12}$")
_NUMERAL = re.compile(r"^[\[(]?(?:\d{1,4}|[ivxlcdm]{1,7})[\])]?\.?$", re.I)
_END_SENTENCE = re.compile(r"[.!?…:\"'”’)\]]$")
_CHAPTER_HEAD = re.compile(
    r"^(chapter|part|book|section|lecture|lesson|prologue|epilogue|preface|foreword|introduction|"
    r"conclusion|afterword|appendix|contents)\b", re.I)


def _key(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"^[ivxlcdm]+\b|\b[ivxlcdm]+$", "#", t)
    return " ".join(re.sub(r"[^\w#]+", " ", t).split())


def body_height(pages: list[Page]) -> float:
    heights = [l.height for p in pages for l in p.lines if len(l.text) >= 25]
    return statistics.median(heights) if heights else 0.02


EDGE_ZONE = 0.16  # running heads and page numbers are within this share of the top or bottom
_FOLIO = re.compile(r"^[\[(]?(\d{1,3})\s*[-–.]\s*(\d{1,3})[\])]?\.?$")  # '2-14': chapter 2, page 14


def _edge_lines(page: Page) -> list[Line]:
    """The two topmost and two lowest lines, if they sit near the top or bottom edge."""
    ordered = sorted(page.lines, key=lambda l: l.y0)
    picks = list({id(l): l for l in ordered[:2] + ordered[-2:]}.values())
    return [l for l in picks if l.y1 <= EDGE_ZONE or l.y0 >= 1 - EDGE_ZONE]


def remove_running_heads(pages: list[Page], folios: dict[int, tuple[int, int]] | None = None) -> int:
    """Drop page numbers, headers and footers. Returns how many lines were removed.

    Page numbers of the form 'chapter-page' ('2-14') are recorded in `folios`, because a
    page numbered 'N-1' is where chapter N begins.
    """
    h = body_height(pages)
    counts = Counter()
    for page in pages:
        counts.update({_key(l.text) for l in _edge_lines(page)})
    threshold = max(3, round(0.03 * len(pages)))
    removed = 0
    for page in pages:
        edge = set(map(id, _edge_lines(page)))
        keep = []
        for line in page.lines:
            text, key = line.text.strip(" .,:;'‘’\"|"), _key(line.text)
            folio = _FOLIO.match(text)
            if id(line) in edge and line.height <= 1.5 * h and (
                    key in ("", "#", "# #") or _NUMERAL.match(text) or folio or counts[key] >= threshold):
                if folio is not None and folios is not None:
                    folios[page.number] = (int(folio.group(1)), int(folio.group(2)))
                removed += 1
                continue
            keep.append(line)
        page.lines = keep
    return removed


def contents_titles(paras: list[Para]) -> dict[int, str]:
    """Chapter titles from a contents page whose entries end in 'N-1' page numbers:
    '1. Learn Two Mind Power Study Techniques ... 1-1' -> {1: 'Learn Two Mind Power ...'}."""
    titles: dict[int, str] = {}
    text = " ".join(p.text for p in paras)
    for m in re.finditer(r"(?<![\w-])(\d{1,2})[.,]\s+([A-Z][^•»]*?)\s*[.,\s]*\b(\d{1,2})\s*[-–]\s*1\b", text):
        number, title = int(m.group(1)), m.group(2)
        if number == int(m.group(3)) and number not in titles:
            title = re.sub(r"\s*(?:\.\s*|[e.,]\s*){2,}$", "", title)  # dot leaders
            titles[number] = " ".join(title.replace("--", " — ").split()).rstrip(" .,")
    return titles


def chapters_from_folios(paras: list[Para], folios: dict[int, tuple[int, int]]) -> list[tuple[str, list[Para]]]:
    """Chapters where 'chapter-page' numbering ('2-14') restarts at 'N-1'."""
    if sum(1 for _, n in folios.values() if n > 1) < 10:
        return []
    # Every numbered page tells where its chapter began (page - number + 1); the median of
    # those guesses is robust to the odd misread page number, and also finds chapters whose
    # first page carries no number at all.
    guesses: dict[int, list[int]] = {}
    for page, (chapter, number) in folios.items():
        guesses.setdefault(chapter, []).append(page - number + 1)
    starts_at = {c: int(statistics.median(g)) for c, g in guesses.items() if len(g) >= 2 or 1 in
                 [n for pg, (ch, n) in folios.items() if ch == c]}
    starts = sorted((page, chapter) for chapter, page in starts_at.items())
    # keep only chapters in increasing order (drops misreads like '7-1' in chapter 1)
    ordered = []
    for page, chapter in starts:
        if not ordered or (chapter > ordered[-1][1] and page > ordered[-1][0]):
            ordered.append((page, chapter))
    if len(ordered) < 3:
        return []
    first_page = ordered[0][0]
    opening = [p for p in paras if p.page < first_page]
    titles = contents_titles(opening)
    groups = []
    if opening:
        groups.append(("Opening pages", opening))
    for k, (page, chapter) in enumerate(ordered):
        end = ordered[k + 1][0] if k + 1 < len(ordered) else 10 ** 9
        group = [p for p in paras if page <= p.page < end]
        if group:
            name = f"Chapter {chapter}" + (f". {titles[chapter]}" if chapter in titles else "")
            groups.append((name, group))
    return groups


def is_junk(line: Line, source: str, h: float = 0.0) -> bool:
    """OCR noise: specks, lettering inside pictures, and scan edges.

    Measured on a scanned book: real text lines have a median confidence of about 96
    (90% above 88), while fragments read from drawings score 0-65 or are several times
    taller than the body text.
    """
    text = line.text.strip()
    if not text or _BREAK_LINE.match(text):
        return False
    if source != "ocr":
        return False
    tokens = text.split()
    if h and line.height > 4 * h and len(tokens) <= 3:
        return True  # giant 'letters' in an illustration
    letters = sum(c.isalpha() for c in text)
    if letters == 0:
        return not text.isdigit() or line.conf < 60
    if line.conf < 35 or (line.conf < 70 and len(tokens) <= 2):
        return True
    words = sum(1 for t in tokens if sum(c.isalpha() for c in t) >= 2)
    return words / len(tokens) < 0.5 and line.conf < 80


def good_heading(text: str) -> bool:
    """Looks like a real title: a word of four letters or more, starting with a capital."""
    return bool(re.search(r"[A-Za-z]{4,}", text)) and text[:1].isupper()


def _caps(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if len(letters) >= 4 else 0.0


def page_paragraphs(page: Page, h: float) -> list[Para]:
    """Group a page's lines into paragraphs by spacing, indentation and line length."""
    lines = [l for l in page.lines if not is_junk(l, page.source, h)]
    if not lines:
        return []
    long_lines = [l for l in lines if len(l.text) >= 25] or lines
    left = sorted(l.x0 for l in long_lines)[len(long_lines) // 10]
    right = sorted(l.x1 for l in long_lines)[(len(long_lines) * 9) // 10]
    em = h * page.aspect  # one line height, in units of page width
    paras: list[Para] = []
    prev = None
    for line in lines:
        new = prev is None
        if prev is not None:
            gap = line.y0 - prev.y1
            if gap > 0.9 * h or line.y0 < prev.y0 - 0.5 * h:
                new = True  # blank line, or a jump to another column / block
            elif line.x0 - left > 1.2 * em and (prev.x0 - left < 0.6 * em or _END_SENTENCE.search(prev.text.rstrip())):
                new = True  # indented first line (also after a one-line paragraph)
            elif prev.x1 < right - 4 * em and _END_SENTENCE.search(prev.text.rstrip()):
                new = True  # short last line that ends a sentence
            elif page.source == "ocr" and line.par != prev.par and gap > 0.3 * h:
                new = True
            elif (min(len(line.text), len(prev.text)) >= 8
                  and max(line.height, prev.height) > 1.35 * min(line.height, prev.height)):
                new = True  # change of type size (heading <-> text)
        if new:
            paras.append(Para([line], page.number))
        else:
            paras[-1].lines.append(line)
        prev = line
    return paras


def join_lines(lines: list[str], vocabulary: Counter | None = None) -> str:
    """Join a paragraph's lines, rejoining words split at line ends."""
    text = ""
    for line in lines:
        line = line.strip()
        if not text:
            text = line
            continue
        m = re.search(r"([A-Za-z]+)[-­¬]$", text)
        if m and line[:1].islower():
            head, tail = m.group(1), re.match(r"[A-Za-z]*", line).group(0)
            joined, hyphened = (head + tail).lower(), f"{head}-{tail}".lower()
            keep = vocabulary is not None and vocabulary[hyphened] > vocabulary[joined]
            text = text[:-1] + ("-" if keep else "") + line
        elif text.endswith(("-", "­", "—")):
            text = text.rstrip("­¬") + line
        else:
            text += " " + line
    return " ".join(text.split())


def _lead_in(text: str) -> str:
    """'IT is a singular fact' -> 'It is a singular fact' (small capitals after a drop cap)."""
    m = re.match(r"([A-Z][A-Z'’]+)(\b.*)", text)
    if m and _caps(text) < 0.3:
        return m.group(1).capitalize() + m.group(2)
    return text


def merge_drop_caps(paras: list[Para]) -> list[Para]:
    """A large first letter set apart from its word ('S' + 'ince ...', 'I' + 'T is ...')."""
    out: list[Para] = []
    for para in paras:
        prev = out[-1] if out else None
        if (prev is not None and re.fullmatch(r"[A-Z]", prev.text) and para.kind == "para"
                and (para.text[:1].islower() or re.match(r"[A-Z]+\b", para.text) and _caps(para.text) < 0.3)):
            para.text = _lead_in(prev.text + para.text)
            para.lines = prev.lines + para.lines
            out[-1] = para
            continue
        out.append(para)
    return out


def _respace(text: str, vocabulary: Counter) -> str:
    """'N o!' -> 'No!': a capital letter split from the rest of its word in the text layer."""
    def fix(m: re.Match) -> str:
        word = m.group(1) + m.group(2)
        return word if vocabulary[word.lower()] >= 2 else m.group(0)

    return re.sub(r"\b([B-HJ-Z]) ([a-z]{1,12})\b", fix, text)


def classify(paras: list[Para], h: float, vocabulary: Counter) -> None:
    for para in paras:
        para.text = _respace(join_lines([l.text for l in para.lines], vocabulary), vocabulary)
    # Compare with this page's own text size: a tilted or curved scan makes every line taller.
    local = [l.height for p in paras for l in p.lines if len(l.text) >= 25]
    h = max(h, statistics.median(local)) if local else h
    for para in paras:
        text = para.text
        words = len(text.split())
        tall = statistics.mean(l.height for l in para.lines) >= 1.4 * h and words <= 10
        para.tall = tall
        if re.fullmatch(r"[A-Z]", text):
            continue  # possibly a drop cap; decided in merge_drop_caps
        if _BREAK_LINE.match(text) and not re.search(r"[A-Za-z]{2}", text):
            para.kind = "break"
        elif len(para.lines) <= 3 and words <= 14 and not text.endswith(",") and (
                tall or _caps(text) >= 0.75 or BARE_TITLE.match(text) or
                (_CHAPTER_HEAD.match(text) and words <= 8)):
            para.kind = "heading"


def merge_across_pages(paras: list[Para], vocabulary: Counter) -> list[Para]:
    """Join a paragraph that continues on the next page, or a fragment that stops
    mid-sentence and resumes in lower case (pull-quotes, captions split by a picture)."""
    out: list[Para] = []
    for para in paras:
        if out and para.kind == "para" and out[-1].kind == "para":
            prev = out[-1].text
            unfinished = not _END_SENTENCE.search(prev)
            lower = para.text[:1].islower()
            if (para.page != out[-1].page and (unfinished or lower)) or (unfinished and lower):
                out[-1].text = join_lines([prev, para.text], vocabulary)
                out[-1].lines += para.lines
                continue
        out.append(para)
    return out


# --- chapters --------------------------------------------------------------------------------
@dataclass
class OutlineEntry:
    title: str
    page: int
    depth: int


def outline(path: Path) -> list[OutlineEntry]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    entries: list[OutlineEntry] = []

    def walk(items, depth):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            title = " ".join(str(getattr(item, "title", "") or "").split())
            entries.append(OutlineEntry(title, page, depth))

    try:
        walk(reader.outline, 0)
    except Exception:
        return []
    return entries


def choose_outline(entries: list[OutlineEntry], pages: int) -> list[OutlineEntry]:
    """Bookmarks at the deepest level whose chapters still average two pages or more."""
    best = None
    for depth in sorted({e.depth for e in entries}):
        level = {e.page for e in entries if e.depth <= depth}
        if len(level) >= 2 and pages / len(level) >= 2:
            best = depth
    chosen = [e for e in entries if best is None or e.depth <= best]
    return sorted(chosen, key=lambda e: (e.page, e.depth))


def _same(a: str, b: str) -> bool:
    simple = lambda s: " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())  # noqa: E731
    a, b = simple(a), simple(b)
    return bool(a and b) and (a == b or (len(a) > 3 and len(b) > 3 and (a in b or b in a)))


def _untitled(title: str) -> bool:
    return not title or title.lower() in {"untitled", "untitled chapter", "chapter"}


def chapters_from_outline(paras: list[Para], entries: list[OutlineEntry]) -> list[tuple[str, list[Para]]]:
    starts: list[tuple[int, str]] = []
    by_page: dict[int, list[str]] = {}
    for entry in entries:
        by_page.setdefault(entry.page, []).append(entry.title)
    for page, titles in sorted(by_page.items()):
        title = next((t for t in reversed(titles) if not _untitled(t)), "")
        index = next((i for i, p in enumerate(paras) if p.page >= page), len(paras))
        # Start at the heading that matches the bookmark (or, for an 'Untitled' bookmark,
        # the first real heading) if it's further down the page.
        for j in range(index, len(paras)):
            if paras[j].page != page:
                break
            if paras[j].kind == "heading" and (_same(paras[j].text, title) if title else good_heading(paras[j].text)):
                index, title = j, title or paras[j].text
                # keep headings printed just above it ('CHAPTER I' over 'THE BEGINNING')
                while index > 0 and paras[index - 1].kind == "heading" and paras[index - 1].page == page:
                    index -= 1
                break
        title = title or "Untitled"
        if starts and index <= starts[-1][0]:
            continue
        starts.append((index, title))
    groups = []
    if starts and starts[0][0] > 0:
        groups.append(("Opening pages", paras[:starts[0][0]]))
    for k, (index, title) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(paras)
        groups.append((title, paras[index:end]))
    return groups


def _chapter_mark(p: Para) -> bool:
    """'CHAPTER IV', 'Prologue', 'IV.' - or a bare number, if it's printed large."""
    if p.kind != "heading" or not p.text[:1].isupper() and not p.text[:1].isdigit():
        return False
    if _CHAPTER_HEAD.match(p.text):
        return True
    m = BARE_TITLE.match(p.text)
    return bool(m) and (not m.group(1).isdigit() or (p.tall and len(m.group(1)) <= 3))


def chapters_from_headings(paras: list[Para]) -> list[tuple[str, list[Para]]]:
    starts = [i for i, p in enumerate(paras)
              if _chapter_mark(p) and (i == 0 or not _chapter_mark(paras[i - 1]))]
    if len(starts) < 2:
        # No "Chapter ..." headings: use every heading that opens a page section, if sensible.
        starts = [i for i, p in enumerate(paras) if p.kind == "heading" and (i == 0 or paras[i - 1].kind != "heading")]
        if not 3 <= len(starts) <= max(3, len(paras) // 3):
            return []
    groups = []
    if starts[0] > 0:
        groups.append(("Opening pages", paras[:starts[0]]))
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(paras)
        heads = []
        for p in paras[start:end]:
            if p.kind != "heading" or len(heads) >= 2:
                break
            heads.append(p.text)
        groups.append((". ".join(h.rstrip(".") for h in heads), paras[start:end]))
    return groups


def tidy_ends(groups: list[tuple[str, list[Para]]], minimum: int = 400) -> list[tuple[str, list[Para]]]:
    """Fold the small sections before the first real chapter (title page, copyright,
    contents) into 'Opening pages', and those after the last one into 'Closing pages'."""
    words = [sum(len(p.text.split()) for p in g) for _, g in groups]
    big = [i for i, w in enumerate(words) if w >= minimum]
    if not big:
        return groups
    first, last = big[0], big[-1]
    out = []
    if first > 0:
        out.append(("Opening pages", [p for _, g in groups[:first] for p in g]))
    out += groups[first:last + 1]
    if last < len(groups) - 1:
        out.append(("Closing pages", [p for _, g in groups[last + 1:] for p in g]))
    return out


def chapters_by_pages(paras: list[Para], size: int = 20) -> list[tuple[str, list[Para]]]:
    groups, current, first = [], [], None
    for para in paras:
        if first is None:
            first = para.page
        if para.page - first >= size and para.kind != "para" or (para.page - first >= size + 5):
            groups.append((f"Pages {first}–{current[-1].page}", current))
            current, first = [], para.page
        current.append(para)
    if current:
        groups.append((f"Pages {first}–{current[-1].page}", current))
    return groups


def _balanced(groups: list[tuple[str, list[Para]]], pages: int) -> bool:
    """Chapters that make sense: at least two, and none holding most of a long book."""
    if len(groups) < 2:
        return False
    words = [sum(len(p.text.split()) for p in g) for _, g in groups]
    return pages < 20 or max(words) <= 0.6 * sum(words)


def _vocabulary(pages: list[Page]) -> Counter:
    """Words as they appear inside lines (not at line ends), to tell real hyphens from split words."""
    vocab = Counter()
    for page in pages:
        for line in page.lines:
            for word in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", line.text)[:-1]:
                vocab[word.lower()] += 1
    return vocab


def _metadata(path: Path) -> tuple[str, list[str]]:
    from pypdf import PdfReader

    meta = PdfReader(str(path)).metadata or {}
    title = " ".join(str(meta.get("/Title") or "").split())
    if not title or title.lower() in {"untitled", "microsoft word"} or title.lower().endswith((".doc", ".pdf")):
        stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", path.stem)  # TheTimeMachine -> The Time Machine
        title = " ".join(re.sub(r"[_\-]+", " ", stem).split()).title()
    author = " ".join(str(meta.get("/Author") or "").split())
    authors = [a.strip() for a in re.split(r";|,(?= *[A-Z])| and | & ", author) if a.strip()] if author else []
    return title, authors


def cover_image(path: Path) -> bytes | None:
    with tempfile.TemporaryDirectory(prefix="abgen-cover-") as tmp:
        base = Path(tmp) / "cover"
        try:
            _run([_tool("pdftoppm", "poppler-utils"), "-f", "1", "-l", "1", "-jpeg", "-jpegopt", "quality=85",
                  "-scale-to", "1200", "-singlefile", str(path), str(base)])
            return (base.with_suffix(".jpg")).read_bytes()
        except (RuntimeError, OSError):
            return None


@dataclass
class PdfReport:
    pages: int = 0
    text_pages: int = 0
    ocr_pages: int = 0
    removed_lines: int = 0
    chapter_source: str = ""


def read_pdf(path: str | Path, ocr: str = "auto", lang: str = "eng", dpi: int = 300,
             workers: int | None = None, cache_dir: Path | None = None,
             progress: Callable[[int, int], None] | None = None, report: PdfReport | None = None,
             title: str | None = None, author: str | None = None) -> Book:
    path = Path(path)
    pages = read_pages(path, ocr, lang, dpi, workers, cache_dir, progress)
    h = body_height(pages)
    folios: dict[int, tuple[int, int]] = {}
    removed = remove_running_heads(pages, folios)
    vocabulary = _vocabulary(pages)
    paras: list[Para] = []
    for page in pages:
        page_paras = page_paragraphs(page, h)
        classify(page_paras, h, vocabulary)
        page_paras = merge_drop_caps(page_paras)
        for para in page_paras:
            if re.fullmatch(r"[A-Z]", para.text):  # a lone letter that wasn't a drop cap
                para.kind = "heading" if para.tall else "para"
        paras += [p for p in page_paras if p.text]

    entries = choose_outline(outline(path), len(pages))
    groups = chapters_from_outline(paras, entries) if len(entries) >= 2 else []
    source = "bookmarks"
    if not groups:
        groups, source = chapters_from_folios(paras, folios), "page numbering"
    if not _balanced(groups, len(pages)):
        groups, source = tidy_ends(chapters_from_headings(paras)), "headings"
    if not _balanced(groups, len(pages)):
        groups, source = chapters_by_pages(paras), "page ranges"

    chapters = []
    for name, group in groups:
        group = merge_across_pages(group, vocabulary)
        blocks = [Block(p.kind, p.text if p.kind != "break" else "") for p in group]
        if _untitled(name):
            first = next((b.text for b in blocks if b.kind == "heading" and good_heading(b.text)), "")
            name = first or (f"Pages {group[0].page}–{group[-1].page}" if group else "Untitled")
        chapter = Chapter(display_title(name), blocks, str(group[0].page) if group else "",
                          announce=not name.startswith("Pages "))
        if any(b.kind != "break" for b in chapter.blocks):
            chapters.append(chapter)

    meta_title, authors = _metadata(path)
    title = title or meta_title
    if author:
        authors = [a.strip() for a in re.split(r";| and | & ", author) if a.strip()]
    if report is not None:
        report.pages = len(pages)
        report.text_pages = sum(p.source == "text" for p in pages)
        report.ocr_pages = sum(p.source == "ocr" for p in pages)
        report.removed_lines = removed
        report.chapter_source = source
    language = TESSERACT_LANGS.get(lang.split("+")[0], "en")
    cover = cover_image(path)
    return Book(title, authors, language, chapters, cover, "image/jpeg" if cover else None)


def progress_printer(stream=sys.stderr) -> Callable[[int, int], None]:
    tty = stream.isatty()

    def show(done: int, total: int) -> None:
        if tty:
            print(f"\rOCR: page {done}/{total}", end="" if done < total else "\n", file=stream, flush=True)
        elif done == total or done % 25 == 0:
            print(f"OCR: page {done}/{total}", file=stream, flush=True)

    return show
