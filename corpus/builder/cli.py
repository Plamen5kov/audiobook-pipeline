"""Build and query an aligned book corpus.

Stages are separate subcommands and each writes per-chapter artifacts, so a
28-hour book can be built incrementally and resumed after an interruption
without redoing finished chapters.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK = ROOT / "work"
DEFAULT_DB = ROOT / "data" / "corpus.db"

CHAPTER_TITLE = re.compile(r"^\s*Chapter\s+(\d+)\s*[-–:.]?\s*(.*)$", re.IGNORECASE)


def registry_defaults() -> tuple[str, str]:
    """Model and endpoint for the registry pass, overridable from the shell."""
    import os

    from . import registry

    return (os.environ.get("REGISTRY_MODEL", registry.MODEL),
            os.environ.get("OLLAMA_URL", registry.ENDPOINT))


def _audio_path(base: Path) -> Path:
    """Locate the book audio whatever container it arrived in.

    Audiobooks ship as .mp3, .m4b or .m4a depending on the source, and the
    extension is kept rather than normalised so ffmpeg's format detection and
    any manual inspection both see the truth.
    """
    found = [p for p in sorted(base.glob("book.*"))
             if p.suffix.lower() not in (".epub", ".json")]
    return found[0] if found else base / "book.mp3"


def _paths(work: Path, slug: str) -> dict[str, Path]:
    base = work / slug
    return {
        "base": base,
        "audio": _audio_path(base),
        "epub": base / "book.epub",
        "chapters": base / "chapters.json",
        "text": base / "text",
        "segments": base / "segments",
        "align": base / "align",
        "wav": base / "wav",
    }


def _load_chapters(p: Path) -> list[dict]:
    if not p.exists():
        sys.exit(f"missing {p} — run `fetch` first")
    return json.loads(p.read_text())["chapters"]


def _select(chapters: list[dict], spec: str | None) -> list[dict]:
    """Filter chapters by a spec like ``1``, ``1-10`` or ``1,4,7``."""
    if not spec:
        return chapters
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        elif part:
            wanted.add(int(part))
    return [c for c in chapters if c["number"] in wanted]


def cmd_fetch(args) -> None:
    """Copy the source files locally and read the embedded chapter markers."""
    p = _paths(args.work, args.slug)
    p["base"].mkdir(parents=True, exist_ok=True)

    audio_dest = p["base"] / f"book{Path(str(args.audio)).suffix.lower() or '.mp3'}"
    for src, dest in ((args.audio, audio_dest), (args.epub, p["epub"])):
        if dest.exists() and not args.force:
            print(f"have {dest.name}")
            continue
        if ":" in str(src) and not Path(src).exists():
            # Streamed over ssh rather than scp: OpenSSH 9 runs scp over SFTP,
            # which does not shell-expand, so quoting a path containing spaces
            # is ambiguous. ssh always hands its command to a shell.
            host, _, remote = str(src).partition(":")
            with open(dest, "wb") as fh:
                subprocess.run(["ssh", host, f"cat {shlex.quote(remote)}"],
                               stdout=fh, check=True)
        else:
            shutil.copy2(src, dest)
        print(f"fetched {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)")

    # Probe the file just fetched, not p["audio"]: p was resolved before the
    # download existed, so it still holds the default name.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters",
         str(audio_dest)],
        capture_output=True, text=True, check=True,
    )
    chapters = []
    for c in json.loads(probe.stdout).get("chapters", []):
        title = (c.get("tags") or {}).get("title", "")
        m = CHAPTER_TITLE.match(title)
        if not m:
            continue
        chapters.append({
            "number": int(m.group(1)),
            "title": m.group(2).strip(),
            "audio_start_s": round(float(c["start_time"]), 3),
            "audio_end_s": round(float(c["end_time"]), 3),
        })
    chapters.sort(key=lambda c: c["number"])
    if not chapters:
        sys.exit("no chapter markers found in the audio")

    total = chapters[-1]["audio_end_s"]
    p["chapters"].write_text(json.dumps(
        {"slug": args.slug, "title": args.title, "author": args.author,
         "series": args.series, "book_number": args.book_number,
         "audio_source": str(args.audio), "epub_source": str(args.epub),
         "audio_duration_s": total, "chapters": chapters}, indent=1))
    print(f"{len(chapters)} chapters, {total / 3600:.2f} h -> {p['chapters']}")


def cmd_extract(args) -> None:
    from . import epub_extract

    p = _paths(args.work, args.slug)
    markers = {c["number"]: c for c in _load_chapters(p["chapters"])}
    chapters = epub_extract.extract_chapters(p["epub"])
    epub_extract.write_chapters(chapters, p["text"])

    found = {c.number for c in chapters}
    missing = sorted(set(markers) - found)
    extra = sorted(found - set(markers))
    mismatch = [c.number for c in chapters
                if c.number in markers
                and _norm(c.title) != _norm(markers[c.number]["title"])]

    print(f"extracted {len(chapters)} chapters, "
          f"{sum(c.word_count for c in chapters):,} words -> {p['text']}")
    print(f"audio-only chapters: {missing or 'none'}")
    print(f"text-only chapters: {extra or 'none'}")
    print(f"title mismatches: {mismatch or 'none'}")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cmd_segment(args) -> None:
    from . import segment

    p = _paths(args.work, args.slug)
    chapters = _select(_load_chapters(p["chapters"]), args.chapters)
    rows = segment.run(p["text"], p["segments"], [c["number"] for c in chapters])
    total = sum(r["segments"] for r in rows)
    unknown = sum(r["unknown"] for r in rows)
    print(f"{len(rows)} chapters, {total:,} segments, "
          f"{unknown:,} unattributed ({100 * unknown / max(total, 1):.1f}%)")


def cmd_align(args) -> None:
    from . import align

    p = _paths(args.work, args.slug)
    chapters = _select(_load_chapters(p["chapters"]), args.chapters)
    if not args.force:
        chapters = [c for c in chapters
                    if not (p["align"] / f"ch{c['number']:03d}.json").exists()]
    if not chapters:
        print("nothing to align")
        return
    print(f"aligning {len(chapters)} chapters on {args.device}", flush=True)
    align.run(p["audio"], p["segments"], p["align"], p["wav"], chapters,
              device=args.device, keep_wav=args.keep_wav)


def cmd_load(args) -> None:
    from . import db

    p = _paths(args.work, args.slug)
    manifest = json.loads(p["chapters"].read_text())
    chapters = _select(manifest["chapters"], args.chapters)

    conn = db.connect(args.db)
    book_id = db.upsert_book(conn, {
        "slug": manifest["slug"], "title": manifest["title"],
        "author": manifest["author"], "series": manifest["series"],
        "book_number": manifest["book_number"],
        "audio_source": manifest["audio_source"],
        "epub_source": manifest["epub_source"],
        "audio_duration_s": manifest["audio_duration_s"],
    })
    stats = db.load_chapters(conn, book_id, p["text"], p["segments"],
                             p["align"], chapters)
    print(f"book {book_id}: {stats}")


def cmd_aliases(args) -> None:
    from . import aliases, db

    p = _paths(args.work, args.slug)
    conn = db.connect(args.db)
    row = conn.execute("SELECT id FROM books WHERE slug = ?", (args.slug,)).fetchone()
    if not row:
        sys.exit(f"no book with slug {args.slug!r} — run `load` first")
    book_id = row[0]

    proposals = aliases.derive(conn, book_id, p["text"])
    if not proposals:
        print("no alias candidates found")
        return

    width = max(len(x["alias"]) for x in proposals)
    for x in proposals:
        print(f"  {x['alias']:<{width}} -> {x['canonical']:<12} "
              f"{x['reason']:<14} {x['lines']:>4} lines "
              f"(evidence {x['evidence']})")
    moved = sum(x["lines"] for x in proposals)
    print(f"\n{len(proposals)} aliases covering {moved:,} segments")

    if not args.apply:
        print("dry run — pass --apply to write")
        return

    aliases.store(conn, book_id, proposals)
    aliases.apply(conn, book_id)
    n = db.rebuild_characters(conn, book_id)
    conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
    conn.commit()
    print(f"applied; {n} characters remain")


def cmd_registry(args) -> None:
    from . import db, registry

    p = _paths(args.work, args.slug)
    conn = db.connect(args.db)
    row = conn.execute("SELECT id, title FROM books WHERE slug = ?",
                       (args.slug,)).fetchone()
    if not row:
        sys.exit(f"no book with slug {args.slug!r} — run `load` first")
    book_id, title = row["id"], row["title"]

    if args.reuse:
        verdicts = [dict(r) for r in conn.execute(
            """SELECT name, is_character, canonical, gender, confidence, reason
               FROM character_registry WHERE book_id = ?""", (book_id,))]
        if not verdicts:
            sys.exit("nothing stored — run without --reuse first")
    else:
        bundles = registry.gather(conn, book_id, args.min_lines)
        cast = [b["name"] for b in bundles]
        if args.only:
            wanted = {n.strip() for n in args.only.split(",")}
            missing = wanted - {b["name"] for b in bundles}
            if missing:
                sys.exit(f"not in the cast: {', '.join(sorted(missing))}")
            bundles = [b for b in bundles if b["name"] in wanted]
        if args.limit:
            bundles = bundles[:args.limit]
        print(f"judging {len(bundles)} labels with {args.model} "
              f"at {args.endpoint}", flush=True)
        done = [0]

        def tick(r):
            done[0] += 1
            print(f"  [{done[0]}/{len(bundles)}] {r['name']:<22} "
                  f"{r['gender']:<7} {r['confidence']:.2f} "
                  f"{'-> ' + r['canonical'] if r['canonical'] else ''}",
                  flush=True)

        verdicts = registry.classify(bundles, title, args.model,
                                     args.endpoint, on_result=tick, cast=cast)

    # Line counts drive the anchor rule the guard reuses, so they come from the
    # corpus rather than from whatever subset was judged this run.
    labels = {r[0]: r[1] for r in conn.execute(
        """SELECT speaker, COUNT(*) FROM segments
           WHERE book_id = ? AND kind = 'dialogue'
             AND speaker NOT IN ('narrator', 'unknown')
           GROUP BY speaker""", (book_id,))}
    verdicts = registry.guard(verdicts, labels, p["text"])
    registry.store(conn, book_id, verdicts, args.model)

    current = {r[0]: r[1] for r in conn.execute(
        "SELECT name, gender FROM characters WHERE book_id = ?", (book_id,))}
    merges = [v for v in verdicts if v["verdict"] == "merge"]
    vetoed = [v for v in verdicts if v["verdict"].startswith("vetoed")]
    junk = [v for v in verdicts if v["is_character"] is False]
    flips = [v for v in verdicts
             if v["gender"] in ("male", "female")
             and current.get(v["name"], "unknown") != v["gender"]
             and v["confidence"] >= registry.APPLY_CONFIDENCE]

    def show(rows, header):
        if not rows:
            return
        print(f"\n{header} ({len(rows)})")
        for v in sorted(rows, key=lambda r: -r["lines"] if "lines" in r else 0):
            target = f" -> {v['canonical']}" if v["canonical"] else ""
            print(f"  {v['name']:<24}{target:<16} {v['gender']:<7} "
                  f"{v['confidence']:.2f}  {(v['reason'] or '')[:70]}")

    show(merges, "merges the prose supports")
    show(vetoed, "merges vetoed by the guard")
    show(junk, "labels judged not to be people")
    show(flips, "gender changes against the pronoun tally")

    print(f"\n{len(verdicts)} verdicts stored")
    if not args.apply:
        print("stored as proposals — pass --apply to rewrite speakers and gender")
        return

    stats = registry.apply(conn, book_id, args.min_confidence)
    n = conn.execute("SELECT COUNT(*) FROM characters WHERE book_id = ?",
                     (book_id,)).fetchone()[0]
    print(f"applied: {stats['merged']} merges, {stats['gender_set']} genders set, "
          f"{n} characters remain")


def cmd_voicebank(args) -> None:
    from . import db, voicebank

    p = _paths(args.work, args.slug)
    conn = db.connect(args.db)
    row = conn.execute("SELECT id FROM books WHERE slug = ?", (args.slug,)).fetchone()
    if not row:
        sys.exit(f"no book with slug {args.slug!r} — run `load` first")

    bank = voicebank.export(conn, row[0], p["audio"], Path(args.out),
                            per_character=args.per_character,
                            min_clips=args.min_clips)
    total = sum(len(v) for v in bank.values())
    print(f"{len(bank)} voices, {total} reference clips -> {args.out}")
    for name, clips in list(bank.items())[:args.show]:
        secs = sum(c["duration_s"] or 0 for c in clips)
        print(f"  {name:<16} {len(clips)} clips  {secs:5.1f}s")


def cmd_stats(args) -> None:
    from . import db

    conn = db.connect(args.db)
    for book in conn.execute("SELECT * FROM books"):
        print(f"\n{book['title']} (id={book['id']}, {book['audio_duration_s'] / 3600:.1f} h)")
        row = conn.execute(
            """SELECT COUNT(*) n,
                      SUM(audio_start_s IS NOT NULL) aligned,
                      SUM(duration_s) secs,
                      AVG(align_score) score
               FROM segments WHERE book_id = ?""", (book["id"],)).fetchone()
        chapters = conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE book_id = ?", (book["id"],)).fetchone()[0]
        print(f"  {chapters} chapters, {row['n']:,} segments, "
              f"{row['aligned'] or 0:,} aligned, "
              f"{(row['secs'] or 0) / 3600:.1f} h of aligned speech, "
              f"mean score {row['score'] or 0:.3f}")
        print("  top speakers:")
        for r in conn.execute(
            """SELECT speaker, COUNT(*) n, SUM(duration_s) secs, AVG(align_score) sc
               FROM segments WHERE book_id = ? AND kind != 'heading'
               GROUP BY speaker ORDER BY n DESC LIMIT ?""",
                (book["id"], args.top)):
            mins = (r["secs"] or 0) / 60
            print(f"    {r['speaker']:<24} {r['n']:>6,} segments  "
                  f"{mins:>7.1f} min  score {r['sc'] or 0:.3f}")


def cmd_clips(args) -> None:
    from . import clips, db

    p = _paths(args.work, args.slug)
    conn = db.connect(args.db)
    book_id = conn.execute("SELECT id FROM books WHERE slug = ?",
                           (args.slug,)).fetchone()[0]
    rows = clips.select(conn, book_id, speaker=args.speaker, kind=args.kind,
                        chapter=args.chapter, min_score=args.min_score,
                        min_words=args.min_words, min_duration=args.min_duration,
                        limit=args.limit, spread=args.spread)
    if not rows:
        sys.exit("no segments matched")
    if args.jsonl:
        out = clips.export_jsonl(conn, rows, Path(args.jsonl))
        print(f"{len(rows)} segments -> {out}")
        return
    out_dir = Path(args.out)
    manifest = clips.export(conn, rows, p["audio"], out_dir, fmt=args.format,
                            sample_rate=args.sample_rate)
    total = sum(r["duration_s"] or 0 for r in rows)
    print(f"{len(rows)} clips ({total / 60:.1f} min) -> {out_dir}")
    print(f"manifest: {manifest}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="corpus", description=__doc__)
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--slug", default="hwfwm-b1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="copy sources locally and read chapter markers")
    f.add_argument("--audio", required=True)
    f.add_argument("--epub", required=True)
    f.add_argument("--title", required=True)
    f.add_argument("--author", default=None)
    f.add_argument("--series", default=None)
    f.add_argument("--book-number", type=int, default=None)
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_fetch)

    e = sub.add_parser("extract", help="epub -> per-chapter text")
    e.set_defaults(func=cmd_extract)

    s = sub.add_parser("segment", help="text -> attributed segments")
    s.add_argument("--chapters", default=None)
    s.set_defaults(func=cmd_segment)

    a = sub.add_parser("align", help="audio -> word and segment timings")
    a.add_argument("--chapters", default=None)
    a.add_argument("--device", default="cpu")
    a.add_argument("--keep-wav", action="store_true")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_align)

    l = sub.add_parser("load", help="artifacts -> sqlite")
    l.add_argument("--chapters", default=None)
    l.set_defaults(func=cmd_load)

    al = sub.add_parser("aliases", help="merge labels naming the same character")
    al.add_argument("--apply", action="store_true", help="write (default: dry run)")
    al.set_defaults(func=cmd_aliases)

    rg = sub.add_parser("registry", help="judge who each speaker label is (LLM)")
    rg.add_argument("--model", default=registry_defaults()[0])
    rg.add_argument("--endpoint", default=registry_defaults()[1])
    rg.add_argument("--min-lines", type=int, default=2,
                    help="skip labels with fewer dialogue lines than this")
    rg.add_argument("--min-confidence", type=float, default=0.6,
                    help="confidence a merge needs to be applied; gender uses "
                         "the module constant so a reload cannot disagree")
    rg.add_argument("--limit", type=int, default=None, help="judge only the first N")
    rg.add_argument("--only", default=None,
                    help="judge only these labels (comma separated)")
    rg.add_argument("--reuse", action="store_true",
                    help="re-guard and re-report stored verdicts, no model calls")
    rg.add_argument("--apply", action="store_true", help="write (default: dry run)")
    rg.set_defaults(func=cmd_registry)

    vb = sub.add_parser("voicebank", help="export cloning references per character")
    vb.add_argument("--out", default="voicebank")
    vb.add_argument("--per-character", type=int, default=3)
    vb.add_argument("--min-clips", type=int, default=2)
    vb.add_argument("--show", type=int, default=15)
    vb.set_defaults(func=cmd_voicebank)

    st = sub.add_parser("stats", help="summarise the corpus")
    st.add_argument("--top", type=int, default=15)
    st.set_defaults(func=cmd_stats)

    c = sub.add_parser("clips", help="cut clips for a speaker")
    c.add_argument("--speaker", default=None)
    c.add_argument("--kind", default=None, choices=["dialogue", "narration", "heading"])
    c.add_argument("--chapter", type=int, default=None)
    c.add_argument("--min-score", type=float, default=0.0)
    c.add_argument("--min-words", type=int, default=0)
    c.add_argument("--min-duration", type=float, default=0.0)
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--out", default="clips")
    c.add_argument("--format", default="wav")
    c.add_argument("--sample-rate", type=int, default=24000)
    c.add_argument("--spread", action="store_true",
                   help="sample evenly across the book instead of the first N")
    c.add_argument("--jsonl", default=None)
    c.set_defaults(func=cmd_clips)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
