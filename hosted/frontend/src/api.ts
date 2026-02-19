// All paths are relative — the host nginx routes them to the NestJS backend.

export interface Voice {
  name: string;
  filename: string;
}

export interface Segment {
  id: number;
  speaker: string;
  original_text: string;
  emotion: string;
  intensity: number;
  pause_before_ms: number;
}

export interface StatusResponse {
  phase: 'analyzing' | 'synthesizing' | 'done';
  status: 'running' | 'done' | 'error';
  segments?: Segment[];
  total?: number;
  output_file?: string;
  error?: string;
}

export async function fetchVoices(): Promise<Voice[]> {
  const res = await fetch('/voices');
  if (!res.ok) throw new Error(`Failed to fetch voices: ${res.status}`);
  return res.json();
}

export async function analyzeText(title: string, text: string, jobId: string): Promise<void> {
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, text, job_id: jobId }),
  });
  if (!res.ok) throw new Error(`Analyze error: ${res.status}`);
}

export async function pollStatus(jobId: string): Promise<StatusResponse | null> {
  const res = await fetch(`/status/${jobId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Status error: ${res.status}`);
  return res.json();
}

export async function startSynthesis(
  segments: Segment[],
  voiceMapping: Record<string, string>,
  jobId: string,
): Promise<void> {
  const res = await fetch('/api/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segments, voice_mapping: voiceMapping, job_id: jobId }),
  });
  if (!res.ok) throw new Error(`Synthesize error: ${res.status}`);
}

export function voiceUrl(filename: string): string {
  return `/voices/${filename}`;
}

export function audioUrl(filename: string): string {
  return `/audio/${filename}`;
}
