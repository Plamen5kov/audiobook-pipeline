import { useState, useRef, useCallback } from 'react';
import { analyzeText, fetchVoices, pollStatus, startSynthesis, Voice, Segment, StatusResponse } from './api';
import { AnalyzeForm } from './components/AnalyzeForm';
import { StatusProgress, PhaseState } from './components/StatusProgress';
import { VoiceCast } from './components/VoiceCast';
import { AudioPlayer } from './components/AudioPlayer';

type AppPhase = 'idle' | 'analyzing' | 'voice-cast' | 'synthesizing' | 'done';

function uuid(): string {
  return ([1e7] as unknown as string + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, (c: string) => {
    const n = parseInt(c);
    return (n ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (n / 4)))).toString(16);
  });
}

interface Phases {
  analyzing:    PhaseState;
  synthesizing: PhaseState;
  assembling:   PhaseState;
}

const INITIAL_PHASES: Phases = {
  analyzing:    { state: 'pending', detail: 'Identifying speakers, emotions, and segments…' },
  synthesizing: { state: 'pending', detail: 'Waiting for analysis to complete…' },
  assembling:   { state: 'pending', detail: 'Waiting…' },
};

export default function App() {
  const [phase, setPhase]             = useState<AppPhase>('idle');
  const [phases, setPhases]           = useState<Phases>(INITIAL_PHASES);
  const [error, setError]             = useState<string>('');
  const [voices, setVoices]           = useState<Voice[]>([]);
  const [segments, setSegments]       = useState<Segment[]>([]);
  const [outputFile, setOutputFile]   = useState<string>('');

  const jobIdRef   = useRef<string>('');
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null);

  const updatePhase = useCallback((
    key: keyof Phases,
    state: PhaseState['state'],
    detail?: string,
  ) => {
    setPhases(prev => ({
      ...prev,
      [key]: { state, detail: detail ?? prev[key].detail },
    }));
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const handlePollResult = useCallback((status: StatusResponse) => {
    const { phase: p, status: st, segments: segs, total, output_file, error: err } = status;

    if (err) {
      updatePhase('analyzing', 'error', err);
      setError('Pipeline error: ' + err);
      stopPolling();
      return;
    }

    if (p === 'analyzing') {
      if (st === 'running') {
        updatePhase('analyzing', 'running', 'LLM is parsing the chapter…');
      } else if (st === 'done') {
        stopPolling();
        const count = segs ? segs.length : '?';
        updatePhase('analyzing', 'done', `Found ${count} segments`);
        setSegments(segs ?? []);
        setPhase('voice-cast');
      }
    } else if (p === 'synthesizing') {
      if (st === 'running') {
        const info = total ? ` — ${total} segments` : '';
        updatePhase('synthesizing', 'running', `Generating voices${info}…`);
      } else if (st === 'done') {
        updatePhase('synthesizing', 'done', 'All segments synthesized');
        updatePhase('assembling', 'running', 'Joining audio clips…');
      }
    } else if (p === 'done') {
      updatePhase('synthesizing', 'done', 'All segments synthesized');
      updatePhase('assembling', 'done', 'Audiobook ready');
      stopPolling();
      setOutputFile(output_file ?? '');
      setPhase('done');
    }
  }, [updatePhase, stopPolling]);

  const startPoll = useCallback((jobId: string) => {
    pollRef.current = setInterval(async () => {
      if (jobId !== jobIdRef.current) return;
      try {
        const status = await pollStatus(jobId);
        if (status) handlePollResult(status);
      } catch { /* ignore transient errors */ }
    }, 4000);
  }, [handlePollResult]);

  const handleAnalyze = useCallback(async (title: string, text: string) => {
    stopPolling();
    setError('');
    setOutputFile('');
    setPhase('analyzing');
    setPhases({
      analyzing:    { state: 'running', detail: 'Sending to LLM for analysis…' },
      synthesizing: { state: 'pending', detail: 'Waiting for analysis to complete…' },
      assembling:   { state: 'pending', detail: 'Waiting…' },
    });

    const jobId = uuid();
    jobIdRef.current = jobId;

    const [analyzeResult, fetchedVoices] = await Promise.allSettled([
      analyzeText(title, text, jobId),
      fetchVoices(),
    ]);

    if (fetchedVoices.status === 'fulfilled') {
      setVoices(fetchedVoices.value);
    }

    if (analyzeResult.status === 'rejected') {
      const e = analyzeResult.reason;
      setError('Failed to reach the pipeline. ' + (e instanceof Error ? e.message : String(e)));
      setPhase('idle');
      return;
    }

    startPoll(jobId);
  }, [stopPolling, startPoll]);

  const handleGenerate = useCallback(async (voiceMapping: Record<string, string>) => {
    stopPolling();
    setError('');
    setOutputFile('');
    setPhase('synthesizing');
    setPhases((prev: Phases) => ({
      ...prev,
      synthesizing: { state: 'running', detail: 'Sending to voice synthesis…' },
      assembling:   { state: 'pending', detail: 'Waiting…' },
    }));

    // Use a fresh job ID each synthesis so polling never reads a stale status file
    const synthJobId = uuid();
    jobIdRef.current = synthJobId;

    try {
      await startSynthesis(segments, voiceMapping, synthJobId);
    } catch (e: unknown) {
      setError('Failed to start synthesis: ' + (e instanceof Error ? e.message : String(e)));
      setPhase('voice-cast');
      return;
    }

    startPoll(synthJobId);
  }, [segments, stopPolling, startPoll]);

  const showProgress  = phase !== 'idle';
  const showVoiceCast = segments.length > 0 && phase !== 'idle' && phase !== 'analyzing';
  const showResult    = phase === 'done';

  return (
    <>
      <header className="app-header">
        <h1>Audiobook <span>Generator</span></h1>
        <p>Paste your chapter text and generate a fully narrated audiobook with distinct character voices.</p>
      </header>

      <AnalyzeForm
        onAnalyze={handleAnalyze}
        disabled={phase !== 'idle' && phase !== 'done'}
        error={error}
      />

      {showProgress && (
        <StatusProgress
          analyzing={phases.analyzing}
          synthesizing={phases.synthesizing}
          assembling={phases.assembling}
        />
      )}

      {showVoiceCast && (
        <VoiceCast
          segments={segments}
          voices={voices}
          onGenerate={handleGenerate}
          disabled={phase === 'synthesizing'}
        />
      )}

      {showResult && outputFile && (
        <AudioPlayer filename={outputFile} />
      )}
    </>
  );
}
