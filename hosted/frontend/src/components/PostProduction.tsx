import { useState, useCallback } from 'react';
import { Voice, Segment, ClipInfo, reSynthesize, reStitch, ReSynthesizeRequest } from '../api';

const EMOTIONS = ['neutral', 'happy', 'sad', 'angry', 'fearful', 'excited', 'tense', 'contemplative', 'curious'];
const QWEN_VOICES = ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ryan', 'Aiden', 'Ono_Anna', 'Sohee'];

type SegStatus = 'clean' | 'modified' | 'busy' | 'done' | 'error';

interface SegState {
  emotion: string;
  intensity: number;
  engine: string;
  voice: string;   // xtts filename or qwen voice name
  speed: number;
  status: SegStatus;
  error?: string;
}

interface Props {
  segments: Segment[];
  clips: ClipInfo[];
  voiceMapping: Record<string, string>;
  engineMapping: Record<string, string>;
  voices: Voice[];
  outputFile: string;
  onClipsChange: (clips: ClipInfo[]) => void;
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

  const filtered = segments.filter(s => {
    if (!search) return true;
    const q = search.toLowerCase();
    return searchBy === 'content'
      ? s.original_text.toLowerCase().includes(q)
      : s.speaker.toLowerCase().includes(q);
  });

  const modifiedIds = Object.entries(segStates)
    .filter(([, s]) => s.status === 'modified')
    .map(([id]) => Number(id));

  // ── Re-synthesize a single segment ──────────────────────────
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
      // Update the clips list with the new file path
      onClipsChange(clips.map(c =>
        c.id === seg.id ? { ...c, file_path: result.file_path } : c,
      ));
      updateSeg(seg.id, { status: 'done' });
    } catch (e) {
      updateSeg(seg.id, {
        status: 'error',
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }, [segStates, clips, updateSeg, onClipsChange]);

  // ── Re-synthesize all modified segments sequentially ────────
  const handleReSynthAll = useCallback(async () => {
    for (const id of modifiedIds) {
      const seg = segments.find(s => s.id === id);
      if (seg) await handleReSynth(seg);
    }
  }, [modifiedIds, segments, handleReSynth]);

  // ── Re-stitch final audio ───────────────────────────────────
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
      setStitchMsg('Stitch failed: ' + (e instanceof Error ? e.message : String(e)));
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
          onChange={e => setSearch(e.target.value)}
        />
        <div className="pp-search-mode">
          <label className="pp-radio">
            <input
              type="radio"
              name="searchBy"
              checked={searchBy === 'content'}
              onChange={() => setSearchBy('content')}
            />
            Content
          </label>
          <label className="pp-radio">
            <input
              type="radio"
              name="searchBy"
              checked={searchBy === 'character'}
              onChange={() => setSearchBy('character')}
            />
            Character
          </label>
        </div>
      </div>

      <div className="pp-list">
        {filtered.map(seg => {
          const st = segStates[seg.id];
          if (!st) return null;
          const isQwen = st.engine === 'qwen3-tts';

          return (
            <div key={seg.id} className={`pp-seg pp-seg--${st.status}`}>
              <div className="pp-seg-header">
                <span className="pp-seg-id">#{seg.id}</span>
                <span className="pp-seg-speaker">{seg.speaker}</span>
                <span className={`pp-seg-badge pp-seg-badge--${st.status}`}>
                  {st.status === 'clean' && '\u2014'}
                  {st.status === 'modified' && 'modified'}
                  {st.status === 'busy' && 'synth...'}
                  {st.status === 'done' && 'done'}
                  {st.status === 'error' && 'error'}
                </span>
              </div>

              <div className="pp-seg-text">
                {seg.original_text.length > 120
                  ? seg.original_text.slice(0, 120) + '...'
                  : seg.original_text}
              </div>

              <div className="pp-seg-controls">
                {/* Emotion */}
                <label className="pp-ctrl">
                  <span>Emotion</span>
                  <select
                    value={st.emotion}
                    onChange={e => updateSeg(seg.id, { emotion: e.target.value })}
                    disabled={st.status === 'busy'}
                  >
                    {EMOTIONS.map(em => (
                      <option key={em} value={em}>{em}</option>
                    ))}
                  </select>
                </label>

                {/* Intensity */}
                <label className="pp-ctrl">
                  <span>Intensity {st.intensity.toFixed(1)}</span>
                  <input
                    type="range"
                    min={0} max={1} step={0.1}
                    value={st.intensity}
                    onChange={e => updateSeg(seg.id, { intensity: parseFloat(e.target.value) })}
                    disabled={st.status === 'busy'}
                  />
                </label>

                {/* Engine toggle */}
                <div className="pp-ctrl">
                  <span>Engine</span>
                  <div className="engine-toggle">
                    <button
                      className={!isQwen ? 'active' : ''}
                      onClick={() => {
                        const defaultVoice = voiceMapping[seg.speaker] ?? (voices[0]?.filename || '');
                        updateSeg(seg.id, { engine: 'xtts-v2', voice: defaultVoice });
                      }}
                      disabled={st.status === 'busy'}
                    >
                      XTTS&#8209;v2
                    </button>
                    <button
                      className={isQwen ? 'active' : ''}
                      onClick={() => updateSeg(seg.id, { engine: 'qwen3-tts', voice: 'Ryan' })}
                      disabled={st.status === 'busy'}
                    >
                      Qwen3
                    </button>
                  </div>
                </div>

                {/* Voice selector */}
                <label className="pp-ctrl">
                  <span>Voice</span>
                  <select
                    value={st.voice}
                    onChange={e => updateSeg(seg.id, { voice: e.target.value })}
                    disabled={st.status === 'busy'}
                  >
                    {isQwen
                      ? QWEN_VOICES.map(v => <option key={v} value={v}>{v}</option>)
                      : voices.map(v => <option key={v.filename} value={v.filename}>{v.name}</option>)
                    }
                  </select>
                </label>

                {/* Speed */}
                <label className="pp-ctrl">
                  <span>Speed {st.speed.toFixed(1)}</span>
                  <input
                    type="range"
                    min={0.5} max={2.0} step={0.1}
                    value={st.speed}
                    onChange={e => updateSeg(seg.id, { speed: parseFloat(e.target.value) })}
                    disabled={st.status === 'busy'}
                  />
                </label>

                {/* Re-synth button */}
                <button
                  className="pp-resynth-btn"
                  onClick={() => handleReSynth(seg)}
                  disabled={st.status === 'busy' || st.status === 'clean'}
                >
                  {st.status === 'busy' ? 'Synth...' : 'Re-synth'}
                </button>
              </div>

              {st.error && <div className="pp-seg-error">{st.error}</div>}
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div className="pp-empty">No segments match your search.</div>
        )}
      </div>

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
