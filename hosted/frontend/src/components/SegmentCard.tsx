import { Segment, Voice } from '../api';
import { EMOTIONS, QWEN_VOICES } from '../constants/engines';

export type SegStatus = 'clean' | 'modified' | 'busy' | 'done' | 'error';

export interface SegState {
  emotion: string;
  intensity: number;
  engine: string;
  voice: string;
  speed: number;
  status: SegStatus;
  error?: string;
}

interface Props {
  seg: Segment;
  st: SegState;
  voiceMapping: Record<string, string>;
  voices: Voice[];
  onUpdate: (id: number, patch: Partial<SegState>) => void;
  onReSynth: (seg: Segment) => void;
}

export function SegmentCard({ seg, st, voiceMapping, voices, onUpdate, onReSynth }: Props) {
  const isQwen = st.engine === 'qwen3-tts';

  return (
    <div className={`pp-seg pp-seg--${st.status}`}>
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
        <label className="pp-ctrl">
          <span>Emotion</span>
          <select
            value={st.emotion}
            onChange={e => onUpdate(seg.id, { emotion: e.target.value })}
            disabled={st.status === 'busy'}
          >
            {EMOTIONS.map(em => (
              <option key={em} value={em}>{em}</option>
            ))}
          </select>
        </label>

        <label className="pp-ctrl">
          <span>Intensity {st.intensity.toFixed(1)}</span>
          <input
            type="range"
            min={0} max={1} step={0.1}
            value={st.intensity}
            onChange={e => onUpdate(seg.id, { intensity: parseFloat(e.target.value) })}
            disabled={st.status === 'busy'}
          />
        </label>

        <div className="pp-ctrl">
          <span>Engine</span>
          <div className="engine-toggle">
            <button
              className={!isQwen ? 'active' : ''}
              onClick={() => {
                const defaultVoice = voiceMapping[seg.speaker] ?? (voices[0]?.filename || '');
                onUpdate(seg.id, { engine: 'xtts-v2', voice: defaultVoice });
              }}
              disabled={st.status === 'busy'}
            >
              XTTS&#8209;v2
            </button>
            <button
              className={isQwen ? 'active' : ''}
              onClick={() => onUpdate(seg.id, { engine: 'qwen3-tts', voice: 'Ryan' })}
              disabled={st.status === 'busy'}
            >
              Qwen3
            </button>
          </div>
        </div>

        <label className="pp-ctrl">
          <span>Voice</span>
          <select
            value={st.voice}
            onChange={e => onUpdate(seg.id, { voice: e.target.value })}
            disabled={st.status === 'busy'}
          >
            {isQwen
              ? QWEN_VOICES.map(v => <option key={v} value={v}>{v}</option>)
              : voices.map(v => <option key={v.filename} value={v.filename}>{v.name}</option>)
            }
          </select>
        </label>

        <label className="pp-ctrl">
          <span>Speed {st.speed.toFixed(1)}</span>
          <input
            type="range"
            min={0.5} max={2.0} step={0.1}
            value={st.speed}
            onChange={e => onUpdate(seg.id, { speed: parseFloat(e.target.value) })}
            disabled={st.status === 'busy'}
          />
        </label>

        <button
          className="pp-resynth-btn"
          onClick={() => onReSynth(seg)}
          disabled={st.status === 'busy' || st.status === 'clean'}
        >
          {st.status === 'busy' ? 'Synth...' : 'Re-synth'}
        </button>
      </div>

      {st.error && <div className="pp-seg-error">{st.error}</div>}
    </div>
  );
}
