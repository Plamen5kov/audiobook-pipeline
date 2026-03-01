import { useState, useRef, useCallback, useEffect } from 'react';
import { Voice, fetchVoices, uploadVoice, deleteVoice } from '../api';
import { formatError } from '../utils/formatError';
import { VoiceRecorder } from './VoiceRecorder';
import { VoiceList } from './VoiceList';
import './VoiceManager.css';

interface Props {
  open: boolean;
  onClose: () => void;
  onVoicesChanged: (voices: Voice[]) => void;
}

export function VoiceManager({ open, onClose, onVoicesChanged }: Props) {
  const [voices, setVoices]       = useState<Voice[]>([]);
  const [loading, setLoading]     = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError]         = useState('');
  const [success, setSuccess]     = useState('');
  const [dragOver, setDragOver]   = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await fetchVoices('xtts');
      setVoices(list);
      onVoicesChanged(list);
    } catch {
      setError('Failed to load voices');
    } finally {
      setLoading(false);
    }
  }, [onVoicesChanged]);

  useEffect(() => {
    if (open) {
      setLoading(true);
      setError('');
      setSuccess('');
      refresh();
    }
  }, [open, refresh]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  // Lock body scroll while open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = ''; };
    }
  }, [open]);

  async function handleFiles(files: FileList | File[]) {
    const wavFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.wav'));
    if (wavFiles.length === 0) {
      setError('Only .wav files are accepted');
      return;
    }
    setError('');
    setSuccess('');
    setUploading(true);
    try {
      for (const file of wavFiles) {
        await uploadVoice('xtts', file);
      }
      setSuccess(`Uploaded ${wavFiles.length} voice${wavFiles.length > 1 ? 's' : ''}`);
      await refresh();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  async function handleDelete(filename: string) {
    setError('');
    setSuccess('');
    try {
      await deleteVoice('xtts', filename);
      await refresh();
      setSuccess('Voice deleted');
    } catch (e) {
      setError(formatError(e));
    }
  }

  if (!open) return null;

  return (
    <div className="vm-overlay" onClick={onClose}>
      <div className="vm-panel" onClick={e => e.stopPropagation()}>
        <div className="vm-header">
          <h2>Voice Manager</h2>
          <button className="vm-close" onClick={onClose}>&times;</button>
        </div>

        {error && <div className="vm-error">{error}</div>}
        {success && <div className="vm-success">{success}</div>}

        <div
          className={`vm-dropzone${dragOver ? ' vm-dropzone--active' : ''}`}
          onDragOver={e => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={e => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          onClick={() => fileRef.current?.click()}
        >
          <p>Drag &amp; drop WAV files here, or</p>
          <button
            className="vm-dropzone-btn"
            onClick={e => { e.stopPropagation(); fileRef.current?.click(); }}
          >
            Browse Files
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".wav"
            multiple
            hidden
            onChange={e => e.target.files && handleFiles(e.target.files)}
          />
        </div>

        <VoiceRecorder
          onVoicesChanged={(list) => { setVoices(list); onVoicesChanged(list); }}
          onSuccess={setSuccess}
          onError={setError}
        />

        {uploading && <div className="vm-status">Uploading...</div>}

        <VoiceList
          voices={voices}
          loading={loading}
          onDelete={handleDelete}
        />
      </div>
    </div>
  );
}
