"""Fetch Royal Road chapters as plain text for the audiobook pipeline.

The fiction index only lists the first and most recent chapters, and chapter
URLs carry opaque numeric ids that cannot be constructed, so this walks the
site's own "Next Chapter" links forward from a starting chapter.

Output matches the layout the corpus builder expects: title on the first line,
then one paragraph per line.

Resumable by design — an already-saved chapter is skipped, so an interrupted
run continues rather than restarting. Requests are paced deliberately; there is
no reason to hammer someone's server for a batch job that nobody is waiting on.

Usage: fetch_rr_chapters.py <out_dir> <start_chapter_url> [count]
"""

from __future__ import annotations

import html
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120 Safari/537.36")
CONTENT = re.compile(r'<div class="chapter-inner chapter-content">(.*?)</div>', re.S)
PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
NEXT = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>\s*Next\s*(?:Chapter)?\s*<', re.I)
TITLE = re.compile(r"<title>([^<]+)</title>")

DELAY_S = 2.0
RETRIES = 3
BACKOFF_S = 15


def _get(url: str) -> str:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(BACKOFF_S * attempt)
    raise RuntimeError(f"failed after {RETRIES} attempts: {url} ({last})")


def _to_text(body: str) -> str:
    paras = []
    for raw in PARA.findall(body):
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        text = re.sub(r"[ \t ]+", " ", text)
        if text:
            paras.append(text)
    return "\n".join(paras)


def main() -> None:
    out_dir = Path(sys.argv[1])
    url = sys.argv[2]
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    out_dir.mkdir(parents=True, exist_ok=True)

    base = re.match(r"(https?://[^/]+)", url).group(1)
    saved = skipped = 0

    for i in range(count):
        try:
            page = _get(url)
        except RuntimeError as exc:
            print(f"stopping: {exc}", flush=True)
            break

        title_m = TITLE.search(page)
        heading = (html.unescape(title_m.group(1)).split(" - ")[0].strip()
                   if title_m else f"chapter-{i}")
        slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
        path = out_dir / f"{slug}.txt"

        if path.exists():
            skipped += 1
        else:
            m = CONTENT.search(page)
            if not m:
                print(f"{heading}: no content block, skipped", flush=True)
            else:
                body = _to_text(m.group(1))
                path.write_text(f"{heading}\n{body}\n", encoding="utf-8")
                words = len(re.findall(r"[A-Za-z']+", body))
                saved += 1
                print(f"[{saved + skipped}/{count}] {heading}: {words:,} words",
                      flush=True)

        nxt = NEXT.search(page)
        if not nxt:
            print("no Next Chapter link — reached the end", flush=True)
            break
        href = html.unescape(nxt.group(1))
        url = href if href.startswith("http") else base + href
        time.sleep(DELAY_S)

    print(f"\ndone: {saved} saved, {skipped} already present -> {out_dir}",
          flush=True)


if __name__ == "__main__":
    main()
