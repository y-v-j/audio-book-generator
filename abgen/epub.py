"""Read an EPUB into chapters of speakable blocks: headings, paragraphs and scene breaks.

Chapters follow the book's table of contents. A TOC entry that points into the middle of
a file (#anchor) starts a new chapter at that point; a file with no TOC entry continues
the previous chapter, unless it opens with a heading.
"""

from __future__ import annotations

import posixpath
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag


def _html(data: bytes) -> BeautifulSoup:
    """Parse XHTML leniently (the HTML parser copes with entities like &nbsp;)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # bs4 warns that XHTML "looks like XML"
        return BeautifulSoup(data, "lxml")


@dataclass
class Block:
    kind: str  # "heading", "para" or "break"
    text: str = ""


@dataclass
class Chapter:
    title: str
    blocks: list[Block] = field(default_factory=list)
    source: str = ""
    announce: bool = True  # read the title aloud when the text has no heading of its own

    @property
    def words(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks)


@dataclass
class Book:
    title: str
    authors: list[str]
    language: str
    chapters: list[Chapter]
    cover: bytes | None = None
    cover_type: str | None = None

    @property
    def author(self) -> str:
        if not self.authors:
            return "Unknown author"
        if len(self.authors) == 1:
            return self.authors[0]
        return ", ".join(self.authors[:-1]) + " and " + self.authors[-1]


HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = HEADINGS | {"p", "li", "blockquote", "dd", "dt", "pre", "div", "section", "article",
                     "header", "footer", "main", "aside", "ul", "ol", "dl", "figure", "table", "hr"}
SKIP = {"script", "style", "head", "svg", "img", "figure", "table", "nav", "audio", "video",
        "object", "math", "map", "iframe", "noscript", "title"}
_NOTE_TYPES = re.compile(r"\b(noteref|footnote|endnote|rearnote|pagebreak)\b")
_PAGE_CLASS = re.compile(r"page[-_]?(num|no|break|number)|pagenum|^pg$", re.I)
_BREAK_TEXT = re.compile(r"^[\s*·•⁂~#_=+\-–—.]+$")
_BREAK_CLASS = re.compile(r"scene[-_]?break|section[-_]?break|transition|space[-_]?break", re.I)
_ENDS_CLAUSE = re.compile(r"[.,;:!?—…\"'”’)\]]$")


def _attr_text(tag: Tag, name: str) -> str:
    value = tag.get(name, "")
    return " ".join(value) if isinstance(value, list) else str(value)


def _is_noise(tag: Tag) -> bool:
    """Footnote markers, footnotes, page numbers and similar non-narrative bits."""
    if tag.name in SKIP:
        return True
    if _NOTE_TYPES.search(_attr_text(tag, "epub:type")) or _NOTE_TYPES.search(_attr_text(tag, "role")):
        return True
    if _PAGE_CLASS.search(_attr_text(tag, "class")):
        return True
    if tag.name == "sup":
        # Superscript numbers or links are note markers; keep anything else ("1st").
        text = tag.get_text().strip()
        return bool(tag.find("a")) or text.isdigit() or text in {"*", "†", "‡"}
    return False


_LINE_BREAK = " "  # marks <br>; ordinary newlines in the source are just spaces


def _clean_text(tag: Tag) -> str:
    for br in tag.find_all("br"):
        br.replace_with(_LINE_BREAK)
    lines = [" ".join(line.split()) for line in tag.get_text().split(_LINE_BREAK)]
    lines = [line for line in lines if line]
    # Line breaks (verse, letters, addresses) become short pauses.
    out = []
    for i, line in enumerate(lines):
        if i < len(lines) - 1 and not _ENDS_CLAUSE.search(line):
            line += ","
        out.append(line)
    return " ".join(out)


_INNER_BLOCKS = sorted(BLOCKS - {"hr"})


def _is_leaf_block(tag: Tag) -> bool:
    return tag.name in BLOCKS and tag.name != "hr" and tag.find(_INNER_BLOCKS) is None


def _block_for(tag: Tag) -> Block | None:
    text = _clean_text(tag)
    if _BREAK_CLASS.search(_attr_text(tag, "class")) and not re.search(r"\w", text):
        return Block("break")
    if not text:
        return None
    if not re.search(r"\w", text):
        return Block("break") if _BREAK_TEXT.match(text) else None
    if tag.name in HEADINGS or _attr_text(tag, "role") == "heading":
        return Block("heading", text)
    return Block("para", text)


def iter_content(body: Tag):
    """Yield ('anchor', id) and ('block', Block) events in reading order."""
    pending: list[str] = []

    def flush():
        text = " ".join(" ".join(pending).split())
        pending.clear()
        if re.search(r"\w", text):
            return ("block", Block("para", text))
        return None

    def walk(node: Tag):
        for child in list(node.children):
            if isinstance(child, NavigableString):
                if type(child) is NavigableString and child.strip():
                    pending.append(str(child))
                continue
            if not isinstance(child, Tag) or _is_noise(child):
                continue
            if child.name in BLOCKS:
                event = flush()
                if event:
                    yield event
            if child.get("id"):
                yield ("anchor", child["id"])
            if child.name == "hr":
                yield ("block", Block("break"))
            elif _is_leaf_block(child):
                for noise in [d for d in child.find_all(True) if _is_noise(d)]:
                    noise.decompose()
                for inner in child.find_all(id=True):
                    yield ("anchor", inner["id"])
                block = _block_for(child)
                if block:
                    yield ("block", block)
            elif child.name in BLOCKS:
                yield from walk(child)
            else:
                # Inline element outside any paragraph: collect it as loose text.
                for inner in child.find_all(id=True):
                    yield ("anchor", inner["id"])
                for noise in [d for d in child.find_all(True) if _is_noise(d)]:
                    noise.decompose()
                pending.append(child.get_text())
        event = flush()
        if event:
            yield event

    yield from walk(body)


# Ordinary DRM-free EPUBs may "encrypt" (obfuscate) embedded fonts; any other encryption is DRM.
_FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
_DRM_FILES = ("META-INF/rights.xml", "META-INF/license.lcpl", "META-INF/sinf.xml")  # Adobe, Readium LCP, Apple


class _Epub:
    def __init__(self, path: str):
        self.zip = zipfile.ZipFile(path)
        self._refuse_drm()
        container = self._xml("META-INF/container.xml")
        rootfile = container.find("rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("not an EPUB: META-INF/container.xml has no rootfile")
        self.opf_path = rootfile["full-path"]
        self.opf_dir = posixpath.dirname(self.opf_path)
        self.opf = self._xml(self.opf_path)

    def _refuse_drm(self) -> None:
        names = set(self.zip.namelist())
        drm = any(name in names for name in _DRM_FILES)
        if not drm and "META-INF/encryption.xml" in names:
            methods = self._xml("META-INF/encryption.xml").find_all("EncryptionMethod")
            drm = any(m.get("Algorithm") not in _FONT_OBFUSCATION for m in methods)
        if drm:
            raise ValueError("this EPUB is DRM-protected; only DRM-free EPUBs can be read")

    def _read(self, path: str) -> bytes:
        return self.zip.read(path)

    def _xml(self, path: str) -> BeautifulSoup:
        return BeautifulSoup(self._read(path), "lxml-xml")

    def resolve(self, href: str, base_dir: str) -> tuple[str, str | None]:
        href, _, frag = href.partition("#")
        path = posixpath.normpath(posixpath.join(base_dir, unquote(href))) if href else ""
        return path, (unquote(frag) or None)

    def metadata(self) -> tuple[str, list[str], str]:
        def texts(name):
            return [" ".join(t.get_text().split()) for t in self.opf.find_all(name) if t.get_text().strip()]

        titles = texts("title")
        return (titles[0] if titles else "Untitled", texts("creator"), (texts("language") or ["en"])[0])

    def manifest(self) -> dict[str, dict]:
        items = {}
        for item in self.opf.find_all("item"):
            if item.get("id") and item.get("href"):
                path, _ = self.resolve(item["href"], self.opf_dir)
                items[item["id"]] = {"path": path, "type": item.get("media-type", ""),
                                     "props": item.get("properties", "")}
        return items

    def spine(self, manifest: dict) -> list[str]:
        paths = []
        spine = self.opf.find("spine")
        for ref in spine.find_all("itemref") if spine else []:
            item = manifest.get(ref.get("idref", ""))
            if item and ref.get("linear", "yes") != "no" and "html" in item["type"]:
                paths.append(item["path"])
        return paths

    def cover(self, manifest: dict) -> tuple[bytes | None, str | None]:
        candidates = [i for i in manifest.values() if "cover-image" in i["props"]]
        meta = self.opf.find("meta", attrs={"name": "cover"})
        if meta is not None and meta.get("content") in manifest:
            candidates.append(manifest[meta["content"]])
        candidates += [i for i in manifest.values()
                       if i["type"].startswith("image/") and "cover" in i["path"].lower()]
        for item in candidates:
            if item["type"].startswith("image/"):
                try:
                    return self._read(item["path"]), item["type"]
                except KeyError:
                    continue
        return None, None

    def toc(self, manifest: dict) -> list[TocEntry]:
        """Table of contents in reading order, with nesting."""
        entries: list[TocEntry] = []
        nav = next((i for i in manifest.values() if "nav" in i["props"].split()), None)
        if nav:
            soup = _html(self._read(nav["path"]))
            navs = soup.find_all("nav")
            toc_nav = next((n for n in navs if "toc" in _attr_text(n, "epub:type")), navs[0] if navs else None)
            base = posixpath.dirname(nav["path"])

            def walk_list(ol: Tag, depth: int, parent: int | None) -> None:
                for li in ol.find_all("li", recursive=False):
                    link = next((a for a in li.find_all("a", href=True) if a.find_parent("li") is li), None)
                    index = parent
                    title = " ".join(link.get_text().split()) if link else ""
                    if link and title:
                        entries.append(TocEntry(*self.resolve(link["href"], base), title, depth, parent))
                        index = len(entries) - 1
                    for sub in li.find_all(["ol", "ul"], recursive=False):
                        walk_list(sub, depth + 1, index)

            top = toc_nav.find(["ol", "ul"]) if toc_nav else None
            if top is not None:
                walk_list(top, 0, None)
        if not entries:
            spine = self.opf.find("spine")
            ncx_id = spine.get("toc") if spine else None
            ncx = manifest.get(ncx_id) if ncx_id else None
            ncx = ncx or next((i for i in manifest.values() if i["type"] == "application/x-dtbncx+xml"), None)
            if ncx:
                soup = self._xml(ncx["path"])
                base = posixpath.dirname(ncx["path"])

                def walk_points(node: Tag, depth: int, parent: int | None) -> None:
                    for point in node.find_all("navPoint", recursive=False):
                        label, content = point.find("navLabel"), point.find("content")
                        index = parent
                        if label is not None and content is not None and content.get("src"):
                            title = " ".join(label.get_text().split())
                            if title:
                                entries.append(TocEntry(*self.resolve(content["src"], base), title, depth, parent))
                                index = len(entries) - 1
                        walk_points(point, depth + 1, index)

                nav_map = soup.find("navMap")
                if nav_map is not None:
                    walk_points(nav_map, 0, None)
        return entries


@dataclass
class TocEntry:
    path: str
    fragment: str | None
    title: str
    depth: int
    parent: int | None  # index of the parent entry


_SMALL_WORDS = {"a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet", "as", "at", "by",
                "in", "of", "off", "on", "per", "to", "up", "via", "with", "from", "into", "over"}
BARE_TITLE = re.compile(r"^\s*([IVXLCDM]+|\d+)\s*[.:]?\s*$", re.I)
MERGE_WORDS = 15000  # parts of one story are kept together up to about an hour and a half


def display_title(title: str) -> str:
    """'II. THE RED-HEADED LEAGUE' -> 'II. The Red-Headed League' for chapter lists."""
    from .textnorm import roman_to_int

    title = " ".join(title.replace("™", "").split())
    letters = [c for c in title if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.6:
        return title
    words = title.split(" ")
    out = []
    for i, word in enumerate(words):
        core = word.strip(".,:;!?()\"'“”‘’")
        if core and roman_to_int(core.upper()) and core.isupper() and core != "I" or core == "I" and i == 0:
            out.append(word)
        elif i > 0 and core.lower() in _SMALL_WORDS and not out[-1].endswith((".", ":")):
            out.append(word.lower())
        else:
            out.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
    return " ".join(out)


def _same_text(a: str, b: str) -> bool:
    def simple(s):
        return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())

    return simple(a) == simple(b)


def read_epub(path: str) -> Book:
    book = _Epub(path)
    title, authors, language = book.metadata()
    manifest = book.manifest()
    spine = book.spine(manifest)
    toc = book.toc(manifest)
    nav_paths = {i["path"] for i in manifest.values() if "nav" in i["props"].split()}

    doc_entries: dict[str, int] = {}
    anchor_entries: dict[str, dict[str, int]] = {}
    for index, entry in enumerate(toc):
        if entry.fragment:
            anchor_entries.setdefault(entry.path, {}).setdefault(entry.fragment, index)
        else:
            doc_entries.setdefault(entry.path, index)

    chapters: list[Chapter] = []
    entry_of: dict[int, int] = {}  # id(chapter) -> TOC entry index

    def start(name: str, source: str, entry: int | None = None) -> None:
        chapters.append(Chapter(name, [], source))
        if entry is not None:
            entry_of[id(chapters[-1])] = entry

    for doc in spine:
        if doc in nav_paths:
            continue
        try:
            soup = _html(book._read(doc))
        except KeyError:
            continue
        events = list(iter_content(soup.body or soup))
        anchors = dict(anchor_entries.get(doc, {}))
        if doc in doc_entries:
            start(toc[doc_entries[doc]].title, doc, doc_entries[doc])
        elif not anchors:
            first = next((e[1] for e in events if e[0] == "block"), None)
            if first is None:
                continue
            if first.kind == "heading" or not chapters:
                start(first.text if first.kind == "heading" else title, doc)
        for kind, value in events:
            if kind == "anchor":
                if value in anchors:
                    entry = anchors.pop(value)
                    start(toc[entry].title, doc, entry)
                continue
            if not chapters:
                start(title, doc)
            chapters[-1].blocks.append(value)

    chapters = _group_parts(chapters, toc, entry_of)
    chapters = _fold_heading_only(chapters, title, authors)
    for chapter in chapters:
        while chapter.blocks and chapter.blocks[0].kind == "break":
            chapter.blocks.pop(0)
        while chapter.blocks and chapter.blocks[-1].kind == "break":
            chapter.blocks.pop()
        chapter.title = display_title(chapter.title)
    cover, cover_type = book.cover(manifest)
    return Book(title, authors, language, chapters, cover, cover_type)


def _group_parts(chapters: list[Chapter], toc: list[TocEntry], entry_of: dict[int, int]) -> list[Chapter]:
    """Sub-entries titled only 'I.', 'II.' ... belong to their parent.

    A short story in parts ('A Scandal in Bohemia' > I., II., III.) becomes one chapter with
    the parts read as sections. When the parts are long (a novel's 'Part One' > 1, 2, 3 ...),
    they stay separate chapters but get a full title, such as 'Part One — 1'.
    """
    position = {entry_of[id(c)]: i for i, c in enumerate(chapters) if id(c) in entry_of}
    children: dict[int, list[int]] = {}
    for i, chapter in enumerate(chapters):
        entry = entry_of.get(id(chapter))
        if entry is not None and toc[entry].parent is not None:
            children.setdefault(toc[entry].parent, []).append(i)
    remove: set[int] = set()
    for parent, kids in children.items():
        if not all(BARE_TITLE.match(chapters[k].title) for k in kids):
            continue
        parent_pos = position.get(parent)
        group = ([parent_pos] if parent_pos is not None else []) + kids
        contiguous = group == list(range(group[0], group[0] + len(group)))
        if parent_pos is not None and contiguous and sum(chapters[g].words for g in group) <= MERGE_WORDS:
            for k in kids:
                kid = chapters[k]
                if not kid.blocks or kid.blocks[0].kind != "heading":
                    kid.blocks.insert(0, Block("heading", kid.title))
                chapters[parent_pos].blocks += kid.blocks
                remove.add(k)
        else:
            for k in kids:
                chapters[k].title = f"{toc[parent].title} — {chapters[k].title.rstrip('.')}"
    return [c for i, c in enumerate(chapters) if i not in remove]


def _fold_heading_only(chapters: list[Chapter], title: str, authors: list[str]) -> list[Chapter]:
    """A chapter that is only a heading ('Part One') is read at the start of the next one.

    Title-page headings that repeat the book title or author are dropped (the narration
    announces them anyway), and so are empty 'Contents' pages.
    """
    def redundant(text: str) -> bool:
        return _same_text(text, title) or any(_same_text(text, f"by {a}") or _same_text(text, a) for a in authors)

    merged: list[Chapter] = []
    carry: list[Block] = []
    for chapter in chapters:
        if not any(b.kind != "break" for b in chapter.blocks):
            continue
        if all(b.kind in ("heading", "break") for b in chapter.blocks):
            if _SKIP_TITLES.search(chapter.title.strip()):
                continue
            headings = [b for b in chapter.blocks if b.kind == "heading"]
            carry += [b for b in headings if not redundant(b.text)]
            continue
        chapter.blocks = carry + chapter.blocks
        carry = []
        merged.append(chapter)
    if carry:  # a trailing heading-only chapter: keep it rather than lose the text
        merged.append(Chapter(carry[0].text, carry))
    return merged


# Chapters an audiobook normally leaves out. Users can still pick them with --chapters.
_SKIP_TITLES = re.compile(
    r"^(table of )?contents\b|^list of (illustrations|figures|plates|tables)\b|^copyright|^colophon$|"
    r"^index$|^also by\b|"
    r"project gutenberg|^(the )?full license", re.I)


def skipped_by_default(chapter: Chapter) -> bool:
    if _SKIP_TITLES.search(chapter.title.strip()):
        return True
    if chapter.title in ("Opening pages", "Closing pages") and chapter.words < 800:  # a PDF's title pages
        return True
    opening = " ".join(b.text for b in chapter.blocks[:3])
    return "Project Gutenberg" in opening and chapter.words < 2500
