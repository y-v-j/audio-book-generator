# Changelog

## 0.2.0 (2026-10-16)

- PDF to EPUB: `abgen epub` turns text-based and scanned PDFs into valid EPUB 3 files (Tesseract OCR, running headers and page numbers removed, words rejoined, paragraphs rebuilt across pages, chapters from bookmarks, headings or chapter-page numbers, the first page as the cover).
- `info`, `text` and `convert` accept PDFs directly; `convert --save-epub` keeps the EPUB as well.
- PDF options: `--ocr auto|always|never`, `--ocr-lang`, `--dpi`, `--ocr-workers`, `--title`, `--author`.
- DRM-protected EPUBs are refused with a clear message instead of being read as garbage.

## 0.1.0 (2026-10-16)

- EPUB to audiobook with the Kokoro-82M voices: one M4B with chapters and cover, or one MP3 per chapter.
- Text rewritten for speech (abbreviations, numbers, dates, money, Roman numerals, headings), natural pauses and pace, room tone, even loudness.
- Voice aliases and blends, `preview`, `text`, resumable conversion, pronunciation lexicon.
