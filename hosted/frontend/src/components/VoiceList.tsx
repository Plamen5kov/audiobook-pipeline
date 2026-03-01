import { useState } from 'react';
import { Voice } from '../api';
import { useAudioPreview } from '../hooks/useAudioPreview';
import { voiceUrl } from '../api';

interface Props {
  voices: Voice[];
  loading: boolean;
  onDelete: (filename: string) => void;
}

export function VoiceList({ voices, loading, onDelete }: Props) {
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const { playing, togglePreview } = useAudioPreview();

  return (
    <div className="vm-voice-list">
      {loading && <div className="vm-status">Loading voices...</div>}
      {!loading && voices.length === 0 && <div className="vm-status">No voices found</div>}
      {voices.map(v => (
        <div key={v.filename} className="vm-voice-item">
          <button
            className="chip-play"
            title={`Preview ${v.name}`}
            onClick={() => togglePreview(v.filename, voiceUrl('xtts', v.filename))}
          >
            {playing === v.filename ? '\u23F9' : '\u25B6'}
          </button>
          <span className="vm-voice-name">{v.name}</span>
          {!v.builtin && (
            confirmDelete === v.filename ? (
              <span className="vm-confirm-group">
                <span>Delete?</span>
                <button onClick={() => { setConfirmDelete(null); onDelete(v.filename); }}>Yes</button>
                <button onClick={() => setConfirmDelete(null)}>No</button>
              </span>
            ) : (
              <button
                className="vm-delete-btn"
                title="Delete voice"
                onClick={() => setConfirmDelete(v.filename)}
              >
                &#10005;
              </button>
            )
          )}
        </div>
      ))}
    </div>
  );
}
