"""Look inside a pipeline run.

The studio UI will eventually show this; until then the same information is
here, because being able to see what a stage produced is the point and it
should not wait on a frontend.

    job.py list                          every run, and how far each got
    job.py show <id>                     stages, artifacts, counts
    job.py segments <id> [--speaker X]   what was meant to be said, and by whom
    job.py segments <id> --failed        only what QA flagged
    job.py redo <id> 12 40               mark lines to render again next run

``redo`` does not synthesise anything. It forgets those segments' clips, so the
next synthesis run renders them and reuses everything else.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.jobs.workspace import STAGES, Workspace  # noqa: E402

DEFAULT_ROOT = Path(os.getenv(
    "WORKSPACE_DIR", Path(__file__).resolve().parents[1] / "output" / "workspace"))


def _workspace(args) -> Workspace:
    return Workspace(Path(args.root))


def _job_or_exit(args):
    ws = _workspace(args)
    if args.job_id not in ws.jobs():
        sys.exit(f"no job {args.job_id!r} in {ws.root} "
                 f"(have: {', '.join(ws.jobs()) or 'none'})")
    return ws.job(args.job_id)


def cmd_list(args) -> None:
    ws = _workspace(args)
    jobs = ws.jobs()
    if not jobs:
        print(f"no runs in {ws.root}")
        return
    width = max(len(j) for j in jobs)
    print(f"{'job':<{width}}  " + "  ".join(f"{s:<9}" for s in STAGES))
    for name in jobs:
        summary = ws.job(name).summary()
        cells = ("done" if summary["stages"][s] == "done" else summary["stages"][s]
                 for s in STAGES)
        print(f"{name:<{width}}  " + "  ".join(f"{c:<9}" for c in cells))


def cmd_show(args) -> None:
    job = _job_or_exit(args)
    manifest = job.manifest()
    print(f"job {job.job_id}   created {manifest.get('created', '?')}")
    print(f"directory {job.root}")
    print()
    for stage in STAGES:
        entry = manifest["stages"].get(stage)
        if not entry:
            print(f"  {stage:<10} not run")
            continue
        extras = {k: v for k, v in entry.items()
                  if k not in ("status", "at", "artifact")}
        detail = "  ".join(f"{k}={v}" for k, v in extras.items())
        artifact = entry.get("artifact", "")
        print(f"  {stage:<10} {entry['status']:<8} {artifact:<26} {detail}")
    print()
    print(f"  {len(manifest.get('segments', {}))} segment clips recorded")


def _load_segments(job) -> list[dict]:
    data = job.read_json("analysis", "segments.json")
    if not data:
        sys.exit(f"no analysis artifact for {job.job_id} — has it been analysed?")
    return data["segments"]


def cmd_segments(args) -> None:
    job = _job_or_exit(args)
    segments = _load_segments(job)

    qa = job.read_json("qa", "report.json") or {}
    verdicts = {r["id"]: r for r in qa.get("results", []) if "id" in r}

    for seg in segments:
        verdict = verdicts.get(seg["id"], {})
        if args.failed and verdict.get("status") not in ("failed", "suspect"):
            continue
        if args.speaker and seg.get("speaker") != args.speaker:
            continue
        record = job.segment_record(seg["id"])
        marks = []
        if verdict.get("status") in ("failed", "suspect"):
            marks.append(f"{verdict['status']} {verdict.get('similarity', 0):.2f}")
        if not record:
            marks.append("no clip")
        spoken = seg.get("spoken_text") or seg.get("original_text", "")
        note = ("  [" + ", ".join(marks) + "]") if marks else ""
        print(f"{seg['id']:>5}  {seg.get('speaker', '?'):<14} {spoken[:88]}{note}")


def cmd_redo(args) -> None:
    job = _job_or_exit(args)
    known = {int(k) for k in job.manifest().get("segments", {})}
    missing = [i for i in args.segments if i not in known]
    for seg_id in args.segments:
        job.forget_segment(seg_id)
    job.save()
    print(f"marked {len(args.segments)} segment(s) to render again: "
          f"{', '.join(str(i) for i in args.segments)}")
    if missing:
        print(f"note: {', '.join(str(i) for i in missing)} had no clip recorded anyway")
    print("run synthesis again; everything else is reused")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="workspace directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="every run and how far it got")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="stages and artifacts for one run")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("segments", help="what each segment says, and its state")
    p.add_argument("job_id")
    p.add_argument("--failed", action="store_true", help="only what QA flagged")
    p.add_argument("--speaker", default=None)
    p.set_defaults(func=cmd_segments)

    p = sub.add_parser("redo", help="mark segments to render again next run")
    p.add_argument("job_id")
    p.add_argument("segments", nargs="+", type=int)
    p.set_defaults(func=cmd_redo)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
