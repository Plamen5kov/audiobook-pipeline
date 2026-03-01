import { useState, useRef, useCallback } from 'react';

export function useAudioPreview() {
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(new Audio());

  const togglePreview = useCallback((key: string, src: string) => {
    const audio = audioRef.current;
    if (playing === key && !audio.paused) {
      audio.pause();
      setPlaying(null);
    } else {
      audio.pause();
      audio.src = src;
      audio.play().catch(() => {});
      setPlaying(key);
      audio.onended = () => setPlaying(null);
    }
  }, [playing]);

  const stopPreview = useCallback(() => {
    audioRef.current.pause();
    setPlaying(null);
  }, []);

  return { playing, togglePreview, stopPreview };
}
