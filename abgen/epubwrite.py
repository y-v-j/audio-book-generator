"""Write a Book as an EPUB 3 file (with an EPUB 2 table of contents for older readers)."""

from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import __version__
from .epub import Book, Chapter

STYLE = """\
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2 { font-family: sans-serif; line-height: 1.25; text-align: center; page-break-after: avoid; }
h1 { font-size: 1.5em; margin: 2.5em 0 1.2em; }
h2 { font-size: 1.15em; margin: 1.8em 0 0.8em; }
p { margin: 0; text-indent: 1.4em; text-align: justify; hyphens: auto; }
h1 + p, h2 + p, hr + p, p.first { text-indent: 0; }
hr.scene { border: none; margin: 1.4em 0; text-align: center; }
hr.scene::after { content: "* * *"; letter-spacing: 0.5em; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body{body_attr}>
{body}
</body>
</html>
"""


def _chapter_body(chapter: Chapter) -> str:
    out = ['<section epub:type="chapter" role="doc-chapter">']
    blocks = chapter.blocks
    lead = 0
    while lead < len(blocks) and blocks[lead].kind == "heading":
        lead += 1
    if lead == 0 and chapter.announce:
        out.append(f"<h1>{escape(chapter.title)}</h1>")
    for i, block in enumerate(blocks):
        if block.kind == "heading":
            tag = "h1" if i == 0 else "h2"
            out.append(f"<{tag}>{escape(block.text)}</{tag}>")
        elif block.kind == "break":
            out.append('<hr class="scene"/>')
        else:
            first = ' class="first"' if i == 0 and lead == 0 and not chapter.announce else ""
            out.append(f"<p{first}>{escape(block.text)}</p>")
    out.append("</section>")
    return "\n".join(out)


def write_epub(book: Book, path: Path, numbers: list[int] | None = None) -> Path:
    """Write the chosen chapters (1-based numbers, default all) of `book` to `path`."""
    numbers = numbers or list(range(1, len(book.chapters) + 1))
    chapters = [(n, book.chapters[n - 1]) for n in numbers]
    lang = (book.language or "en").split("-")[0]
    book_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"abgen:{book.title}:{book.author}")
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cover_ext = {"image/jpeg": "jpg", "image/png": "png"}.get(book.cover_type or "")
    has_cover = bool(book.cover and cover_ext)

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    if has_cover:
        manifest += [f'<item id="cover-image" href="images/cover.{cover_ext}" media-type="{book.cover_type}" properties="cover-image"/>',
                     '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>']
        spine.append('<itemref idref="cover"/>')
    files = []
    for k, (n, chapter) in enumerate(chapters, 1):
        name = f"chapter-{k:03d}.xhtml"
        manifest.append(f'<item id="c{k:03d}" href="text/{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{k:03d}"/>')
        files.append((name, chapter))

    title, author = escape(book.title), escape(book.author)
    creators = "".join(f'<dc:creator id="creator{i}">{escape(a)}</dc:creator>' for i, a in enumerate(book.authors, 1))
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{book_uuid}</dc:identifier>
    <dc:title>{title}</dc:title>
    {creators}
    <dc:language>{lang}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
    <meta name="generator" content="audio-book-generator {__version__}"/>
    {'<meta name="cover" content="cover-image"/>' if has_cover else ''}
  </metadata>
  <manifest>
    {chr(10).join('    ' + m for m in manifest).strip()}
  </manifest>
  <spine toc="ncx">
    {chr(10).join('    ' + s for s in spine).strip()}
  </spine>
</package>
"""
    nav_items = "\n".join(f'      <li><a href="text/{name}">{escape(ch.title)}</a></li>' for name, ch in files)
    landmarks = "\n".join(
        ([ '      <li><a epub:type="cover" href="text/cover.xhtml">Cover</a></li>'] if has_cover else []) +
        ([f'      <li><a epub:type="bodymatter" href="text/{files[0][0]}">Start of book</a></li>'] if files else []))
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head><meta charset="utf-8"/><title>Contents</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
  <nav epub:type="toc" id="toc" role="doc-toc">
    <h1>Contents</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol>
{landmarks}
    </ol>
  </nav>
</body>
</html>
"""
    points = "\n".join(
        f'    <navPoint id="np{k}" playOrder="{k}"><navLabel><text>{escape(ch.title)}</text></navLabel>'
        f'<content src="text/{name}"/></navPoint>' for k, (name, ch) in enumerate(files, 1))
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{lang}">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{title}</text></docTitle>
  <docAuthor><text>{author}</text></docAuthor>
  <navMap>
{points}
  </navMap>
</ncx>
"""
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        deflate = {"compress_type": zipfile.ZIP_DEFLATED}
        z.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""", **deflate)
        z.writestr("OEBPS/content.opf", opf, **deflate)
        z.writestr("OEBPS/nav.xhtml", nav, **deflate)
        z.writestr("OEBPS/toc.ncx", ncx, **deflate)
        z.writestr("OEBPS/style.css", STYLE, **deflate)
        if has_cover:
            z.writestr(f"OEBPS/images/cover.{cover_ext}", book.cover)
            body = f'<div class="cover"><img src="../images/cover.{cover_ext}" alt="{title}"/></div>'
            z.writestr("OEBPS/text/cover.xhtml",
                       XHTML.format(lang=lang, title=title, body_attr=' class="cover"', body=body), **deflate)
        for name, chapter in files:
            z.writestr(f"OEBPS/text/{name}", XHTML.format(lang=lang, title=escape(chapter.title), body_attr="",
                                                          body=_chapter_body(chapter)), **deflate)
    tmp.replace(path)
    return path
