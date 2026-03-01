import { useState, useRef, useCallback, useEffect } from 'react';
import { Voice, fetchVoices, uploadVoice, deleteVoice, voiceUrl } from '../api';

interface Props {
  open: boolean;
  onClose: () => void;
  onVoicesChanged: (voices: Voice[]) => void;
}

/** Encode raw PCM float samples as a 16-bit mono WAV file. */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const numSamples = samples.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  function writeStr(offset: number, str: string) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);          // subchunk size
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);

  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

export function VoiceManager({ open, onClose, onVoicesChanged }: Props) {
  const [voices, setVoices]             = useState<Voice[]>([]);
  const [loading, setLoading]           = useState(true);
  const [uploading, setUploading]       = useState(false);
  const [error, setError]               = useState('');
  const [success, setSuccess]           = useState('');
  const [playing, setPlaying]           = useState<string | null>(null);
  const [dragOver, setDragOver]         = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Recording state
  const [recording, setRecording]       = useState(false);
  const [recordSecs, setRecordSecs]     = useState(0);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedUrl, setRecordedUrl]   = useState('');
  const [recordName, setRecordName]     = useState('');
  const [playingRecording, setPlayingRecording] = useState(false);

  const audioRef      = useRef<HTMLAudioElement>(new Audio());
  const fileRef       = useRef<HTMLInputElement>(null);
  const recorderRef   = useRef<MediaRecorder | null>(null);
  const chunksRef     = useRef<Blob[]>([]);
  const timerRef      = useRef<ReturnType<typeof setInterval> | null>(null);
  const previewRef    = useRef<HTMLAudioElement>(new Audio());

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
      setConfirmDelete(null);
      refresh();
    } else {
      audioRef.current.pause();
      previewRef.current.pause();
      setPlaying(null);
      setPlayingRecording(false);
      discardRecording();
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

  const togglePreview = useCallback((filename: string) => {
    const audio = audioRef.current;
    if (playing === filename && !audio.paused) {
      audio.pause();
      setPlaying(null);
    } else {
      audio.pause();
      audio.src = voiceUrl('xtts', filename);
      audio.play().catch(() => {});
      setPlaying(filename);
      audio.onended = () => setPlaying(null);
    }
  }, [playing]);

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
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  async function handleDelete(filename: string) {
    setError('');
    setSuccess('');
    setConfirmDelete(null);
    if (playing === filename) {
      audioRef.current.pause();
      setPlaying(null);
    }
    try {
      await deleteVoice('xtts', filename);
      await refresh();
      setSuccess('Voice deleted');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  // ── Recording ────────────────────────────────────────────────

  async function startRecording() {
    setError('');
    setSuccess('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        // Stop all tracks so the mic indicator goes away
        stream.getTracks().forEach(t => t.stop());

        const raw = new Blob(chunksRef.current, { type: recorder.mimeType });

        // Decode to PCM and re-encode as WAV
        try {
          const arrayBuf = await raw.arrayBuffer();
          const ctx = new AudioContext();
          const decoded = await ctx.decodeAudioData(arrayBuf);
          const pcm = decoded.getChannelData(0); // mono
          const wavBlob = encodeWav(pcm, decoded.sampleRate);
          await ctx.close();

          const url = URL.createObjectURL(wavBlob);
          setRecordedBlob(wavBlob);
          setRecordedUrl(url);
          setRecordName('my_voice');
        } catch {
          setError('Failed to process recording');
        }
      };

      recorder.start();
      setRecording(true);
      setRecordSecs(0);
      timerRef.current = setInterval(() => setRecordSecs(s => s + 1), 1000);
    } catch {
      setError('Microphone access denied');
    }
  }

  function stopRecording() {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setRecording(false);
  }

  function discardRecording() {
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    setRecordedBlob(null);
    setRecordedUrl('');
    setRecordName('');
    setRecordSecs(0);
    previewRef.current.pause();
    setPlayingRecording(false);
    // Also stop if currently recording
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
      recorderRef.current.stream.getTracks().forEach(t => t.stop());
    }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setRecording(false);
  }

  function toggleRecordingPreview() {
    const audio = previewRef.current;
    if (playingRecording && !audio.paused) {
      audio.pause();
      setPlayingRecording(false);
    } else {
      audio.src = recordedUrl;
      audio.play().catch(() => {});
      setPlayingRecording(true);
      audio.onended = () => setPlayingRecording(false);
    }
  }

  async function saveRecording() {
    if (!recordedBlob || !recordName.trim()) return;
    setError('');
    setSuccess('');
    setUploading(true);
    try {
      const safeName = recordName.trim().replace(/[^a-zA-Z0-9_\-]/g, '_');
      const file = new File([recordedBlob], `${safeName}.wav`, { type: 'audio/wav' });
      await uploadVoice('xtts', file);
      setSuccess(`Voice "${safeName}" saved`);
      discardRecording();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  function formatTime(secs: number): string {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
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

        {/* ── Microphone recorder ────────────────────────────── */}
        <div className="vm-recorder">
          {!recording && !recordedBlob && (
            <button className="vm-rec-btn" onClick={startRecording}>
              <span className="vm-mic-icon">&#127908;</span> Record from microphone
            </button>
          )}

          {recording && (
            <div className="vm-rec-active">
              <span className="vm-rec-dot" />
              <span className="vm-rec-time">{formatTime(recordSecs)}</span>
              <span className="vm-rec-target">{recordSecs < 10 ? 'Keep going — aim for 10s' : recordSecs <= 15 ? 'Good length!' : 'You can stop now'}</span>
              <button className="vm-rec-stop" onClick={stopRecording}>Stop</button>
            </div>
          )}

          {!recordedBlob && (
            <div className="vm-rec-guide">
              {!recording && (
                <>
                  <p className="vm-rec-guide-title">Recording tips</p>
                  <ul>
                    <li>Aim for <strong>10 &ndash; 15 seconds</strong> of clear speech</li>
                    <li>Use a quiet room &mdash; avoid echo and background noise</li>
                    <li>Hold the mic 15 &ndash; 20 cm from your mouth</li>
                    <li>Speak at a natural, steady pace</li>
                  </ul>
                </>
              )}
              <p className="vm-rec-guide-title">Read this aloud:</p>
              <blockquote className="vm-rec-sample">
                &ldquo;The old house at the edge of the village had been silent for years.
                On rainy evenings, shadows danced behind its dusty windows, and the
                creaking gate swung gently in the breeze. No one dared to knock,
                yet everyone wondered what stories those crumbling walls still held.&rdquo;
              </blockquote>
            </div>
          )}

          {recordedBlob && !recording && (
            <div className="vm-rec-review">
              <div className="vm-rec-review-top">
                <button
                  className="chip-play"
                  onClick={toggleRecordingPreview}
                  title="Preview recording"
                >
                  {playingRecording ? '⏹' : '▶'}
                </button>
                <input
                  className="vm-rec-name"
                  type="text"
                  placeholder="Voice name"
                  value={recordName}
                  onChange={e => setRecordName(e.target.value)}
                />
                <button
                  className="vm-rec-save"
                  onClick={saveRecording}
                  disabled={!recordName.trim() || uploading}
                >
                  Save
                </button>
                <button className="vm-rec-discard" onClick={discardRecording}>
                  ✕
                </button>
              </div>
            </div>
          )}
        </div>

        {uploading && <div className="vm-status">Uploading…</div>}

        <div className="vm-voice-list">
          {loading && <div className="vm-status">Loading voices…</div>}
          {!loading && voices.length === 0 && <div className="vm-status">No voices found</div>}
          {voices.map(v => (
            <div key={v.filename} className="vm-voice-item">
              <button
                className="chip-play"
                title={`Preview ${v.name}`}
                onClick={() => togglePreview(v.filename)}
              >
                {playing === v.filename ? '⏹' : '▶'}
              </button>
              <span className="vm-voice-name">{v.name}</span>
              {!v.builtin && (
                confirmDelete === v.filename ? (
                  <span className="vm-confirm-group">
                    <span>Delete?</span>
                    <button onClick={() => handleDelete(v.filename)}>Yes</button>
                    <button onClick={() => setConfirmDelete(null)}>No</button>
                  </span>
                ) : (
                  <button
                    className="vm-delete-btn"
                    title="Delete voice"
                    onClick={() => setConfirmDelete(v.filename)}
                  >
                    ✕
                  </button>
                )
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
