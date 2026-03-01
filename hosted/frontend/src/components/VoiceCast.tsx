import { useState, useEffect } from 'react';
import { Voice, Segment, voiceUrl } from '../api';
import { QWEN_VOICES, QWEN_DEFAULT, Engine } from '../constants/engines';
import { useAudioPreview } from '../hooks/useAudioPreview';
import './VoiceCast.css';

interface Props {
  segments: Segment[];
  voices: Voice[];
  onGenerate: (voiceMapping: Record<string, string>, engineMapping: Record<string, string>, segments: Segment[]) => void;
  disabled?: boolean;
}

function pickDefault(speaker: string, index: number, voices: Voice[]): string {
  if (voices.length === 0) return 'generic_neutral.wav';
  const lower = speaker.toLowerCase();
  const exact = voices.find(v => v.name.toLowerCase() === lower);
  return exact ? exact.filename : voices[index % voices.length].filename;
}

export function VoiceCast({ segments, voices, onGenerate, disabled = false }: Props) {
  const speakers = [...new Set(segments.map(s => s.speaker).filter(Boolean))];
  const displayVoices = voices.length > 0 ? voices : [{ name: 'generic_neutral', filename: 'generic_neutral.wav' }];

  const [engines, setEngines] = useState<Record<string, Engine>>(() => {
    const init: Record<string, Engine> = {};
    speakers.forEach(sp => { init[sp] = 'xtts-v2'; });
    return init;
  });

  const [selected, setSelected] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    speakers.forEach((sp, i) => { init[sp] = pickDefault(sp, i, displayVoices); });
    return init;
  });

  const [editJson, setEditJson]       = useState(() => JSON.stringify(segments, null, 2));
  const [jsonError, setJsonError]     = useState<string | null>(null);
  const { playing, togglePreview, stopPreview } = useAudioPreview();

  useEffect(() => {
    setEditJson(JSON.stringify(segments, null, 2));
    setJsonError(null);
  }, [segments]);

  function switchEngine(speaker: string, engine: Engine) {
    stopPreview();
    setEngines(prev => ({ ...prev, [speaker]: engine }));
    setSelected(prev => ({
      ...prev,
      [speaker]: engine === 'qwen3-tts'
        ? QWEN_DEFAULT
        : pickDefault(speaker, speakers.indexOf(speaker), displayVoices),
    }));
  }

  function handleJsonChange(value: string) {
    setEditJson(value);
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch (e) {
      setJsonError((e as Error).message);
    }
  }

  function handleReset(e: React.MouseEvent) {
    e.stopPropagation();
    setEditJson(JSON.stringify(segments, null, 2));
    setJsonError(null);
  }

  function handleGenerate() {
    stopPreview();
    if (jsonError) return;
    let parsedSegments: Segment[] = segments;
    try {
      const parsed = JSON.parse(editJson);
      if (Array.isArray(parsed)) parsedSegments = parsed as Segment[];
    } catch { /* blocked by jsonError check above */ }
    onGenerate(selected, engines, parsedSegments);
  }

  return (
    <div className="card">
      <h2>Voice Cast</h2>
      <p className="subtitle">Choose an engine and voice for each character, then generate.</p>

      {speakers.map((speaker) => {
        const engine = engines[speaker] ?? 'xtts-v2';
        const isQwen = engine === 'qwen3-tts';

        return (
          <div key={speaker} className="cast-row">
            <div className="cast-speaker-col">
              <div className="cast-speaker">{speaker}</div>
              <div className="engine-toggle">
                <button
                  className={!isQwen ? 'active' : ''}
                  onClick={() => switchEngine(speaker, 'xtts-v2')}
                  title="Voice cloning from reference audio"
                >
                  XTTS&#8209;v2
                </button>
                <button
                  className={isQwen ? 'active' : ''}
                  onClick={() => switchEngine(speaker, 'qwen3-tts')}
                  title="9 predefined voices with emotion control"
                >
                  Qwen3
                </button>
              </div>
            </div>

            <div className="voice-chips">
              {isQwen
                ? QWEN_VOICES.map(v => {
                    const isSelected = selected[speaker] === v;
                    const qwenFile = `${v}.wav`;
                    const isPlaying = playing === `qwen3/${qwenFile}`;
                    return (
                      <div
                        key={v}
                        className={`voice-chip qwen-chip${isSelected ? ' selected' : ''}${isPlaying ? ' playing' : ''}`}
                        onClick={() => setSelected(prev => ({ ...prev, [speaker]: v }))}
                      >
                        <button
                          className="chip-play"
                          title={`Preview ${v}`}
                          onClick={e => { e.stopPropagation(); togglePreview(`qwen3/${qwenFile}`, voiceUrl('qwen3', qwenFile)); }}
                        >
                          {isPlaying ? '\u23F9' : '\u25B6'}
                        </button>
                        <span className="chip-name">{v.replace(/_/g, '\u00a0')}</span>
                      </div>
                    );
                  })
                : displayVoices.map(v => {
                    const isSelected = selected[speaker] === v.filename;
                    const isPlaying  = playing === `xtts/${v.filename}`;
                    return (
                      <div
                        key={v.filename}
                        className={`voice-chip${isSelected ? ' selected' : ''}${isPlaying ? ' playing' : ''}`}
                        onClick={() => setSelected(prev => ({ ...prev, [speaker]: v.filename }))}
                      >
                        <button
                          className="chip-play"
                          title={`Preview ${v.name}`}
                          onClick={e => { e.stopPropagation(); togglePreview(`xtts/${v.filename}`, voiceUrl('xtts', v.filename)); }}
                        >
                          {isPlaying ? '\u23F9' : '\u25B6'}
                        </button>
                        <span className="chip-name">{v.name}</span>
                      </div>
                    );
                  })
              }
            </div>
          </div>
        );
      })}

      <details className="segments-editor">
        <summary className="segments-editor-summary">
          Segments JSON
          <button className="segments-reset-btn" onClick={handleReset} disabled={disabled}>
            Reset
          </button>
        </summary>
        <div className="segments-editor-body">
          <textarea
            className="segments-json-textarea"
            value={editJson}
            onChange={e => handleJsonChange(e.target.value)}
            spellCheck={false}
            disabled={disabled}
          />
          {jsonError && <div className="segments-json-error">&#9888; {jsonError}</div>}
        </div>
      </details>

      <div className="cast-actions">
        <button className="btn-primary" onClick={handleGenerate} disabled={disabled || !!jsonError}>
          {disabled ? 'Synthesizing\u2026' : 'Generate Audiobook'}
        </button>
      </div>
    </div>
  );
}
