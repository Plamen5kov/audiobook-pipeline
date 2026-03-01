import { useEffect, useRef, useCallback } from 'react';

export function usePolling(fn: () => Promise<void> | void, intervalMs: number, enabled: boolean = true) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const start = useCallback(() => {
    stop();
    fnRef.current();
    pollRef.current = setInterval(() => fnRef.current(), intervalMs);
  }, [intervalMs, stop]);

  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }
    return stop;
  }, [enabled, start, stop]);

  return { start, stop };
}
