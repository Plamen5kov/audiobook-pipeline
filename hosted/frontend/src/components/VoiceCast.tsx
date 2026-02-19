import { useState, useRef, useCallback } from 'react';
import { Voice, Segment, voiceUrl } from '../api';

interface Props {
  segments: Segment[];
  voices: Voice[];
  onGenerate: (voiceMapping: Record<string, string>) => void;
}

function pickDefault(speaker: string, index: number, voices: Voice[]): string {
  if (voices.length === 0) return 'generic_neutral.wav';
  const lower = speaker.toLowerCase();
  const exact = voices.find(v => v.name.toLowerCase() === lower);
  return exact ? exact.filename : voices[index % voices.length].filename;
}

export function VoiceCast({ segments, voices, onGenerate }: Props) {
  const speakers = [...new Set(segments.map(s => s.speaker).filter(Boolean))];
  const displayVoices = voices.length > 0 ? voices : [{ name: 'generic_neutral', filename: 'generic_neutral.wav' }];

  // selected[speaker] = filename
  const [selected, setSelected] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    speakers.forEach((sp, i) => { init[sp] = pickDefault(sp, i, displayVoices); });
    return init;
  });

  // playing = filename currently previewing, or null
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

  function handleGenerate() {
    audioRef.current.pause();
    onGenerate(selected);
  }

  return (
    <div className="card">
      <h2>Voice Cast</h2>
      <p className="subtitle">Assign a reference voice to each character, then start synthesis.</p>

      {speakers.map((speaker, si) => (
        <div key={speaker} className="cast-row">
          <div className="cast-speaker">{speaker}</div>
          <div className="voice-chips">
            {displayVoices.map(v => {
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
            })}
          </div>
        </div>
      ))}

      <div className="cast-actions">
        <button className="btn-primary" onClick={handleGenerate}>
          Generate Audiobook
        </button>
      </div>
    </div>
  );
}
