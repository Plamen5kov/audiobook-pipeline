import { useState, useRef, useCallback } from 'react';
import { analyzeText, fetchVoices, pollStatus, startSynthesis, Voice, Segment, StatusResponse, NodeStatus, ClipInfo } from '../api';
import { PhaseState } from '../types/pipeline';
import { formatError } from '../utils/formatError';

export type AppPhase = 'idle' | 'analyzing' | 'voice-cast' | 'synthesizing' | 'done';

export interface Phases {
  analyzing:    PhaseState;
  synthesizing: PhaseState;
  assembling:   PhaseState;
}

const INITIAL_PHASES: Phases = {
  analyzing:    { state: 'pending', detail: 'Identifying speakers, emotions, and segments\u2026' },
  synthesizing: { state: 'pending', detail: 'Waiting for analysis to complete\u2026' },
  assembling:   { state: 'pending', detail: 'Waiting\u2026' },
};

export function usePipeline() {
  const [phase, setPhase]             = useState<AppPhase>('idle');
  const [phases, setPhases]           = useState<Phases>(INITIAL_PHASES);
  const [error, setError]             = useState<string>('');
  const [voices, setVoices]           = useState<Voice[]>([]);
  const [segments, setSegments]       = useState<Segment[]>([]);
  const [outputFile, setOutputFile]   = useState<string>('');
  const [nodes, setNodes]             = useState<Record<string, NodeStatus> | undefined>(undefined);
  const [activeJobId, setActiveJobId] = useState<string>('');
  const [clips, setClips]             = useState<ClipInfo[]>([]);
  const [voiceMapping, setVoiceMapping]   = useState<Record<string, string>>({});
  const [engineMapping, setEngineMapping] = useState<Record<string, string>>({});
  const [audioVersion, setAudioVersion]   = useState(0);
  const [voiceManagerOpen, setVoiceManagerOpen] = useState(false);

  const jobIdRef = useRef<string>('');
  const pollRef  = useRef<ReturnType<typeof setInterval> | null>(null);

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
    const { phase: p, status: st, segments: segs, total, output_file, error: err, nodes: n } = status;

    if (n) setNodes(prev => {
      const merged: Record<string, NodeStatus> = { ...prev };
      const now = Math.floor(Date.now() / 1000);
      for (const [key, val] of Object.entries(n)) {
        const mergedNode = { ...(prev?.[key] ?? {}), ...val };
        if (val.status === 'running') {
          delete mergedNode.finished;
        }
        if (mergedNode.status === 'done' && !mergedNode.finished && mergedNode.started) {
          mergedNode.finished = now;
        }
        merged[key] = mergedNode;
      }
      return merged;
    });

    if (err) {
      updatePhase('analyzing', 'error', err);
      setError('Pipeline error: ' + err);
      stopPolling();
      return;
    }

    if (p === 'analyzing') {
      if (st === 'running') {
        updatePhase('analyzing', 'running', 'LLM is parsing the chapter\u2026');
      } else if (st === 'done') {
        stopPolling();
        const count = segs ? segs.length : '?';
        updatePhase('analyzing', 'done', `Found ${count} segments`);
        setSegments(segs ?? []);
        setPhase('voice-cast');
      }
    } else if (p === 'synthesizing') {
      if (st === 'running') {
        const info = total ? ` \u2014 ${total} segments` : '';
        updatePhase('synthesizing', 'running', `Generating voices${info}\u2026`);
      } else if (st === 'done') {
        updatePhase('synthesizing', 'done', 'All segments synthesized');
        updatePhase('assembling', 'running', 'Joining audio clips\u2026');
      }
    } else if (p === 'done') {
      updatePhase('synthesizing', 'done', 'All segments synthesized');
      updatePhase('assembling', 'done', 'Audiobook ready');
      stopPolling();
      setOutputFile(output_file ?? '');
      if (status.clips) setClips(status.clips);
      if (status.voice_mapping) setVoiceMapping(status.voice_mapping);
      if (status.engine_mapping) setEngineMapping(status.engine_mapping);
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
      analyzing:    { state: 'running', detail: 'Sending to LLM for analysis\u2026' },
      synthesizing: { state: 'pending', detail: 'Waiting for analysis to complete\u2026' },
      assembling:   { state: 'pending', detail: 'Waiting\u2026' },
    });

    const jobId = crypto.randomUUID();
    jobIdRef.current = jobId;
    setActiveJobId(jobId);
    setNodes(undefined);

    const [analyzeResult, fetchedVoices] = await Promise.allSettled([
      analyzeText(title, text, jobId),
      fetchVoices('xtts'),
    ]);

    if (fetchedVoices.status === 'fulfilled') {
      setVoices(fetchedVoices.value);
    }

    if (analyzeResult.status === 'rejected') {
      setError('Failed to reach the pipeline. ' + formatError(analyzeResult.reason));
      setPhase('idle');
      return;
    }

    startPoll(jobId);
  }, [stopPolling, startPoll]);

  const handleGenerate = useCallback(async (
    voiceMap: Record<string, string>,
    engineMap: Record<string, string>,
    editedSegments: Segment[],
  ) => {
    stopPolling();
    setError('');
    setOutputFile('');
    setPhase('synthesizing');
    setPhases((prev: Phases) => ({
      ...prev,
      synthesizing: { state: 'running', detail: 'Sending to voice synthesis\u2026' },
      assembling:   { state: 'pending', detail: 'Waiting\u2026' },
    }));

    const jobId = jobIdRef.current;

    setVoiceMapping(voiceMap);
    setEngineMapping(engineMap);

    try {
      await startSynthesis(editedSegments, voiceMap, engineMap, jobId);
    } catch (e: unknown) {
      setError('Failed to start synthesis: ' + formatError(e));
      setPhase('voice-cast');
      return;
    }

    startPoll(jobId);
  }, [stopPolling, startPoll]);

  return {
    phase,
    phases,
    error,
    voices,
    setVoices,
    segments,
    outputFile,
    setOutputFile,
    nodes,
    activeJobId,
    clips,
    setClips,
    voiceMapping,
    engineMapping,
    audioVersion,
    setAudioVersion,
    voiceManagerOpen,
    setVoiceManagerOpen,
    handleAnalyze,
    handleGenerate,
  };
}
