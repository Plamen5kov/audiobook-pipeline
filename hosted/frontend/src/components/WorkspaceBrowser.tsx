import { useCallback, useEffect, useMemo, useState } from "react";
import {
  JobDetail,
  JobSummary,
  STAGE_ORDER,
  StageArtifacts,
  StageName,
  WorkspaceSegment,
  getJob,
  getStageArtifacts,
  listJobs,
  deleteAllJobs,
  deleteJob,
  listSegments,
  redoSegments,
  segmentAudioUrl,
} from "../api";
import { formatError } from "../utils/formatError";
import "./WorkspaceBrowser.css";

/**
 * Browsing what a run left behind.
 *
 * The pipeline keeps every stage's output, and this is the window onto it:
 * pick a run, see how far it got, read what each stage produced, and go line by
 * line to hear a take and mark the ones worth doing again. It exists so
 * problems get noticed at the stage that caused them rather than at the end.
 */

const STAGE_LABELS: Record<StageName, string> = {
  input: "input",
  analysis: "analysis",
  cast: "cast",
  synthesis: "synthesis",
  assembly: "assembly",
  qa: "qa",
};

function stageClass(status: string | undefined): string {
  if (status === "done") return "ws-stage-done";
  if (status === "failed" || status === "error") return "ws-stage-error";
  if (!status || status === "not run") return "ws-stage-pending";
  return "ws-stage-other";
}

