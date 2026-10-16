"""A tiny PDF writer for tests: Helvetica text at given positions, optional bookmarks."""

from __future__ import annotations

from pathlib import Path

WIDTH, HEIGHT = 420, 600


def _escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def make_pdf(path: Path, pages: list[list[tuple[float, float, float, str]]],
             title: str | None = None, author: str | None = None,
             bookmarks: list[tuple[str, int]] | None = None) -> Path:
    """`pages`: per page, (x, y from the top, font size, text) for each line.
    `bookmarks`: (title, 1-based page) entries."""
    objects: dict[int, bytes] = {}
    n_pages = len(pages)
    first_page = 4
    for i, lines in enumerate(pages):
        page_id, content_id = first_page + 2 * i, first_page + 2 * i + 1
        stream = b"".join(
            b"BT /F1 %.1f Tf %.1f %.1f Td (" % (size, x, HEIGHT - y - size) + _escape(text) + b") Tj ET\n"
            for x, y, size, text in lines)
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objects[page_id] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (WIDTH, HEIGHT, content_id))
    next_id = first_page + 2 * n_pages
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(n_pages))
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages)
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if bookmarks:
        outlines_id = next_id
        item_ids = list(range(next_id + 1, next_id + 1 + len(bookmarks)))
        next_id += 1 + len(bookmarks)
        objects[outlines_id] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (
            item_ids[0], item_ids[-1], len(bookmarks))
        for k, ((name, page), oid) in enumerate(zip(bookmarks, item_ids)):
            links = b""
            if k > 0:
                links += b" /Prev %d 0 R" % item_ids[k - 1]
            if k < len(item_ids) - 1:
                links += b" /Next %d 0 R" % item_ids[k + 1]
            objects[oid] = (b"<< /Title (" + _escape(name) + b") /Parent %d 0 R%s /Dest [%d 0 R /XYZ 0 %d 0] >>"
                            % (outlines_id, links, first_page + 2 * (page - 1), HEIGHT))
        catalog += b" /Outlines %d 0 R" % outlines_id
    objects[1] = catalog + b" >>"
    info_id = None
    if title or author:
        info_id = next_id
        fields = b""
        if title:
            fields += b" /Title (" + _escape(title) + b")"
        if author:
            fields += b" /Author (" + _escape(author) + b")"
        objects[info_id] = b"<<" + fields + b" >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + objects[oid] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for oid in range(1, size):
        out += b"%010d 00000 n \n" % offsets[oid] if oid in offsets else b"0000000000 65535 f \n"
    trailer = b"trailer\n<< /Size %d /Root 1 0 R" % size
    if info_id:
        trailer += b" /Info %d 0 R" % info_id
    out += trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    Path(path).write_bytes(bytes(out))
    return Path(path)


def sample_book_pages() -> list[list[tuple[float, float, float, str]]]:
    """Four pages with a running header, page numbers, two chapters, a hyphenated word
    and a paragraph that continues onto the next page."""
    def page(n, body):
        return [(40, 20, 9, "THE TEST BOOK"), *body, (200, 570, 9, str(n))]

    return [
        page(1, [(40, 90, 18, "CHAPTER I"), (40, 116, 18, "THE BEGINNING"),
                 (40, 160, 11, "It was a cold morning when the begin-"),
                 (40, 175, 11, "ning of the story took place in the old"),
                 (40, 190, 11, "town by the river."),
                 (60, 205, 11, "Nobody expected what would happen next,"),
                 (40, 220, 11, "least of all the baker, who had lived")]),
        page(2, [(40, 60, 11, "there for forty years and knew every"),
                 (40, 75, 11, "face in the street."),
                 (60, 90, 11, "He opened the shop as usual and waited."),
                 (60, 105, 11, "The well-known bell over the door rang twice.")]),
        page(3, [(40, 90, 18, "CHAPTER II"), (40, 116, 18, "THE STRANGER"),
                 (40, 160, 11, "The stranger wore a long grey coat and"),
                 (40, 175, 11, "carried a small wooden box under his arm."),
                 (60, 190, 11, "He asked for bread and paid in gold.")]),
        page(4, [(60, 60, 11, "That night the baker could not sleep."),
                 (60, 75, 11, "He thought about the box until morning.")]),
    ]
