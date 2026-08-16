// Base URL is configured at build time via VITE_BACKEND_URL.
// For local dev: http://localhost:3001
// For production: set VITE_BACKEND_URL to your public backend domain and rebuild.
const BASE = (import.meta.env.VITE_BACKEND_URL as string | undefined ?? '').replace(/\/$/, '');

export interface Voice {
  name: string;
  filename: string;
  builtin?: boolean;
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

export interface ClipInfo {
  id: number;
  file_path: string;
  pause_before_ms: number;
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
  clips?: ClipInfo[];
  voice_mapping?: Record<string, string>;
  engine_mapping?: Record<string, string>;
}

export interface ServiceStatus {
  name: string;
  status: 'ok' | 'loading' | 'error';
  detail: Record<string, unknown> | string;
}

// ── Shared request helper ────────────────────────────────────

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status}${detail ? `: ${detail}` : ''}`);
  }
  return res.json();
}

async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status}${detail ? `: ${detail}` : ''}`);
  }
}

// ── API functions ────────────────────────────────────────────

export async function getServicesHealth(): Promise<ServiceStatus[]> {
  return request<ServiceStatus[]>(`${BASE}/services/health`);
}

export async function fetchVoices(engine: string): Promise<Voice[]> {
  return request<Voice[]>(`${BASE}/voices/${engine}`);
}

export async function analyzeText(title: string, text: string, jobId: string): Promise<void> {
  await requestVoid(`${BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, text, job_id: jobId }),
  });
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
  await requestVoid(`${BASE}/api/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      segments,
      voice_mapping: voiceMapping,
      engine_mapping: engineMapping,
      job_id: jobId,
    }),
  });
}

export function voiceUrl(engine: string, filename: string): string {
  return `${BASE}/voices/${engine}/${filename}`;
}

export function audioUrl(filename: string): string {
  return `${BASE}/audio/${filename}`;
}

// ── Post-production ───────────────────────────────────────────

export interface ReSynthesizeRequest {
  text: string;
  segment_id: number;
  speaker: string;
  engine: string;
  reference_audio_path: string;
  qwen_speaker: string;
  emotion: string;
  intensity: number;
  speed: number;
}

export interface ReSynthesizeResponse {
  segment_id: number;
  speaker: string;
  file_path: string;
  filename: string;
}

export async function reSynthesize(params: ReSynthesizeRequest): Promise<ReSynthesizeResponse> {
  return request<ReSynthesizeResponse>(`${BASE}/api/re-synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

export interface ReStitchRequest {
  clips: ClipInfo[];
  output_filename: string;
  crossfade_ms?: number;
  normalize?: boolean;
}

export interface ReStitchResponse {
  file_path: string;
  filename: string;
  duration_ms: number;
  clips_count: number;
}

export async function reStitch(params: ReStitchRequest): Promise<ReStitchResponse> {
  return request<ReStitchResponse>(`${BASE}/api/re-stitch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
}

// ── Voice management ──────────────────────────────────────────

export async function uploadVoice(engine: string, file: File): Promise<Voice> {
  const form = new FormData();
  form.append('file', file);
  return request<Voice>(`${BASE}/voices/upload/${engine}`, {
    method: 'POST',
    body: form,
  });
}

export async function deleteVoice(engine: string, filename: string): Promise<void> {
  await requestVoid(`${BASE}/voices/${engine}/${filename}`, {
    method: 'DELETE',
  });
}

// ── Workspace: reading what a run produced ───────────────────

export type StageName =
  | 'input' | 'analysis' | 'cast' | 'synthesis' | 'assembly' | 'qa';

export const STAGE_ORDER: StageName[] =
  ['input', 'analysis', 'cast', 'synthesis', 'assembly', 'qa'];

export interface JobSummary {
  job_id: string;
  created?: string;
  stages: Record<StageName, string>;
  segments_recorded: number;
}

export interface StageDetail {
  status: string;
  at: string;
  artifact?: string;
  [key: string]: unknown;
}

export interface JobDetail extends JobSummary {
  stage_detail: Record<StageName, StageDetail | null>;
}

export interface QaVerdict {
  id: number;
  status: string;
  similarity?: number;
  heard?: string;
}

export interface WorkspaceSegment {
  id: number;
  kind: string;
  speaker: string;
  original_text: string;
  spoken_text: string;
  emotion: string;
  intensity: number;
  pause_before_ms: number;
  clip: { present: boolean; fingerprint: string | null; url: string };
  qa: QaVerdict | null;
}

export interface SegmentsResponse {
  job_id: string;
  title?: string;
  total: number;
  returned: number;
  segments: WorkspaceSegment[];
}

export interface StageArtifacts {
  stage: string;
  artifacts?: Record<string, unknown>;
  files?: string[];
}

export async function listJobs(): Promise<JobSummary[]> {
  return request<JobSummary[]>(`${BASE}/api/jobs`);
}

export async function getJob(jobId: string): Promise<JobDetail> {
  return request<JobDetail>(`${BASE}/api/jobs/${encodeURIComponent(jobId)}`);
}

export async function getStageArtifacts(
  jobId: string,
  stage: StageName,
): Promise<StageArtifacts> {
  return request<StageArtifacts>(
    `${BASE}/api/jobs/${encodeURIComponent(jobId)}/stages/${stage}`,
  );
}

export async function listSegments(
  jobId: string,
  opts: { failed?: boolean; speaker?: string } = {},
): Promise<SegmentsResponse> {
  const query = new URLSearchParams();
  if (opts.failed) query.set('failed', 'true');
  if (opts.speaker) query.set('speaker', opts.speaker);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return request<SegmentsResponse>(
    `${BASE}/api/jobs/${encodeURIComponent(jobId)}/segments${suffix}`,
  );
}

/** The URL to play one take. Given to an <audio> element rather than fetched. */
export function segmentAudioUrl(jobId: string, segmentId: number): string {
  return `${BASE}/api/jobs/${encodeURIComponent(jobId)}/segments/${segmentId}/audio`;
}

export async function redoSegments(
  jobId: string,
  segments: number[],
): Promise<{ marked: number[]; had_no_clip: number[] }> {
  return request(`${BASE}/api/jobs/${encodeURIComponent(jobId)}/redo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segments }),
  });
}
