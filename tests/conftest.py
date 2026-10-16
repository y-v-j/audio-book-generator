import zipfile
from pathlib import Path

import pytest

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>x</title><style>p {{ margin: 0 }}</style></head><body>{body}</body></html>"""


def make_epub(path: Path, files: dict[str, str], toc: list[tuple[str, str]], ncx: bool = False,
              cover: bytes | None = None, title: str = "The Test Book", author: str = "A. N. Author",
              extra: dict[str, str] | None = None) -> Path:
    """A minimal EPUB: `files` maps names to body HTML, `toc` lists (href, title), `extra` adds raw files."""
    manifest = "".join(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
                       for i, name in enumerate(files))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(files)))
    if ncx:
        manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        spine_attr = ' toc="ncx"'
    else:
        manifest += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        spine_attr = ""
    if cover:
        manifest += '<item id="cov" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:language>en</dc:language>
  </metadata>
  <manifest>{manifest}</manifest>
  <spine{spine_attr}>{spine}</spine>
</package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        for name, body in files.items():
            z.writestr(f"OEBPS/{name}", XHTML.format(body=body))
        if ncx:
            points = "".join(f'<navPoint id="n{i}"><navLabel><text>{t}</text></navLabel><content src="{h}"/></navPoint>'
                             for i, (h, t) in enumerate(toc))
            z.writestr("OEBPS/toc.ncx", f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{points}</navMap></ncx>')
        else:
            items = "".join(f'<li><a href="{h}">{t}</a></li>' for h, t in toc)
            z.writestr("OEBPS/nav.xhtml", XHTML.format(body=f'<nav epub:type="toc"><ol>{items}</ol></nav>'))
        if cover:
            z.writestr("OEBPS/images/cover.jpg", cover)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


@pytest.fixture
def epub_factory(tmp_path):
    counter = iter(range(1000))

    def factory(files, toc, **kwargs):
        return make_epub(tmp_path / f"book{next(counter)}.epub", files, toc, **kwargs)

    return factory
