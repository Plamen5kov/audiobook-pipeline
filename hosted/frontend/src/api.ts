// Base URL is configured at build time via VITE_BACKEND_URL.
// For local dev: http://localhost:3001
// For production: set VITE_BACKEND_URL to your public backend domain and rebuild.
const BASE = (import.meta.env.VITE_BACKEND_URL as string | undefined ?? '').replace(/\/$/, '');

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

export interface NodeStatus {
  status: 'pending' | 'running' | 'done' | 'error';
  started?: number;
  finished?: number;
  completed?: number;
  total?: number;
}

export interface StatusResponse {
  phase: 'analyzing' | 'synthesizing' | 'done';
  status: 'running' | 'done' | 'error';
  segments?: Segment[];
  total?: number;
  completed?: number;
  output_file?: string;
  error?: string;
  nodes?: Record<string, NodeStatus>;
}

export interface ServiceStatus {
  name: string;
  status: 'ok' | 'loading' | 'error';
  detail: Record<string, unknown> | string;
}

export async function getServicesHealth(): Promise<ServiceStatus[]> {
  const res = await fetch(`${BASE}/services/health`);
  if (!res.ok) throw new Error('health check failed');
  return res.json();
}

export async function fetchVoices(): Promise<Voice[]> {
  const res = await fetch(`${BASE}/voices`);
  if (!res.ok) throw new Error(`Failed to fetch voices: ${res.status}`);
  return res.json();
}

export async function analyzeText(title: string, text: string, jobId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, text, job_id: jobId }),
  });
  if (!res.ok) throw new Error(`Analyze error: ${res.status}`);
}

export async function pollStatus(jobId: string): Promise<StatusResponse | null> {
  const res = await fetch(`${BASE}/status/${jobId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Status error: ${res.status}`);
  return res.json();
}

export async function startSynthesis(
  segments: Segment[],
  voiceMapping: Record<string, string>,
  engineMapping: Record<string, string>,
  jobId: string,
): Promise<void> {
  const res = await fetch(`${BASE}/api/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segments, voice_mapping: voiceMapping, engine_mapping: engineMapping, job_id: jobId }),
  });
  if (!res.ok) throw new Error(`Synthesize error: ${res.status}`);
}

export function voiceUrl(filename: string): string {
  return `${BASE}/voices/${filename}`;
}

export function audioUrl(filename: string): string {
  return `${BASE}/audio/${filename}`;
}
