import type { Track } from './api';

export type BrowseFilters = { genre: string; artist: string; source: string; status: string };
export const emptyFilters: BrowseFilters = { genre: 'all', artist: 'all', source: 'all', status: 'all' };
export type TrackSort = 'default' | 'title' | 'artist' | 'duration';

export function browseTracks(items: Track[], query: string, filters: BrowseFilters, sort: TrackSort): Track[] {
  const search = query.trim().toLocaleLowerCase('en');
  const result = items.filter(track =>
    (!search || `${track.title} ${track.artist} ${track.genre}`.toLocaleLowerCase('en').includes(search)) &&
    (filters.genre === 'all' || track.genre === filters.genre) &&
    (filters.artist === 'all' || track.artist === filters.artist) &&
    (filters.source === 'all' || track.source === filters.source) &&
    (filters.status === 'all' || (filters.status === 'preparing' ? ['queued', 'processing'].includes(track.playback_status) : track.playback_status === filters.status))
  );
  if (sort === 'title' || sort === 'artist') result.sort((a, b) => a[sort].localeCompare(b[sort], 'en', { sensitivity: 'base' }) || a.id - b.id);
  if (sort === 'duration') result.sort((a, b) => a.duration - b.duration || a.id - b.id);
  return result;
}
