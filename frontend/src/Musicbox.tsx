'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, AudioLines, Check, Disc3, Heart, Headphones, Library, ListMusic, LoaderCircle, MoreHorizontal, Music2, Pause, Play, Plus, RefreshCw, Repeat, Search, Shuffle, SkipBack, SkipForward, SlidersHorizontal, Sparkles, ThumbsDown, Trash2, Upload, Volume2, X } from 'lucide-react';
import { api, ApiError, SessionState, bootstrap, getCsrf, labels, Playlist, Recommendations, Discovery, TasteMix, time, Track, User } from './lib/api';
import DiscoveryPanel from './DiscoveryPanel';
import AIStudioPanel from './AIStudioPanel';
import { BrowseFilters, browseTracks, emptyFilters, TrackSort } from './lib/browse';

type View = 'for-you' | 'library' | 'liked' | 'similar' | 'playlist' | 'studio';
type Modal = 'upload' | 'playlist' | 'queue' | 'track' | null;

function Cover({ track, className = '' }: { track: Pick<Track, 'id' | 'color'>; className?: string }) {
  const variant = track.id % 4;
  return <div className={`cover cover-${variant} ${className}`} style={{ '--cover-color': ['#689cff', '#a78bfa', '#f06a81', '#526fe8', '#be83ee', '#df536e', '#819aff', '#9174db'][track.id % 8] } as React.CSSProperties} aria-hidden="true">
    <svg viewBox="0 0 200 200" fill="none"><rect width="200" height="200" fill="currentColor" />
      {variant === 0 ? <><circle cx="100" cy="100" r="66" fill="#18203e" /><circle cx="100" cy="100" r="45" stroke="currentColor" strokeWidth="1" /><circle cx="100" cy="100" r="24" stroke="currentColor" strokeWidth="1" /><circle cx="100" cy="100" r="6" fill="currentColor" /><path d="M20 180L180 20" stroke="#fff" strokeOpacity=".25" strokeWidth="1" /></> :
      variant === 1 ? <><path d="M-15 150L82 20L160 200" fill="#222947" /><path d="M55 200L150 36L225 170" fill="#fff" fillOpacity=".3" /><circle cx="40" cy="45" r="15" fill="#fff" fillOpacity=".7" /></> :
      variant === 2 ? <><circle cx="100" cy="160" r="80" fill="#283340" /><circle cx="100" cy="65" r="48" fill="#fff" fillOpacity=".3" /><path d="M0 100H200M0 115H200M0 130H200M0 145H200" stroke="currentColor" strokeWidth="2" /><circle cx="100" cy="65" r="26" stroke="#fff" strokeOpacity=".5" /></> :
      <><path d="M25 25H175V175H25z" stroke="#1a2040" strokeWidth="2" /><path d="M45 45H155V155H45zM65 65H135V135H65z" stroke="#1a2040" strokeWidth="2" /><path d="M100 5V195M5 100H195" stroke="#1a2040" strokeWidth="2" /><circle cx="100" cy="100" r="32" fill="#1a2040" /></>}
    </svg><span className="cover-mark">MB / {String(track.id).padStart(2, '0')}</span>
  </div>;
}

function TrackCard({ track, active, onPlay, onLike, onMenu, onExposure }: { track: Track; active: boolean; onPlay: () => void; onLike: () => void; onMenu: () => void; onExposure: () => void }) {
  const element = useRef<HTMLElement>(null);
  const exposureCallback = useRef(onExposure);
  exposureCallback.current = onExposure;
  const recorded = useRef<string | null>(null);
  useEffect(() => {
    if (!track.request_id || !element.current) return;
    const key = `${track.request_id}:${track.id}`;
    if (recorded.current === key) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && entries[0].intersectionRatio >= .5) { if (timer) clearTimeout(timer); timer = setTimeout(() => { recorded.current = key; exposureCallback.current(); observer.disconnect(); }, 1000); }
      else if (timer) clearTimeout(timer);
    }, { threshold: [.5] });
    observer.observe(element.current);
    return () => { observer.disconnect(); if (timer) clearTimeout(timer); };
  }, [track.id, track.request_id]);
  const ready = track.playback_status === 'ready';
  return <article ref={element} className={`track-card ${active ? 'active-track' : ''}`} data-testid="track-card">
    <div className="cover-wrap"><Cover track={track} /><span className="genre-badge">{labels[track.genre]}</span>
      <button className="card-play" aria-label={`Play ${track.title}`} data-testid="track-play" disabled={!ready} onClick={onPlay}>{ready ? <Play size={21} fill="currentColor" /> : <LoaderCircle size={21} className="spin" />}</button>
      {track.source === 'demo' && <span className="demo-badge">Demo audio</span>}
    </div>
    <div className="track-info"><h3 dir="auto">{track.title}</h3><button className="icon-button" aria-label="Track options" onClick={onMenu}><MoreHorizontal size={19} /></button></div>
    <div className="track-artist"><span dir="auto">{track.artist}</span><span dir="ltr">{time(track.duration)}</span></div>
    <div className="track-bottom"><span>{track.playback_status === 'failed' ? 'Processing failed' : !ready ? 'Preparing audio…' : track.reason_code === 'hybrid_taste' ? 'Your taste + recent listening' : track.reason_code === 'long_term_taste' ? 'Based on your listening taste' : track.reason_code === 'session_taste' ? 'Fits this listening session' : track.reason_code === 'cold_start_diversity' ? 'Explore your music' : track.reason_code === 'similar_to_seed' ? 'Similar sound' : track.reason_code === 'neighbor_genre' ? 'Explore a nearby genre' : track.visibility === 'private' ? 'Your private library' : 'From the shared catalog'}</span>
      <button className={`icon-button like-button ${track.feedback === 'like' ? 'liked' : ''}`} data-testid="track-like" aria-label={`Like ${track.title}`} aria-pressed={track.feedback === 'like'} onClick={onLike}><Heart size={17} fill={track.feedback === 'like' ? 'currentColor' : 'none'} /></button>
    </div>
  </article>;
}

