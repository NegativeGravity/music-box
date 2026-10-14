import { api, Track } from './api';

export type GenerationInput = {
  prompt: string; mode: 'vocal' | 'instrumental'; lyrics: string; language: string;
  reference_track_ids: number[]; primary_reference_track_id: number | null;
};
export type GenerationJob = {
  id: string; request: GenerationInput & { seed: number; duration_seconds: number; engine?: string; takes?: number; references: { id: number; title: string; genre: string }[] };
  status: 'queued' | 'submitting' | 'running' | 'ingesting' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
  phase: string; created_at: number; updated_at?: number; elapsed_seconds: number | null;
  audio_url: string | null; saved_track_id: number | null; error: string | null;
  selected_candidate_index?: number | null;
  candidates?: GenerationCandidate[];
  progress?: { completed: number; finished: number; total: number; active_candidate_index: number | null };
};
export type GenerationCandidate = { index: number; seed: number; status: string; audio_url: string | null;
  duration_seconds: number | null; duration_capped?: boolean; saved_track_id: number | null; error?: string | null };
export type GenerationStatus = {
  available: boolean; active: boolean; queued_count: number;
  capabilities: { engine?: string; model?: string; duration_seconds: number; takes: number; languages: string[];
    tested_languages: string[]; automatic_lyrics: boolean; audio_reference?: boolean; duration_is_maximum?: boolean };
};
export const generation = {
  status: (signal?: AbortSignal) => api<GenerationStatus>('/generation/status', { signal }),
  jobs: (signal?: AbortSignal) => api<{ jobs: GenerationJob[] }>('/generation/jobs', { signal }),
  create: (body: GenerationInput, key: string) => api<{ job: GenerationJob }>('/generation/jobs', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) }),
  save: (id: string) => api<{ track: Track }>(`/generation/jobs/${id}/save`, { method: 'POST' }),
  select: (id: string, index: number) => api<{ job: GenerationJob }>(`/generation/jobs/${id}/candidates/${index}/select`, { method: 'POST' }),
  cancel: (id: string) => api<{ job: GenerationJob }>(`/generation/jobs/${id}/cancel`, { method: 'POST' }),
};
export const terminal = (job: GenerationJob) => ['completed', 'failed', 'interrupted', 'cancelled'].includes(job.status);

export function mergeGenerationJobs(current: GenerationJob[], incoming: GenerationJob[]) {
  const jobs = new Map(current.map(job => [job.id, job]));
  for (const job of incoming) {
    const existing = jobs.get(job.id);
    if (!existing || (job.updated_at || 0) >= (existing.updated_at || 0)) jobs.set(job.id, job);
  }
  return [...jobs.values()].sort((a, b) => b.created_at - a.created_at).slice(0, 30);
}
