import { useState, useRef, useCallback } from 'react';
import { Voice, Segment, voiceUrl } from '../api';

const QWEN_VOICES = ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ryan', 'Aiden', 'Ono_Anna', 'Sohee'];
const QWEN_DEFAULT = 'Ryan';

type Engine = 'xtts-v2' | 'qwen3-tts';

interface Props {
  segments: Segment[];
  voices: Voice[];
  onGenerate: (voiceMapping: Record<string, string>, engineMapping: Record<string, string>) => void;
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

  // engines[speaker] = which TTS engine is active for that speaker
  const [engines, setEngines] = useState<Record<string, Engine>>(() => {
    const init: Record<string, Engine> = {};
    speakers.forEach(sp => { init[sp] = 'xtts-v2'; });
    return init;
  });

  // selected[speaker] = WAV filename (xtts-v2) OR Qwen voice name (qwen3-tts)
  const [selected, setSelected] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    speakers.forEach((sp, i) => { init[sp] = pickDefault(sp, i, displayVoices); });
    return init;
  });

  // playing = WAV filename currently previewing (xtts-v2 only)
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(new Audio());

  const togglePreview = useCallback((filename: string) => {
    const audio = audioRef.current;
    if (playing === filename && !audio.paused) {
      audio.pause();
      setPlaying(null);
    } else {
      audio.pause();
      audio.src = voiceUrl(filename);
      audio.play().catch(() => {});
      setPlaying(filename);
      audio.onended = () => setPlaying(null);
    }
  }, [playing]);

  function switchEngine(speaker: string, engine: Engine) {
    audioRef.current.pause();
    setPlaying(null);
    setEngines(prev => ({ ...prev, [speaker]: engine }));
    setSelected(prev => ({
      ...prev,
      [speaker]: engine === 'qwen3-tts'
        ? QWEN_DEFAULT
        : pickDefault(speaker, speakers.indexOf(speaker), displayVoices),
    }));
  }

  function handleGenerate() {
    audioRef.current.pause();
    onGenerate(selected, engines);
  }

  return (
    <div className="card">
      <h2>Voice Cast</h2>
      <p className="subtitle">Choose an engine and voice for each character, then generate.</p>

      {speakers.map((speaker, si) => {
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
                    return (
                      <div
                        key={v}
                        className={`voice-chip qwen-chip${isSelected ? ' selected' : ''}`}
                        onClick={() => setSelected(prev => ({ ...prev, [speaker]: v }))}
                      >
                        <span className="chip-name">{v.replace(/_/g, '\u00a0')}</span>
                      </div>
                    );
                  })
                : displayVoices.map(v => {
                    const isSelected = selected[speaker] === v.filename;
                    const isPlaying  = playing === v.filename;
                    return (
                      <div
                        key={v.filename}
                        className={`voice-chip${isSelected ? ' selected' : ''}${isPlaying ? ' playing' : ''}`}
                        onClick={() => setSelected(prev => ({ ...prev, [speaker]: v.filename }))}
                      >
                        <button
                          className="chip-play"
                          title={`Preview ${v.name}`}
                          onClick={e => { e.stopPropagation(); togglePreview(v.filename); }}
                        >
                          {isPlaying ? '⏹' : '▶'}
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

      <div className="cast-actions">
        <button className="btn-primary" onClick={handleGenerate} disabled={disabled}>
          {disabled ? 'Synthesizing…' : 'Generate Audiobook'}
        </button>
      </div>
    </div>
  );
}