export default function Musicbox() {
  const [user, setUser] = useState<User | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [rec, setRec] = useState<Recommendations | null>(null);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [view, setView] = useState<View>('for-you');
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [browseFilters, setBrowseFilters] = useState<BrowseFilters>(emptyFilters);
  const [sort, setSort] = useState<TrackSort>('default');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [modal, setModal] = useState<Modal>(null);
  const [selected, setSelected] = useState<Track | null>(null);
  const [playlistId, setPlaylistId] = useState<string | null>(null);
  const [similarTitle, setSimilarTitle] = useState('');
  const [sidebar, setSidebar] = useState(false);
  const [current, setCurrent] = useState<Track | null>(null);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(.7);
  const [shuffle, setShuffle] = useState(false);
  const [repeat, setRepeat] = useState(false);
  const [queue, setQueue] = useState<Track[]>([]);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [discoveryError, setDiscoveryError] = useState('');
  const [choosingNext, setChoosingNext] = useState(false);
  const discoveryGeneration = useRef(0);
  const autoplayGeneration = useRef(0);
  const queueMode = useRef<'smart' | 'playlist'>('smart');
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const audio = useRef<HTMLAudioElement>(null);
  const currentRef = useRef<Track | null>(null);
  const userRef = useRef<User | null>(null);
  const viewRef = useRef<View>('for-you');
  const similarSeed = useRef<number | null>(null);
  const playStarted = useRef<number | null>(null);
  const sessionId = useRef('');
  const requestGeneration = useRef(0);
  const seed = useRef(42);
  const formRef = useRef<HTMLFormElement>(null);
  const flushing = useRef<Promise<void> | null>(null);
  const personalized = Boolean(user?.preferences.personalization_enabled);
  const changeView = useCallback((target: View) => { viewRef.current = target; setView(target); }, []);

  const notify = useCallback((message: string) => { setToast(message); }, []);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 4000); return () => clearTimeout(t); }, [toast]);
  useEffect(() => { userRef.current = user; }, [user]);

  const flushEvents = useCallback((): Promise<void> => {
    if (flushing.current) return flushing.current;
    const activeUser = userRef.current;
    if (!activeUser) return Promise.resolve();
    const key = `musicbox.events.${activeUser.id}`;
    const task = (async () => {
      while (navigator.onLine && userRef.current?.id === activeUser.id) {
        const events = JSON.parse(localStorage.getItem(key) || '[]') as { event_id: string }[];
        if (!events.length) break;
        let consumed: Set<string>;
        try {
          const result = await api<{ accepted?: string[]; rejected?: { event_id: string; status: number }[] }>('/events/batch', { method: 'POST', body: JSON.stringify({ events }) });
          consumed = new Set([...(result.accepted || events.map(e => e.event_id)), ...(result.rejected || []).filter(e => e.status >= 400 && e.status < 500).map(e => e.event_id)]);
        } catch (error) {
          if (error instanceof ApiError && [404, 409, 422].includes(error.status)) consumed = new Set(events.map(e => e.event_id));
          else break;
        }
        const remaining = (JSON.parse(localStorage.getItem(key) || '[]') as { event_id: string }[]).filter(e => !consumed.has(e.event_id));
        localStorage.setItem(key, JSON.stringify(remaining));
      }
    })();
    flushing.current = task.finally(() => { flushing.current = null; });
    return flushing.current;
  }, []);

  const record = useCallback((track: Track, event_type: string, extras: Record<string, unknown> = {}) => {
    const activeUser = userRef.current;
    if (!activeUser) return;
    const event = { event_id: crypto.randomUUID(), track_id: track.id, request_id: track.request_id || null, position: track.position || null, event_type, session_id: sessionId.current, occurred_at: new Date().toISOString(), playback_origin: track.request_id ? 'recommendation' : 'library', ...extras };
    const key = `musicbox.events.${activeUser.id}`;
    const saved = JSON.parse(localStorage.getItem(key) || '[]') as unknown[];
    const events = [...saved, event].slice(-100);
    localStorage.setItem(key, JSON.stringify(events));
    void flushEvents();
  }, [flushEvents]);

  useEffect(() => {
    if (!user) return;
    const retry = () => { void flushEvents(); };
    window.addEventListener('online', retry);
    const timer = setInterval(retry, 10000);
    retry();
    return () => { clearInterval(timer); window.removeEventListener('online', retry); };
  }, [user?.id, flushEvents]);

  const loadLibrary = useCallback(async () => {
    const [library, lists] = await Promise.all([api<{ tracks: Track[] }>('/library/tracks'), api<{ playlists: Playlist[] }>('/playlists')]);
    setTracks(library.tracks); setPlaylists(lists.playlists);
    return library.tracks;
  }, []);

  const loadDiscovery = useCallback(async (currentId = currentRef.current?.id): Promise<Discovery | null> => {
    const activeUser=userRef.current;
    if (!activeUser?.preferences.personalization_enabled) return null;
    const generation=++discoveryGeneration.current;
    try {
      await flushEvents();
      if (!userRef.current?.preferences.personalization_enabled || userRef.current.id!==activeUser.id) return null;
      const result=await api<Discovery>(`/recommendations/discovery?session_id=${encodeURIComponent(sessionId.current)}&seed=${seed.current}${currentId===undefined?'':`&current_id=${currentId}`}`);
      if (!userRef.current?.preferences.personalization_enabled || userRef.current.id!==activeUser.id || currentRef.current?.id!==currentId) return null;
      const attach=(track: Track) => ({...track,request_id:result.request_id});
      const updated={...result,next_items:result.next_items.map(attach),interest_groups:result.interest_groups.map(mix=>({...mix,items:mix.items.map(attach)}))};
      if (generation===discoveryGeneration.current) {setDiscovery(updated);setDiscoveryError('');}
      return updated;
    } catch {if (generation===discoveryGeneration.current) setDiscoveryError('Could not refresh taste mixes. Try again.');return null;}
  }, [flushEvents]);

  const loadRec = useCallback(async (similar?: number) => {
    if (similar === undefined && !userRef.current?.preferences.personalization_enabled) return;
    const generation = ++requestGeneration.current;
    const result = await api<Recommendations>(similar ? `/recommendations/similar/${similar}?limit=12` : `/recommendations/for-you?limit=20&seed=${seed.current}&session_id=${encodeURIComponent(sessionId.current)}`);
    if (generation === requestGeneration.current) setRec({ ...result, items: result.items.map(t => ({ ...t, request_id: result.request_id })) });
    if (similar===undefined) await loadDiscovery();
  }, [loadDiscovery]);

  useEffect(() => {if (personalized && current) void loadDiscovery();}, [current?.id,personalized,loadDiscovery]);

  useEffect(() => {
    let live = true;
    sessionId.current = crypto.randomUUID();
    seed.current = crypto.getRandomValues(new Uint32Array(1))[0] % (2 ** 31);
    bootstrap().then(async auth => {
      if (!live) return;
      userRef.current = auth.user; setUser(auth.user);
      await Promise.all([loadLibrary(), ...(auth.user.preferences.personalization_enabled ? [loadRec()] : [])]);
    }).catch(e => { if (live) setError(e.message || 'Could not connect to Musicbox'); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [loadLibrary, loadRec]);

  useEffect(() => {
    if (!user) return;
    const timer = setInterval(() => {
      if (tracks.some(t => t.playback_status === 'queued' || t.playback_status === 'processing' || t.embedding_status === 'upgrading')) loadLibrary().catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [user, tracks, loadLibrary]);

  useEffect(() => { if (audio.current) audio.current.volume = volume; }, [volume]);
  useEffect(() => { if (audio.current) audio.current.loop = repeat; }, [repeat]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); document.getElementById('search')?.focus(); } };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, []);

  const endSegment = useCallback((reason: string) => {
    if (playStarted.current !== null && currentRef.current) {
      record(currentRef.current, 'play_end', { played_ms: Math.round(performance.now() - playStarted.current), termination_reason: reason });
      playStarted.current = null;
    }
  }, [record]);

  const baseItems = (() => {
    let items: Track[];
    if (view === 'similar' || (view === 'for-you' && personalized)) items = rec?.items || [];
    else if (view === 'for-you') items = tracks;
    else if (view === 'liked') items = tracks.filter(t => t.feedback === 'like');
    else if (view === 'playlist') items = (playlists.find(p => p.id === playlistId)?.track_ids || []).map(id => tracks.find(t => t.id === id)).filter((t): t is Track => !!t);
    else items = tracks.filter(t => filter === 'all' || t.visibility === filter);
    return items;
  })();
  const visible = browseTracks(baseItems, query, browseFilters, sort);
  const artists = [...new Set(baseItems.map(t => t.artist))].sort((a, b) => a.localeCompare(b, 'en'));
  const hasFilters = Boolean(query || Object.values(browseFilters).some(value => value !== 'all') || sort !== 'default');
  const clearFilters = () => { setBrowseFilters(emptyFilters); setSort('default'); setQuery(''); };

  const playTrack = useCallback((track: Track, list?: Track[], explicitPlaylist = false) => {
    if (track.playback_status !== 'ready') return;
    autoplayGeneration.current++;setChoosingNext(false);
    if (list) {setQueue(list.filter(t => t.playback_status === 'ready'));queueMode.current=explicitPlaylist || viewRef.current==='playlist'?'playlist':'smart';}
    if (currentRef.current?.id === track.id) {
      if (explicitPlaylist) {audio.current?.play().catch(() => notify('Playback could not start'));return;}
      if (audio.current?.paused) audio.current.play().catch(() => notify('Playback could not start. Please try again.'));
      else audio.current?.pause();
      return;
    }
    endSegment('next_track');
    currentRef.current = track; setCurrent(track); setElapsed(0); setDuration(track.duration);
    if (audio.current) { audio.current.src = `/api/v1/tracks/${track.id}/stream`; audio.current.play().catch(() => notify('Could not play this audio file')); }
  }, [endSegment, notify]);

  const next = useCallback(async (direction = 1) => {
    const index = queue.findIndex(t => t.id === currentRef.current?.id);
    const smart=userRef.current?.preferences.personalization_enabled && userRef.current.preferences.smart_autoplay_enabled;
    if (direction===1 && smart && !(queueMode.current==='playlist' && index>=0 && index<queue.length-1)) {
      const from=currentRef.current?.id;const generation=++autoplayGeneration.current;
      endSegment('next_track');setChoosingNext(true);
      const result=await loadDiscovery(from);
      if (generation!==autoplayGeneration.current || currentRef.current?.id!==from || !userRef.current?.preferences.personalization_enabled || !userRef.current.preferences.smart_autoplay_enabled) return;
      setChoosingNext(false);
      if (result?.next_items.length) {playTrack(result.next_items[0],result.next_items);queueMode.current='smart';return;}
      notify(result?'No other eligible tracks to continue with.':'Smart autoplay unavailable. Continuing your queue.');
      if (result) return;
    }
    if (!queue.length) return;
    const target = shuffle ? Math.floor(Math.random() * queue.length) : (index + direction + queue.length) % queue.length;
    playTrack(queue[target]);
  }, [queue, shuffle, playTrack, endSegment, loadDiscovery, notify]);

  const feedback = async (track: Track, signal: 'like' | 'dislike' | 'clear') => {
    try {
      await api('/feedback', { method: 'POST', body: JSON.stringify({ track_id: track.id, signal, event_id: crypto.randomUUID() }) });
      setTracks(t => t.map(item => item.id === track.id ? { ...item, feedback: signal } : item));
      setRec(r => r ? { ...r, items: r.items.map(item => item.id === track.id ? { ...item, feedback: signal } : item) } : null);
      if (userRef.current?.preferences.personalization_enabled) void loadDiscovery();
      if (signal === 'dislike') {
        if (viewRef.current === 'similar' && similarSeed.current !== null) await loadRec(similarSeed.current);
        else if (viewRef.current === 'for-you' && userRef.current?.preferences.personalization_enabled) await loadRec();
        notify('Removed from future recommendations');
      }
    } catch (e) { notify((e as Error).message); }
  };

  const preference = async (changes: Partial<User['preferences']>) => {
    if (!user) return false;
    const before=userRef.current || user;
    const stopping=changes.personalization_enabled===false || changes.smart_autoplay_enabled===false;
    if (stopping) {
      autoplayGeneration.current++;setChoosingNext(false);
      const pending={...before,preferences:{...before.preferences,...changes}};
      userRef.current=pending;setUser(pending);
      if (changes.personalization_enabled===false) {requestGeneration.current++;discoveryGeneration.current++;setDiscovery(null);setRec(null);}
    }
    setRefreshing(true);
    try {
      const result = await api<{ preferences: User['preferences'] }>('/preferences', { method: 'PATCH', body: JSON.stringify(changes) });
      const updated = { ...before, preferences: result.preferences };
      userRef.current = updated; setUser(updated); changeView('for-you'); setSidebar(false); setRefreshing(true);
      if (result.preferences.personalization_enabled) await loadRec();
      else { requestGeneration.current++;discoveryGeneration.current++;autoplayGeneration.current++;setChoosingNext(false);setDiscovery(null); setRec(null); await loadLibrary(); }
      return true;
    } catch (e) {if (stopping) {userRef.current=before;setUser(before);} notify((e as Error).message); return false; } finally { setRefreshing(false); }
  };

  const navigate = async (target: View, pid?: string) => {
    changeView(target); clearFilters(); setSidebar(false);
    if (pid) setPlaylistId(pid);
    if (target === 'for-you' && personalized) await loadRec().catch(e => notify(e.message));
    else await loadLibrary().catch(e => notify(e.message));
  };

  const saveMix = async (mix: TasteMix) => {
    if (!discovery) return;
    try {
      await api<Playlist>('/playlists/from-recommendation',{method:'POST',body:JSON.stringify({request_id:discovery.request_id,group_id:mix.id})});
      await loadLibrary();notify('Mix saved to your playlists');
    } catch (e) {notify((e as Error).message);}
  };

  const upload = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault(); if (!file || busy) return;
    const data = new FormData(e.currentTarget); data.set('file', file); setBusy(true); setUploadProgress(0);
    const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/v1/uploads');
    xhr.setRequestHeader('X-CSRF-Token', getCsrf());
    xhr.setRequestHeader('Idempotency-Key', crypto.randomUUID());
    xhr.upload.onprogress = ev => { if (ev.lengthComputable) setUploadProgress(Math.round(ev.loaded / ev.total * 100)); };
    xhr.onload = async () => {
      setBusy(false);
      if (xhr.status >= 200 && xhr.status < 300) { setModal(null); setFile(null); setUploadTitle(''); clearFilters(); changeView('library'); setFilter('private'); await loadLibrary(); notify('Track added. Preparing it for playback.'); }
      else { try { notify(JSON.parse(xhr.responseText).detail || 'Upload failed'); } catch { notify('Upload failed'); } }
    };
    xhr.onerror = () => { setBusy(false); notify('Connection lost. Please try again.'); };
    xhr.send(data);
  };

  useEffect(() => {
    if (!user) return;
    api<SessionState>('/session').then(auth => {
      let meta = document.querySelector<HTMLMetaElement>('meta[name=csrf]');
      if (!meta) { meta = document.createElement('meta'); meta.name = 'csrf'; document.head.appendChild(meta); }
      meta.content = auth.csrf_token;
    }).catch(() => {});
  }, [user?.id]);

  const title = view === 'studio' ? 'AI Studio' : view === 'for-you' ? (personalized ? 'Made for you' : 'All your music') : view === 'library' ? 'Your library' : view === 'liked' ? 'Liked tracks' : view === 'similar' ? `Sounds like ${similarTitle}` : playlists.find(p => p.id === playlistId)?.title || 'Playlist';
  const ready = tracks.filter(t => t.playback_status === 'ready');
  const heroTrack = visible.find(t => t.playback_status === 'ready');

  return <div className="app-shell">
    <audio ref={audio} preload="metadata" onLoadedMetadata={() => setDuration(audio.current?.duration || 0)} onTimeUpdate={() => setElapsed(audio.current?.currentTime || 0)}
      onPlay={() => { window.dispatchEvent(new Event('musicbox:library-play')); setPlaying(true); if (playStarted.current === null) { playStarted.current = performance.now(); if (currentRef.current) record(currentRef.current, 'play_start'); } }}
      onPause={() => { setPlaying(false); endSegment('pause'); if (!audio.current?.ended) void loadDiscovery(); }} onEnded={() => { setPlaying(false);endSegment('ended'); void next(); }} onError={() => { setPlaying(false); endSegment('error'); notify('Playback error. Check the audio file or connection.'); }} />
    {sidebar && <button className="sidebar-backdrop" aria-label="Close menu" onClick={() => setSidebar(false)} />}
    <aside className={`sidebar ${sidebar ? 'sidebar-open' : ''}`}>
      <a className="brand" href="/" aria-label="Musicbox"><span className="brand-icon"><AudioLines size={22} /></span><span>musicbox<span className="brand-dot">.</span></span></a>
      <div className="nav-label">YOUR MUSIC</div>
      <nav>
        <button className={view === 'for-you' ? 'nav-active' : ''} onClick={() => navigate('for-you')}><Sparkles size={19} /><span>For you</span><span className="nav-dot" /></button>
        <button aria-label="Library" className={view === 'library' ? 'nav-active' : ''} onClick={() => navigate('library')}><Library size={19} /><span>Library</span><span className="nav-count" aria-hidden="true">{tracks.length}</span></button>
        <button aria-label="Liked tracks" className={view === 'liked' ? 'nav-active' : ''} onClick={() => navigate('liked')}><Heart size={19} /><span>Liked tracks</span><span className="nav-count" aria-hidden="true">{tracks.filter(t => t.feedback === 'like').length}</span></button>
        <button aria-label="AI Studio" className={view === 'studio' ? 'nav-active' : ''} onClick={() => navigate('studio')}><AudioLines size={19} /><span>AI Studio</span><span className="studio-nav-tag">LOCAL</span></button>
      </nav>
      <div className="playlist-heading"><span className="nav-label">YOUR PLAYLISTS</span><button className="icon-button" aria-label="New playlist" onClick={() => setModal('playlist')}><Plus size={17} /></button></div>
      <div className="playlist-nav">{playlists.map(p => <button key={p.id} className={view === 'playlist' && playlistId === p.id ? 'selected' : ''} onClick={() => navigate('playlist', p.id)}><ListMusic size={16} /><span>{p.title}</span><small>{p.track_ids.length}</small></button>)}
        {!playlists.length && <p className="sidebar-empty">Keep good music<br />together.</p>}
      </div>
      <div className="sidebar-bottom"><div className="upload-note"><span className="small-icon"><Upload size={19} /></span><strong>Bring your own music</strong><p>Your tracks. Your private space.</p><button onClick={() => setModal('upload')}>Upload track <Plus size={15} /></button></div>
        <div className="profile"><span className="avatar"><Headphones size={17} /></span><span><strong>Your listening space</strong><small>Saved in this browser</small></span></div>
      </div>
    </aside>

    <main className="main-content">
      <header className="topbar"><button className="mobile-menu icon-button" aria-label="Open menu" onClick={() => setSidebar(true)}><ListMusic size={23} /></button>
        <div className="breadcrumb"><span>Home</span><span>/</span><strong>{view === 'for-you' ? 'For you' : title}</strong></div>
        <label className="search"><Search size={17} /><input id="search" placeholder="Search tracks or artists…" value={query} onChange={e => setQuery(e.target.value)} /><kbd>⌘ K</kbd></label>
        <button className="top-upload" onClick={() => setModal('upload')}><Plus size={17} /><span>Upload track</span></button>
      </header>

      {error ? <div className="empty-state"><Headphones size={40} /><h2>Unable to connect</h2><p>{error}</p><button className="primary-button" onClick={() => window.location.reload()}>Try again</button></div> : <>
        {view === 'studio' && user && <AIStudioPanel key={user.id} userId={user.id} tracks={tracks} onPreviewRequested={() => { autoplayGeneration.current++; setChoosingNext(false); audio.current?.pause(); }} onLibraryChanged={loadLibrary} onPlayTrack={track => playTrack(track, tracks)} />}
        {view === 'for-you' && !query && <section className="hero">
          <div className="hero-text"><span className="eyebrow"><span /> MUSIC, ON YOUR TERMS</span><h1>Find your sound.<span>Feel something new.</span></h1><p>A little of what you love.<br />A little you have yet to discover.</p>
            <div className="hero-actions"><button className="primary-button" disabled={!heroTrack} onClick={() => heroTrack && playTrack(heroTrack, visible)}><Play size={17} fill="currentColor" /> Start listening</button><button className="text-button" onClick={() => setModal('upload')}>Add your music <ArrowRight size={16} /></button></div>
            <div className="hero-detail"><span className="sound-bars"><i /><i /><i /><i /><i /></span><span>{ready.length.toLocaleString('en-US')} tracks ready to play</span><span className="detail-divider" /><span>Shared catalog + private library</span></div>
          </div>
          <div className="hero-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="hero-record"><div className="record-label"><AudioLines size={42} /><span>YOUR SOUND<br />YOUR SPACE</span></div></div><div className="floating-note note-one"><Music2 size={24} /></div><div className="floating-note note-two"><Sparkles size={22} /></div><span className="art-caption">MAKE ROOM FOR<br /><b>GOOD MUSIC.</b></span></div>
        </section>}

        {view !== 'studio' && <section className="collection">
          <div className="section-heading"><div><div className="eyebrow subtle">{view === 'for-you' && personalized ? 'TUNED TO YOUR TASTE' : 'A SOUND FOR EVERY MOMENT'}</div><h2>{title}<span className="section-count">{visible.length.toLocaleString('en-US')}</span></h2></div>
            <div className="collection-actions">{view === 'for-you' && <button className={`personalize-button ${personalized ? 'personalize-active' : ''}`} aria-pressed={personalized} disabled={refreshing || loading} onClick={async () => { clearFilters(); await preference({ personalization_enabled: !personalized }); }}><Sparkles size={16} /> Personalize</button>}
            <button className={`refresh-button ${refreshing ? 'is-refreshing' : ''}`} disabled={refreshing} onClick={async () => { setRefreshing(true); seed.current = (seed.current + 1) % (2 ** 31); try { if (view === 'for-you' && personalized) await loadRec(); else if (view === 'similar' && similarSeed.current !== null) await loadRec(similarSeed.current); else await loadLibrary(); } catch (e) { notify((e as Error).message); } finally { setRefreshing(false); } }}><RefreshCw size={16} />{view === 'for-you' && personalized ? 'Fresh picks' : 'Refresh'}</button></div>
          </div>

          {view === 'for-you' && <p className="browse-description">{personalized ? 'Personalization is on. Your genre, favorites and listening history shape these picks.' : 'Browse all your tracks. Turn on Personalize whenever you want recommendations.'}</p>}
          {view === 'for-you' && personalized && <div className="taste-controls"><div className="genre-pills">{Object.keys(labels).map(g => <button key={g} disabled={refreshing} className={user?.preferences.genre === g ? 'pill-selected' : ''} onClick={() => preference({ genre: g })}>{labels[g]}{user?.preferences.genre === g && <span />}</button>)}</div>
            <div className="listening-mode"><SlidersHorizontal size={16} /><button disabled={refreshing} aria-pressed={user?.preferences.mode === 'balanced'} className={user?.preferences.mode === 'balanced' ? 'mode-active' : ''} onClick={() => preference({ mode: 'balanced' })}>Balanced</button><button disabled={refreshing} aria-pressed={user?.preferences.mode === 'focus'} className={user?.preferences.mode === 'focus' ? 'mode-active' : ''} onClick={() => preference({ mode: 'focus' })}>Focus</button></div>
          </div>}
          {view === 'for-you' && personalized && <p className="browse-description">Preferred genre is a gentle boost. Focus favors closer matches; Balanced adds variety across your library.</p>}
          {view === 'for-you' && personalized && <DiscoveryPanel data={discovery} error={discoveryError} autoplay={Boolean(user?.preferences.smart_autoplay_enabled)} choosing={choosingNext} disabled={refreshing} onToggle={() => {autoplayGeneration.current++;setChoosingNext(false);void preference({smart_autoplay_enabled:!userRef.current?.preferences.smart_autoplay_enabled});}} onPlayMix={mix => {if(mix.items.length) playTrack(mix.items[0],mix.items,true);}} onSave={saveMix} onPlayNext={track => {playTrack(track,discovery?.next_items);queueMode.current='smart';}} onRefresh={() => {void loadDiscovery();}} />}
          {view === 'library' && <div className="library-tabs">{[['all', 'All tracks'], ['private', 'My uploads'], ['shared', 'Shared catalog']].map(([id, text]) => <button key={id} className={filter === id ? 'tab-active' : ''} onClick={() => setFilter(id)}>{text}</button>)}</div>}
          <div className="browse-panel"><div className="browse-filter-heading"><span><SlidersHorizontal size={15} /> Find your next track</span><button disabled={!hasFilters} onClick={clearFilters}>Clear filters</button></div>
            <div className="browse-fields">
              <label>Genre<select aria-label="Filter by genre" value={browseFilters.genre} onChange={e => setBrowseFilters(f => ({ ...f, genre: e.target.value }))}><option value="all">All genres</option>{Object.entries(labels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
              <label>Artist<select aria-label="Filter by artist" value={browseFilters.artist} onChange={e => setBrowseFilters(f => ({ ...f, artist: e.target.value }))}><option value="all">All artists</option>{artists.map(artist => <option key={artist} value={artist}>{artist}</option>)}</select></label>
              <label>Source<select aria-label="Filter by source" value={browseFilters.source} onChange={e => setBrowseFilters(f => ({ ...f, source: e.target.value }))}><option value="all">All sources</option><option value="catalog">Catalog</option><option value="upload">My uploads</option><option value="demo">Demo audio</option></select></label>
              <label>Playback<select aria-label="Filter by playback status" value={browseFilters.status} onChange={e => setBrowseFilters(f => ({ ...f, status: e.target.value }))}><option value="all">All statuses</option><option value="ready">Ready to play</option><option value="preparing">Preparing</option><option value="failed">Processing failed</option></select></label>
              <label>Sort by<select aria-label="Sort tracks" value={sort} onChange={e => setSort(e.target.value as TrackSort)}><option value="default">{view === 'similar' || (view === 'for-you' && personalized) ? 'Recommended order' : 'Newest first'}</option><option value="title">Title A–Z</option><option value="artist">Artist A–Z</option><option value="duration">Shortest first</option></select></label>
            </div>
          </div>
          {(view === 'similar' || (view === 'for-you' && personalized)) && rec && <div className="recommendation-note"><span className="status-dot" /><span>{rec.strategy === 'cold_start_diverse_audio' ? 'An audio-diverse starting mix. Like tracks or keep listening to shape your recommendations.' : rec.strategy === 'hybrid_audio_profiles' ? rec.profile_weights?.long_term && rec.profile_weights?.session ? 'Audio matches shaped by your lasting taste and recent listening.' : rec.profile_weights?.long_term ? 'Audio matches shaped by your lasting taste.' : 'Audio matches shaped by your recent listening.' : rec.engine.startsWith('clap:') ? 'Audio similarity with CLAP' : rec.engine.startsWith('spectral-') ? 'Audio similarity with spectral analysis' : 'Audio analysis unavailable; using metadata.'}{rec.shortage_reason && ' · Results adjusted to the available catalog'}</span></div>}

          {loading ? <div className="track-grid">{Array.from({ length: 8 }).map((_, i) => <div className="skeleton-card" key={i}><div /><span /><span /></div>)}</div> : !visible.length ? <div className="empty-state"><Music2 size={37} /><h3>{hasFilters ? 'No tracks found' : view === 'liked' ? 'Find your first favorite' : 'No tracks here yet'}</h3><p>{hasFilters ? 'Try another search or clear your filters.' : 'Explore the shared catalog or bring your own music.'}</p><button className="primary-button" onClick={() => setModal('upload')}><Upload size={16} /> Upload track</button></div> : <div className="track-grid">{visible.map(t => <TrackCard key={t.id} track={t} active={current?.id === t.id} onPlay={() => playTrack(t, visible)} onLike={() => feedback(t, t.feedback === 'like' ? 'clear' : 'like')} onMenu={() => { setSelected(t); setModal('track'); }} onExposure={() => record(t, 'exposure')} />)}</div>}
        </section>}
        {view === 'for-you' && <section className="discovery-banner"><span className="discovery-icon"><Headphones size={27} /></span><div><h3>Keep the tracks you love.</h3><p>Save your favorites now. Use them for recommendations whenever you turn on Personalize.</p></div><button onClick={() => navigate('liked')}>My favorites <ArrowRight size={17} /></button></section>}
        <footer className="page-footer"><span>MUSICBOX · YOUR SOUND, YOUR SPACE</span><span>Move at your own rhythm.</span></footer>
      </>}
    </main>

    <div className={`player ${current ? 'player-active' : ''}`}>
      <div className="now-playing">{current ? <Cover track={current} className="mini-cover" /> : <span className="mini-placeholder"><Disc3 size={23} /></span>}<div><strong dir="auto">{current?.title || 'Ready when you are'}</strong><span dir="auto">{current?.artist || 'Pick a track to begin'}</span></div>{current && <button className={`icon-button ${tracks.find(t => t.id === current.id)?.feedback === 'like' ? 'liked' : ''}`} aria-label="Like current track" onClick={() => feedback(current, tracks.find(t => t.id === current.id)?.feedback === 'like' ? 'clear' : 'like')}><Heart size={18} /></button>}</div>
      <div className="player-center"><div className="transport" dir="ltr"><button className={`icon-button ${shuffle ? 'control-on' : ''}`} aria-label="Shuffle" aria-pressed={shuffle} onClick={() => setShuffle(!shuffle)}><Shuffle size={17} /></button><button className="icon-button" disabled={!current} aria-label="Previous track" onClick={() => next(-1)}><SkipBack size={19} fill="currentColor" /></button><button className="main-play" disabled={!current} aria-label={playing || choosingNext ? 'Pause' : 'Resume playback'} onClick={() => {autoplayGeneration.current++;setChoosingNext(false);if (choosingNext || playing) audio.current?.pause();else audio.current?.play().catch(() => notify('Playback could not start'));}}>{playing || choosingNext ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}</button><button className="icon-button" disabled={!current} aria-label="Next track" onClick={() => next()}><SkipForward size={19} fill="currentColor" /></button><button className={`icon-button ${repeat ? 'control-on' : ''}`} aria-label="Repeat track" aria-pressed={repeat} onClick={() => setRepeat(!repeat)}><Repeat size={17} /></button></div>
        <div className="timeline" dir="ltr"><span>{time(elapsed)}</span><input aria-label="Playback position" type="range" min={0} max={duration || 1} step={.1} value={Math.min(elapsed, duration || 1)} disabled={!current} onChange={e => { if (audio.current) audio.current.currentTime = Number(e.target.value); setElapsed(Number(e.target.value)); }} style={{ '--range-progress': `${elapsed / (duration || 1) * 100}%` } as React.CSSProperties} /><span>{time(duration)}</span></div>
      </div>
      <div className="player-tools"><button className="icon-button" aria-label="Playback queue" onClick={() => setModal('queue')}><ListMusic size={19} /></button><Volume2 size={18} /><input type="range" aria-label="Volume" min="0" max="1" step="0.01" value={volume} onChange={e => setVolume(Number(e.target.value))} /></div>
    </div>

    {toast && <div className="toast" role="status"><Check size={17} />{toast}</div>}
    {modal && <div className="modal-backdrop" onClick={() => !busy && setModal(null)}><section className={`modal ${modal === 'queue' ? 'queue-modal' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close dialog" disabled={busy} onClick={() => setModal(null)}><X size={20} /></button>
      {modal === 'upload' && <><span className="modal-symbol"><Upload size={25} /></span><h2 id="modal-title">Bring your own music</h2><p>Private to this browser. Keep its cookies to retain access to your uploads.</p><form ref={formRef} onSubmit={upload}><label className="file-drop"><Upload size={27} /><strong>{file?.name || 'Choose an audio file'}</strong><span>MP3 or WAV · up to 100 MiB and 10 minutes</span><input type="file" accept=".mp3,.wav" required onChange={e => { const f = e.target.files?.[0] || null; setFile(f); setUploadTitle(f?.name.replace(/\.[^.]+$/, '') || ''); }} /></label><label className="form-label">Track title<input name="title" value={uploadTitle} onChange={e => setUploadTitle(e.target.value)} maxLength={160} required /></label><label className="form-label">Artist<input name="artist" maxLength={160} placeholder="Artist name" /></label><label className="form-label">Genre<select name="genre" defaultValue={user?.preferences.genre || 'Electronic'}>{Object.entries(labels).map(([g, l]) => <option key={g} value={g}>{l}</option>)}</select></label><button className="primary-button full-width" disabled={!file || busy}>{busy ? <><LoaderCircle className="spin" size={17} /> Uploading {uploadProgress}%</> : <><Plus size={17} /> Add to library</>}</button></form></>}
      {modal === 'playlist' && <><span className="modal-symbol"><ListMusic size={25} /></span><h2 id="modal-title">Create a new playlist</h2><p>Keep your favorite tracks together.</p><form onSubmit={async e => { e.preventDefault(); const title = new FormData(e.currentTarget).get('title'); try { const p = await api<Playlist>('/playlists', { method: 'POST', body: JSON.stringify({ title }) }); setModal(null); await loadLibrary(); setPlaylistId(p.id); changeView('playlist'); } catch (e) { notify((e as Error).message); } }}><label className="form-label">Playlist name<input name="title" maxLength={100} required autoFocus placeholder="e.g. The way home" /></label><button className="primary-button full-width"><Plus size={17} /> Create playlist</button></form></>}
      {modal === 'queue' && <><h2 id="modal-title">Up next</h2><p>{queue.length.toLocaleString('en-US')} tracks</p>{queue.map((t, i) => <button key={t.id} className={`queue-row ${current?.id === t.id ? 'queue-current' : ''}`} onClick={() => playTrack(t)}><small>{i + 1}</small><Cover track={t} className="mini-cover" /><span><strong dir="auto">{t.title}</strong><small>{t.artist}</small></span><Play size={17} /></button>)}{!queue.length && <p>Play a track to build your queue.</p>}</>}
      {modal === 'track' && selected && <><div className="selected-track"><Cover track={selected} className="mini-cover" /><div><h2 id="modal-title" dir="auto">{selected.title}</h2><p dir="auto">{selected.artist}</p></div></div><div className="track-actions"><button onClick={async () => { setModal(null); setSimilarTitle(selected.title); similarSeed.current = selected.id; clearFilters(); changeView('similar'); setRefreshing(true); try { await loadRec(selected.id); } catch (e) { notify((e as Error).message); } finally { setRefreshing(false); } }}><Sparkles size={19} /> Similar tracks</button><button onClick={async () => { const saved = await preference({ personalization_enabled: true, seed_track_ids: [...(user?.preferences.seed_track_ids || []).filter(id => id !== selected.id && tracks.some(t => t.id === id)), selected.id].slice(-3) }); if (saved) { setModal(null); notify('Added to your reference tracks'); } }}><Disc3 size={19} /> Use as a reference track</button><button onClick={() => { feedback(selected, 'dislike'); setModal(null); }}><ThumbsDown size={19} /> Recommend this less</button>{playlists.map(p => <button key={p.id} onClick={async () => { try { await api(`/playlists/${p.id}/tracks`, { method: 'POST', body: JSON.stringify({ track_id: selected.id }) }); await loadLibrary(); setModal(null); notify(`Added to ${p.title}`); } catch (e) { notify((e as Error).message); } }}><Plus size={19} /> Add to {p.title}</button>)}{view === 'playlist' && playlistId && <button onClick={async () => { await api(`/playlists/${playlistId}/tracks/${selected.id}`, { method: 'DELETE' }); setModal(null); await loadLibrary(); }}><Trash2 size={19} /> Remove from playlist</button>}{selected.visibility === 'private' && <button className="danger-action" onClick={async () => { try { await api(`/tracks/${selected.id}`, { method: 'DELETE' }); if (current?.id === selected.id) { audio.current?.pause(); setCurrent(null); currentRef.current = null; } setQueue(q => q.filter(t => t.id !== selected.id)); setUser(u => u ? { ...u, preferences: { ...u.preferences, seed_track_ids: u.preferences.seed_track_ids.filter(id => id !== selected.id) } } : u); setModal(null); await loadLibrary(); notify('Track deleted from your library'); } catch (e) { notify((e as Error).message); } }}><Trash2 size={19} /> Delete from library</button>}</div>{selected.error && <p className="processing-error">Audio processing failed. Please try a different file.</p>}</>}
    </section></div>}
  </div>;
}
