import { useState, useRef, useCallback } from 'react';
import { encodeWav } from '../utils/encodeWav';

interface UseVoiceRecorderReturn {
  recording: boolean;
  recordSecs: number;
  recordedBlob: Blob | null;
  recordedUrl: string;
  recordName: string;
  playingRecording: boolean;
  setRecordName: (name: string) => void;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  discardRecording: () => void;
  toggleRecordingPreview: () => void;
  setError: (msg: string) => void;
  error: string;
}

export function useVoiceRecorder(): UseVoiceRecorderReturn {
  const [recording, setRecording] = useState(false);
  const [recordSecs, setRecordSecs] = useState(0);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedUrl, setRecordedUrl] = useState('');
  const [recordName, setRecordName] = useState('');
  const [playingRecording, setPlayingRecording] = useState(false);
  const [error, setError] = useState('');

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const previewRef = useRef<HTMLAudioElement>(new Audio());

  const discardRecording = useCallback(() => {
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    setRecordedBlob(null);
    setRecordedUrl('');
    setRecordName('');
    setRecordSecs(0);
    previewRef.current.pause();
    setPlayingRecording(false);
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
      recorderRef.current.stream.getTracks().forEach(t => t.stop());
    }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setRecording(false);
  }, [recordedUrl]);

  const startRecording = useCallback(async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const raw = new Blob(chunksRef.current, { type: recorder.mimeType });

        try {
          const arrayBuf = await raw.arrayBuffer();
          const ctx = new AudioContext();
          const decoded = await ctx.decodeAudioData(arrayBuf);
          const pcm = decoded.getChannelData(0);
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
  }, []);

  const stopRecording = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setRecording(false);
  }, []);

  const toggleRecordingPreview = useCallback(() => {
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
  }, [playingRecording, recordedUrl]);

  return {
    recording,
    recordSecs,
    recordedBlob,
    recordedUrl,
    recordName,
    playingRecording,
    setRecordName,
    startRecording,
    stopRecording,
    discardRecording,
    toggleRecordingPreview,
    setError,
    error,
  };
}
