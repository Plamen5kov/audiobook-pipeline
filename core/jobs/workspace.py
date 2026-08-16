"""Where a run's work is kept, so it can be looked at.

A run used to exist only as HTTP calls passing JSON between services. Nothing
survived it but the finished audio, so the only way to find out why a chapter
came out wrong was to run it again and watch the logs.

Here every stage writes what it produced into a directory of its own, numbered
in the order they happen:

    workspace/<job>/
      00-input/      chapter.txt
      01-analysis/   segments.json
      02-cast/       cast.json
      03-synthesis/  0001.wav …
      04-assembly/   chapter.mp3
      05-qa/         report.json
      manifest.json

The numbers are the point. Somebody opening the directory, or an agent reading
it, learns the order of the pipeline from the listing, and answering "why does
line 40 sound wrong" is a matter of opening 01-analysis to see what was meant
to be said and playing the clip in 03-synthesis.

The manifest records what happened per stage, and a fingerprint per synthesised
segment so a later run can tell which clips are still current. That is what
makes it possible to change one line and re-render only that line.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Order matters and is encoded in the directory names.
STAGES: tuple[str, ...] = (
    "input", "analysis", "cast", "synthesis", "assembly", "qa",
)

MANIFEST = "manifest.json"


def stage_dirname(stage: str) -> str:
    try:
        return f"{STAGES.index(stage):02d}-{stage}"
    except ValueError:
        raise KeyError(f"unknown stage {stage!r}; known: {', '.join(STAGES)}") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    A half-written manifest is worse than none: the next run would read it,
    believe a stage finished, and skip work that never happened.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class Job:
    """One run's directory, and the manifest that describes it."""

    root: Path
    job_id: str
    _manifest: dict = field(default_factory=dict, repr=False)

    # ---- layout -----------------------------------------------------------

    def stage_dir(self, stage: str, create: bool = False) -> Path:
        path = self.root / stage_dirname(stage)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, stage: str, name: str, create: bool = False) -> Path:
        return self.stage_dir(stage, create=create) / name

    # ---- artifacts --------------------------------------------------------

    def write_text(self, stage: str, name: str, text: str) -> Path:
        p = self.path(stage, name, create=True)
        _write_atomic(p, text)
        return p

    def write_json(self, stage: str, name: str, data) -> Path:
        return self.write_text(stage, name, json.dumps(data, indent=1, ensure_ascii=False))

    def read_json(self, stage: str, name: str):
        p = self.path(stage, name)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ---- manifest ---------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST

    def manifest(self) -> dict:
        if not self._manifest:
            if self.manifest_path.exists():
                self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            else:
                self._manifest = {"job_id": self.job_id, "created": _now(),
                                  "stages": {}, "segments": {}}
        return self._manifest

    def save(self) -> None:
        _write_atomic(self.manifest_path,
                      json.dumps(self.manifest(), indent=1, ensure_ascii=False))

    def record_stage(self, stage: str, status: str, artifact: str | None = None,
                     **extra) -> None:
        """Note that a stage reached a state. Absent means never run, which is
        different from run and failed."""
        entry = {"status": status, "at": _now()}
        if artifact:
            entry["artifact"] = f"{stage_dirname(stage)}/{artifact}"
        entry.update(extra)
        self.manifest()["stages"][stage] = entry
        self.save()

    def stage_status(self, stage: str) -> str | None:
        entry = self.manifest()["stages"].get(stage)
        return entry["status"] if entry else None

    # ---- per-segment bookkeeping -----------------------------------------

    def segment_record(self, segment_id: int) -> dict | None:
        return self.manifest()["segments"].get(str(segment_id))

    def record_segment(self, segment_id: int, fingerprint: str, clip: str,
                       **extra) -> None:
        self.manifest()["segments"][str(segment_id)] = {
            "fingerprint": fingerprint, "clip": clip, "at": _now(), **extra}

    def forget_segment(self, segment_id: int) -> None:
        """Drop a segment's record so the next run re-renders it, which is what
        'do that line again' means when the inputs have not changed."""
        self.manifest()["segments"].pop(str(segment_id), None)

    def summary(self) -> dict:
        m = self.manifest()
        return {
            "job_id": m.get("job_id", self.job_id),
            "created": m.get("created"),
            "stages": {s: m["stages"].get(s, {}).get("status", "not run")
                       for s in STAGES},
            "segments_recorded": len(m.get("segments", {})),
        }


@dataclass
class Workspace:
    """The directory holding every job."""

    root: Path

    def job(self, job_id: str, create: bool = False) -> Job:
        if "/" in job_id or ".." in job_id:
            raise ValueError(f"unsafe job id: {job_id!r}")
        job_root = self.root / job_id
        if create:
            job_root.mkdir(parents=True, exist_ok=True)
        return Job(root=job_root, job_id=job_id)

    def jobs(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def delete(self, job_id: str) -> bool:
        """Remove a run's directory. Returns False if it was not there.

        Only the job's own directory goes. The synthesised clips live in the
        volume shared with assembly, under names derived from the segment id
        alone, so several runs point at the same files — deleting them here
        would take another run's audio with it.
        """
        job_root = self.job(job_id).root
        if not job_root.is_dir():
            return False
        if job_root.resolve().parent != self.root.resolve():
            # A job directory is always one level under the workspace. Anything
            # else means the id escaped its validation, and deleting recursively
            # on the strength of that is not a risk worth taking.
            raise ValueError(f"refusing to delete outside the workspace: {job_root}")
        shutil.rmtree(job_root)
        return True
