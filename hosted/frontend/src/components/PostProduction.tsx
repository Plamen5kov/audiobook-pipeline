import { useState, useCallback } from 'react';
import { Voice, Segment, ClipInfo, reSynthesize, reStitch, ReSynthesizeRequest } from '../api';
import { formatError } from '../utils/formatError';
import { SegmentCard, SegState } from './SegmentCard';
import './PostProduction.css';

interface Props {
  segments: Segment[];
  clips: ClipInfo[];
  voiceMapping: Record<string, string>;
  engineMapping: Record<string, string>;
  voices: Voice[];
  outputFile: string;
  onClipsChange: (updater: ClipInfo[] | ((prev: ClipInfo[]) => ClipInfo[])) => void;
  onOutputFileChange: (f: string) => void;
}

function buildInitial(
  segments: Segment[],
  voiceMapping: Record<string, string>,
  engineMapping: Record<string, string>,
): Record<number, SegState> {
  const out: Record<number, SegState> = {};
  for (const seg of segments) {
    const engine = engineMapping[seg.speaker] ?? 'xtts-v2';
    out[seg.id] = {
      emotion: seg.emotion ?? 'neutral',
      intensity: seg.intensity ?? 0.5,
      engine,
      voice: voiceMapping[seg.speaker] ?? '',
      speed: 1.0,
      status: 'clean',
    };
  }
  return out;
}

export function PostProduction({
  segments, clips, voiceMapping, engineMapping, voices, outputFile,
  onClipsChange, onOutputFileChange,
}: Props) {
  const [search, setSearch] = useState('');
  const [searchBy, setSearchBy] = useState<'content' | 'character'>('content');
  const [segStates, setSegStates] = useState<Record<number, SegState>>(() =>
    buildInitial(segments, voiceMapping, engineMapping),
  );
  const [page, setPage] = useState(0);
  const [stitching, setStitching] = useState(false);
  const [stitchMsg, setStitchMsg] = useState('');

  const updateSeg = useCallback((id: number, patch: Partial<SegState>) => {
    setSegStates(prev => ({
      ...prev,
      [id]: {
        ...prev[id],
        ...patch,
        status: patch.status ?? 'modified',
      },
    }));
  }, []);

  const PAGE_SIZE = 5;

  const filtered = segments.filter(s => {
    if (!search) return true;
    const q = search.toLowerCase();
    return searchBy === 'content'
      ? s.original_text.toLowerCase().includes(q)
      : s.speaker.toLowerCase().includes(q);
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const modifiedIds = Object.entries(segStates)
    .filter(([, s]) => s.status === 'modified')
    .map(([id]) => Number(id));

  // Bug fix: use functional updater to avoid stale closure on `clips`
  const handleReSynth = useCallback(async (seg: Segment) => {
    const st = segStates[seg.id];
    if (!st) return;

    updateSeg(seg.id, { status: 'busy' });

    const isQwen = st.engine === 'qwen3-tts';
    const params: ReSynthesizeRequest = {
      text: seg.original_text,
      segment_id: seg.id,
      speaker: seg.speaker,
      engine: st.engine,
      reference_audio_path: isQwen ? '' : `/voices/xtts/${st.voice}`,
      qwen_speaker: isQwen ? st.voice : '',
      emotion: st.emotion,
      intensity: st.intensity,
      speed: st.speed,
    };

    try {
      const result = await reSynthesize(params);
      onClipsChange(prev => prev.map(c =>
        c.id === seg.id ? { ...c, file_path: result.file_path } : c,
      ));
      updateSeg(seg.id, { status: 'done' });
    } catch (e) {
      updateSeg(seg.id, {
        status: 'error',
        error: formatError(e),
      });
    }
  }, [segStates, updateSeg, onClipsChange]);

  const handleReSynthAll = useCallback(async () => {
    for (const id of modifiedIds) {
      const seg = segments.find(s => s.id === id);
      if (seg) await handleReSynth(seg);
    }
  }, [modifiedIds, segments, handleReSynth]);

  const handleReStitch = useCallback(async () => {
    setStitching(true);
    setStitchMsg('');
    try {
      const result = await reStitch({
        clips,
        output_filename: outputFile,
      });
      onOutputFileChange(result.filename);
      setStitchMsg(`Stitched ${result.clips_count} clips (${(result.duration_ms / 1000).toFixed(1)}s)`);
    } catch (e) {
      setStitchMsg('Stitch failed: ' + formatError(e));
    } finally {
      setStitching(false);
    }
  }, [clips, outputFile, onOutputFileChange]);

  const anyBusy = Object.values(segStates).some(s => s.status === 'busy');

  return (
    <div className="card pp">
      <h2>Post Production</h2>
      <p className="subtitle">Tweak individual segments, re-synthesize, then re-stitch the final audio.</p>

      <div className="pp-search-row">
        <input
          type="text"
          className="pp-search"
          placeholder={searchBy === 'content' ? 'Search by content...' : 'Search by character...'}
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(0); }}
        />
        <div className="pp-search-mode">
          <label className="pp-radio">
            <input
              type="radio"
              name="searchBy"
              checked={searchBy === 'content'}
              onChange={() => { setSearchBy('content'); setPage(0); }}
            />
            Content
          </label>
          <label className="pp-radio">
            <input
              type="radio"
              name="searchBy"
              checked={searchBy === 'character'}
              onChange={() => { setSearchBy('character'); setPage(0); }}
            />
            Character
          </label>
        </div>
      </div>

      <div className="pp-list">
        {paged.map(seg => {
          const st = segStates[seg.id];
          if (!st) return null;
          return (
            <SegmentCard
              key={seg.id}
              seg={seg}
              st={st}
              voiceMapping={voiceMapping}
              voices={voices}
              onUpdate={updateSeg}
              onReSynth={handleReSynth}
            />
          );
        })}

        {filtered.length === 0 && (
          <div className="pp-empty">No segments match your search.</div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="pp-pager">
          <button
            className="pp-pager-btn"
            disabled={safePage === 0}
            onClick={() => setPage(safePage - 1)}
          >
            Prev
          </button>
          <span className="pp-pager-info">
            {safePage * PAGE_SIZE + 1}&ndash;{Math.min((safePage + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}
          </span>
          <button
            className="pp-pager-btn"
            disabled={safePage >= totalPages - 1}
            onClick={() => setPage(safePage + 1)}
          >
            Next
          </button>
        </div>
      )}

      <div className="pp-actions">
        {modifiedIds.length > 0 && (
          <button
            className="pp-batch-btn"
            onClick={handleReSynthAll}
            disabled={anyBusy}
          >
            Re-synth All Modified ({modifiedIds.length})
          </button>
        )}

        <button
          className="pp-stitch-btn"
          onClick={handleReStitch}
          disabled={stitching || anyBusy}
        >
          {stitching ? 'Stitching...' : 'Re-stitch Final'}
        </button>
      </div>

      {stitchMsg && <div className="pp-stitch-msg">{stitchMsg}</div>}
    </div>
  );
}
