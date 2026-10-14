export type Track = {
  id: number; title: string; artist: string; genre: string; duration: number;
  visibility: 'shared' | 'private'; source: string; color: string;
  playback_status: string; embedding_status: string; embedding_model: string | null;
  feedback: 'like' | 'dislike' | 'clear' | null; error?: string | null;
  reason_code?: string; because_track_id?: number; position?: number;
  engine?: string; request_id?: string; created_at?: number;
};
export type User = { id: string; name: string; preferences: { genre: string; mode: 'focus' | 'balanced'; seed_track_ids: number[]; personalization_enabled: boolean; smart_autoplay_enabled: boolean } };
export type SessionState = { user: User; csrf_token: string };
export type Playlist = { id: string; title: string; track_ids: number[] };
export type Recommendations = { request_id: string; items: Track[]; engine: string; returned_count: number; requested_limit: number; actual_exploration_rate: number; shortage_reason: string | null; strategy?: string; candidate_count?: number; scored_count?: number; profile_weights?: { long_term: number; session: number } };
export type TasteMix = { id: string; title: string; seed_track_ids: number[]; signal_count: number; items: Track[] };
export type Discovery = { request_id: string; interest_groups: TasteMix[]; next_items: Track[]; next_strategy: string; candidate_count: number; additional_interest_count: number };
let csrf = '';
let boot: Promise<SessionState> | null = null;
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export function setCsrf(token: string) { csrf = token; }
export function getCsrf() { return csrf; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-CSRF-Token': csrf, ...options.headers as Record<string, string> };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
  const data = await response.json();
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : 'Invalid request data', response.status);
  return data;
}
export function bootstrap(reset = false): Promise<SessionState> {
  if (reset) boot = null;
  if (!boot) boot = api<SessionState>('/session', { method: 'POST' }).then(auth => { setCsrf(auth.csrf_token); return auth; });
  return boot;
}
export const labels: Record<string, string> = { 'Hip-Hop': 'Hip-Hop', Electronic: 'Electronic', Pop: 'Pop', Rock: 'Rock', Folk: 'Folk', Instrumental: 'Instrumental', Experimental: 'Experimental', International: 'International' };
export const time = (seconds: number) => `${Math.floor((seconds || 0) / 60)}:${Math.floor((seconds || 0) % 60).toString().padStart(2, '0')}`;
