"""Split an epub into one plain-text file per chapter.

Chapter boundaries come from the NCX navigation map walked against the OPF
spine, so a chapter that the publisher split across several XHTML files is
still reassembled into one document.

Output text is one paragraph per line, which is the shape
``segment_splitter`` expects.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

NCX_NS = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
OPF_NS = {"o": "http://www.idpf.org/2007/opf"}

BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "br"}
SKIP_TAGS = {"script", "style", "head"}

_CHAPTER_LABEL = re.compile(r"^\s*(\d+)\s*[.)-]\s*(.+?)\s*$")


@dataclass
class ChapterText:
    number: int
    title: str
    heading: str
    text: str
    word_count: int


class _Extractor(HTMLParser):
    """Collect text with block-level tags forced onto their own lines.

    ``anchor`` starts collection at the element carrying that id; without one,
    collection starts immediately.
    """

    def __init__(self, anchor: str | None = None):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._anchor = anchor
        self._live = anchor is None

    def _flush(self) -> None:
        line = re.sub(r"[ \t]+", " ", "".join(self._buf)).strip()
        self._buf.clear()
        if line:
            self.lines.append(line)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if not self._live:
            if dict(attrs).get("id") == self._anchor:
                self._live = True
            else:
                return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._live and tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._live and not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _spine_documents(zf: zipfile.ZipFile) -> tuple[list[str], str]:
    """Return spine document paths in reading order, plus the OPF directory."""
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(
        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
    )
    opf_path = rootfile.get("full-path")
    opf_dir = str(Path(opf_path).parent)

    opf = ET.fromstring(zf.read(opf_path))
    manifest = {
        item.get("id"): unquote(item.get("href"))
        for item in opf.findall(".//o:manifest/o:item", OPF_NS)
    }
    spine = [
        manifest[ref.get("idref")]
        for ref in opf.findall(".//o:spine/o:itemref", OPF_NS)
        if ref.get("idref") in manifest
    ]
    return spine, opf_dir


def _ncx_path(zf: zipfile.ZipFile) -> str:
    for name in zf.namelist():
        if name.lower().endswith(".ncx"):
            return name
    raise FileNotFoundError("no NCX navigation document in epub")


def _nav_points(zf: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    """Return ``(label, href, anchor)`` for every navigation point, in order."""
    ncx = ET.fromstring(zf.read(_ncx_path(zf)))
    points = []
    for nav in ncx.findall(".//n:navPoint", NCX_NS):
        label_el = nav.find(".//n:text", NCX_NS)
        content = nav.find("n:content", NCX_NS)
        if label_el is None or content is None:
            continue
        src = unquote(content.get("src") or "")
        href, _, anchor = src.partition("#")
        points.append(((label_el.text or "").strip(), href, anchor or ""))
    return points


def _join(opf_dir: str, href: str) -> str:
    return str(Path(opf_dir) / href) if opf_dir not in ("", ".") else href


def extract_chapters(epub_path: Path) -> list[ChapterText]:
    """Extract every numbered chapter from *epub_path* in reading order."""
    with zipfile.ZipFile(epub_path) as zf:
        spine, opf_dir = _spine_documents(zf)
        spine_index = {path: i for i, path in enumerate(spine)}
        points = _nav_points(zf)

        # Where each navigation point lands in the spine, so a chapter can be
        # closed at the start of whatever comes next rather than at EOF.
        located = []
        for label, href, anchor in points:
            full = _join(opf_dir, href)
            if full in spine_index:
                located.append((label, spine_index[full], anchor))

        chapters: list[ChapterText] = []
        for pos, (label, start_idx, anchor) in enumerate(located):
            m = _CHAPTER_LABEL.match(label)
            if not m:
                continue
            number, title = int(m.group(1)), m.group(2)
            end_idx = located[pos + 1][1] if pos + 1 < len(located) else len(spine)
            if end_idx <= start_idx:
                end_idx = start_idx + 1

            lines: list[str] = []
            for i in range(start_idx, end_idx):
                parser = _Extractor(anchor if i == start_idx and anchor else None)
                parser.feed(zf.read(_join(opf_dir, spine[i])).decode("utf-8", "replace"))
                parser.close()
                lines.extend(parser.lines)

            heading = _strip_heading(lines, number, title)
            text = "\n".join(lines).strip()
            chapters.append(ChapterText(
                number=number,
                title=title,
                heading=heading,
                text=text,
                word_count=len(re.findall(r"[A-Za-z']+", text)),
            ))

    chapters.sort(key=lambda c: c.number)
    return chapters


def _strip_heading(lines: list[str], number: int, title: str) -> str:
    """Normalise the chapter heading line to the spoken title.

    The narration says the title, so it stays in the text and aligns; the bare
    chapter number does not survive tokenisation and is dropped.
    """
    # Publishers split the heading in two ways: "1. Strange Business" on one
    # line, or a bare "1" above the title. Neither the digits nor the period
    # survive tokenisation, and a bare number left in place becomes a segment
    # with no alignable words.
    for i, line in enumerate(lines[:4]):
        m = _CHAPTER_LABEL.match(line)
        if m and m.group(1) == str(number):
            lines[i] = title
            return title
        if line.strip() == str(number):
            lines.pop(i)
            return _strip_heading(lines, number, title)
        if line.strip().lower() == title.strip().lower():
            return line.strip()
    lines.insert(0, title)
    return title


def write_chapters(chapters: list[ChapterText], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for ch in chapters:
        (out_dir / f"ch{ch.number:03d}.txt").write_text(ch.text, encoding="utf-8")
        meta = asdict(ch)
        meta.pop("text")
        (out_dir / f"ch{ch.number:03d}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
