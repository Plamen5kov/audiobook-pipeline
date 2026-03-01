import { useVoiceRecorder } from '../hooks/useVoiceRecorder';
import { formatTime } from '../utils/formatDuration';
import { formatError } from '../utils/formatError';
import { uploadVoice, fetchVoices, Voice } from '../api';
import { useCallback, useState } from 'react';

interface Props {
  onVoicesChanged: (voices: Voice[]) => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

export function VoiceRecorder({ onVoicesChanged, onSuccess, onError }: Props) {
  const recorder = useVoiceRecorder();
  const [uploading, setUploading] = useState(false);

  const saveRecording = useCallback(async () => {
    if (!recorder.recordedBlob || !recorder.recordName.trim()) return;
    onError('');
    onSuccess('');
    setUploading(true);
    try {
      const safeName = recorder.recordName.trim().replace(/[^a-zA-Z0-9_\-]/g, '_');
      const file = new File([recorder.recordedBlob], `${safeName}.wav`, { type: 'audio/wav' });
      await uploadVoice('xtts', file);
      onSuccess(`Voice "${safeName}" saved`);
      recorder.discardRecording();
      const list = await fetchVoices('xtts');
      onVoicesChanged(list);
    } catch (e) {
      onError(formatError(e));
    } finally {
      setUploading(false);
    }
  }, [recorder, onVoicesChanged, onSuccess, onError]);

  return (
    <div className="vm-recorder">
      {!recorder.recording && !recorder.recordedBlob && (
        <button className="vm-rec-btn" onClick={recorder.startRecording}>
          <span className="vm-mic-icon">&#127908;</span> Record from microphone
        </button>
      )}

      {recorder.recording && (
        <div className="vm-rec-active">
          <span className="vm-rec-dot" />
          <span className="vm-rec-time">{formatTime(recorder.recordSecs)}</span>
          <span className="vm-rec-target">
            {recorder.recordSecs < 10 ? 'Keep going \u2014 aim for 10s' : recorder.recordSecs <= 15 ? 'Good length!' : 'You can stop now'}
          </span>
          <button className="vm-rec-stop" onClick={recorder.stopRecording}>Stop</button>
        </div>
      )}

      {!recorder.recordedBlob && (
        <div className="vm-rec-guide">
          {!recorder.recording && (
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

      {recorder.recordedBlob && !recorder.recording && (
        <div className="vm-rec-review">
          <div className="vm-rec-review-top">
            <button
              className="chip-play"
              onClick={recorder.toggleRecordingPreview}
              title="Preview recording"
            >
              {recorder.playingRecording ? '\u23F9' : '\u25B6'}
            </button>
            <input
              className="vm-rec-name"
              type="text"
              placeholder="Voice name"
              value={recorder.recordName}
              onChange={e => recorder.setRecordName(e.target.value)}
            />
            <button
              className="vm-rec-save"
              onClick={saveRecording}
              disabled={!recorder.recordName.trim() || uploading}
            >
              Save
            </button>
            <button className="vm-rec-discard" onClick={recorder.discardRecording}>
              &#10005;
            </button>
          </div>
        </div>
      )}

      {recorder.error && <div className="vm-error">{recorder.error}</div>}
    </div>
  );
}