function JobList({
  jobs,
  selected,
  onSelect,
  onDelete,
}: {
  jobs: JobSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (jobs.length === 0) {
    return (
      <p className="ws-empty">
        No runs yet. Analyse a chapter and it will appear here.
      </p>
    );
  }
  return (
    <ul className="ws-job-list">
      {jobs.map((job) => {
        const done = STAGE_ORDER.filter((s) => job.stages[s] === "done").length;
        return (
          <li key={job.job_id} className="ws-job-row">
            <button
              className={`ws-job${job.job_id === selected ? " ws-job-selected" : ""}`}
              onClick={() => onSelect(job.job_id)}
            >
              <span className="ws-job-id">{job.job_id}</span>
              <span className="ws-job-meta">
                {done}/{STAGE_ORDER.length} stages
                {job.segments_recorded > 0 &&
                  ` · ${job.segments_recorded} takes`}
              </span>
            </button>
            <button
              className="ws-job-delete"
              title="Delete this run"
              onClick={() => onDelete(job.job_id)}
            >
              ×
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function StageStrip({
  job,
  active,
  onPick,
}: {
  job: JobDetail;
  active: StageName | null;
  onPick: (stage: StageName | null) => void;
}) {
  return (
    <div className="ws-stages">
      {STAGE_ORDER.map((stage, i) => {
        const detail = job.stage_detail[stage];
        const status = detail?.status ?? "not run";
        const extras = detail
          ? Object.entries(detail)
              .filter(([k]) => !["status", "at", "artifact"].includes(k))
              .map(([k, v]) => `${k}=${v}`)
              .join(" · ")
          : "";
        return (
          <button
            key={stage}
            className={`ws-stage ${stageClass(detail?.status)}${
              active === stage ? " ws-stage-active" : ""
            }`}
            onClick={() => onPick(active === stage ? null : stage)}
            title={extras || status}
          >
            <span className="ws-stage-index">{String(i).padStart(2, "0")}</span>
            <span className="ws-stage-name">{STAGE_LABELS[stage]}</span>
            <span className="ws-stage-status">{status}</span>
          </button>
        );
      })}
    </div>
  );
}

function ArtifactView({ jobId, stage }: { jobId: string; stage: StageName }) {
  const [data, setData] = useState<StageArtifacts | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setData(null);
    setError("");
    getStageArtifacts(jobId, stage)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(formatError(e)));
    return () => {
      live = false;
    };
  }, [jobId, stage]);

  if (error)
    return (
      <p className="ws-error">
        {stage}: {error}
      </p>
    );
  if (!data) return <p className="ws-empty">Loading {stage}…</p>;

  if (data.files) {
    return (
      <div className="ws-artifact">
        <p className="ws-empty">{data.files.length} file(s) in this stage</p>
        <pre>{data.files.join("\n")}</pre>
      </div>
    );
  }
  return (
    <div className="ws-artifact">
      {Object.entries(data.artifacts ?? {}).map(([name, body]) => (
        <details key={name} open>
          <summary>{name}</summary>
          <pre>{JSON.stringify(body, null, 1)}</pre>
        </details>
      ))}
    </div>
  );
}

function SegmentRow({
  jobId,
  segment,
  marked,
  onToggleMark,
}: {
  jobId: string;
  segment: WorkspaceSegment;
  marked: boolean;
  onToggleMark: (id: number) => void;
}) {
  const qa = segment.qa;
  const flagged = qa && (qa.status === "failed" || qa.status === "suspect");
  const rewritten = segment.spoken_text !== segment.original_text;

  return (
    <tr className={flagged ? "ws-row-flagged" : undefined}>
      <td className="ws-col-id">{segment.id}</td>
      <td className="ws-col-speaker">{segment.speaker}</td>
      <td className="ws-col-text">
        <span>{segment.spoken_text}</span>
        {rewritten && (
          <details className="ws-original">
            <summary>as written</summary>
            <span>{segment.original_text}</span>
          </details>
        )}
      </td>
      <td className="ws-col-qa">
        {flagged ? (
          <span className={`ws-badge ws-badge-${qa!.status}`}>
            {qa!.status}
            {qa!.similarity !== undefined && ` ${qa!.similarity.toFixed(2)}`}
          </span>
        ) : (
          <span className="ws-badge-none">—</span>
        )}
        {qa?.heard && <div className="ws-heard">heard: {qa.heard}</div>}
      </td>
      <td className="ws-col-audio">
        {segment.clip.present ? (
          <audio
            controls
            preload="none"
            src={segmentAudioUrl(jobId, segment.id)}
          />
        ) : (
          <span className="ws-badge-none">no take</span>
        )}
      </td>
      <td className="ws-col-redo">
        <label className="ws-redo">
          <input
            type="checkbox"
            checked={marked}
            onChange={() => onToggleMark(segment.id)}
            disabled={!segment.clip.present}
          />
          redo
        </label>
      </td>
    </tr>
  );
}

export function WorkspaceBrowser({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [segments, setSegments] = useState<WorkspaceSegment[]>([]);
  const [total, setTotal] = useState(0);
  const [stage, setStage] = useState<StageName | null>(null);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [speaker, setSpeaker] = useState("");
  const [marked, setMarked] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const refreshJobs = useCallback(() => {
    listJobs()
      .then(setJobs)
      .catch((e) => setError(formatError(e)));
  }, []);

  const clearSelection = () => {
    setSelected(null);
    setJob(null);
    setSegments([]);
  };

  const removeJob = async (id: string) => {
    if (!window.confirm(`Delete run ${id}? The segment audio itself is kept.`))
      return;
    try {
      await deleteJob(id);
      if (selected === id) clearSelection();
      setNotice(`Deleted ${id}.`);
      refreshJobs();
    } catch (e) {
      setError(formatError(e));
    }
  };

  const removeAllJobs = async () => {
    if (
      !window.confirm(
        `Delete all ${jobs.length} run(s)? This clears the list only; the segment ` +
          "audio in the shared volume is kept.",
      )
    )
      return;
    try {
      const { count } = await deleteAllJobs();
      clearSelection();
      setNotice(`Deleted ${count} run(s).`);
      refreshJobs();
    } catch (e) {
      setError(formatError(e));
    }
  };

  useEffect(() => {
    if (open) refreshJobs();
  }, [open, refreshJobs]);

  useEffect(() => {
    if (!selected) return;
    setError("");
    setMarked(new Set());
    getJob(selected)
      .then(setJob)
      .catch((e) => setError(formatError(e)));
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    listSegments(selected, {
      failed: onlyFlagged,
      speaker: speaker || undefined,
    })
      .then((r) => {
        setSegments(r.segments);
        setTotal(r.total);
      })
      .catch((e) => {
        setSegments([]);
        setError(formatError(e));
      });
  }, [selected, onlyFlagged, speaker]);

  const speakers = useMemo(
    () => Array.from(new Set(segments.map((s) => s.speaker))).sort(),
    [segments],
  );

  const toggleMark = (id: number) =>
    setMarked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const submitRedo = async () => {
    if (!selected || marked.size === 0) return;
    setBusy(true);
    setNotice("");
    try {
      const res = await redoSegments(
        selected,
        Array.from(marked).sort((a, b) => a - b),
      );
      setNotice(
        `Marked ${res.marked.length} line(s). They will be rendered on the next ` +
          "synthesis run; everything else is reused.",
      );
      setMarked(new Set());
      const refreshed = await listSegments(selected, {
        failed: onlyFlagged,
        speaker: speaker || undefined,
      });
      setSegments(refreshed.segments);
    } catch (e) {
      setError(formatError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="ws-overlay" role="dialog" aria-label="Workspace browser">
      <div className="ws-panel">
        <header className="ws-header">
          <h2>Workspace</h2>
          <div className="ws-header-actions">
            <button onClick={refreshJobs}>Refresh</button>
            <button
              onClick={removeAllJobs}
              disabled={jobs.length === 0}
              title="Remove every run from this list"
            >
              Clear all
            </button>
            <button onClick={onClose}>Close</button>
          </div>
        </header>

        {error && <p className="ws-error">{error}</p>}

        <div className="ws-body">
          <aside className="ws-sidebar">
            <h3>Runs</h3>
            <JobList
              jobs={jobs}
              selected={selected}
              onSelect={setSelected}
              onDelete={removeJob}
            />
          </aside>

          <section className="ws-main">
            {!job && (
              <p className="ws-empty">Pick a run to see what it produced.</p>
            )}

            {job && (
              <>
                <StageStrip job={job} active={stage} onPick={setStage} />
                {stage && <ArtifactView jobId={job.job_id} stage={stage} />}

                <div className="ws-filters">
                  <label>
                    <input
                      type="checkbox"
                      checked={onlyFlagged}
                      onChange={(e) => setOnlyFlagged(e.target.checked)}
                    />
                    only what QA flagged
                  </label>
                  <select
                    value={speaker}
                    onChange={(e) => setSpeaker(e.target.value)}
                  >
                    <option value="">every speaker</option>
                    {speakers.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <span className="ws-count">
                    showing {segments.length} of {total}
                  </span>
                  <button
                    className="ws-redo-btn"
                    disabled={marked.size === 0 || busy}
                    onClick={submitRedo}
                  >
                    {busy ? "Marking…" : `Redo ${marked.size || ""}`.trim()}
                  </button>
                </div>

                {notice && <p className="ws-notice">{notice}</p>}

                {segments.length === 0 ? (
                  <p className="ws-empty">
                    Nothing to show. {onlyFlagged && "QA flagged nothing here."}
                  </p>
                ) : (
                  <table className="ws-segments">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>speaker</th>
                        <th>spoken</th>
                        <th>qa</th>
                        <th>take</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {segments.map((s) => (
                        <SegmentRow
                          key={s.id}
                          jobId={job.job_id}
                          segment={s}
                          marked={marked.has(s.id)}
                          onToggleMark={toggleMark}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
