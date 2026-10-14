'use client';
import { useEffect, useRef, useState } from 'react';
import { AudioLines, Check, Download, Headphones, LoaderCircle, Mic2, Music2, Pause, Play, Plus, Search, ShieldCheck, Sparkles } from 'lucide-react';
import { ApiError, time, Track } from './lib/api';
import { generation, GenerationInput, GenerationJob, GenerationCandidate, GenerationStatus, terminal, mergeGenerationJobs } from './lib/generation';
import GenerationVariations from './GenerationVariations';

const languages: Record<string, string> = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', zh: 'Chinese', ja: 'Japanese', ko: 'Korean' };
const phases: Record<string, string> = { queued: 'In queue', submitting: 'Preparing the model', planning: 'Planning the composition', awaiting_memory: 'Waiting for available memory', rendering: 'Rendering audio', running: 'Rendering audio', ingesting: 'Checking your audio', completed: 'Ready to listen', awaiting_engine_idle: 'Interrupted · waiting for engine', interrupted: 'Interrupted', failed: 'Generation failed', cancelled: 'Cancelled' };
type Pending = { key: string; body: GenerationInput };

export default function AIStudioPanel({ userId, tracks, onPreviewRequested, onLibraryChanged, onPlayTrack }: {
  userId: string; tracks: Track[]; onPreviewRequested: () => void;
  onLibraryChanged: () => Promise<unknown>; onPlayTrack: (track: Track) => void;
}) {
  const [prompt, setPrompt] = useState('');
  const [mode, setMode] = useState<'vocal' | 'instrumental'>('vocal');
  const [lyrics, setLyrics] = useState('');
  const [language, setLanguage] = useState('en');
  const [references, setReferences] = useState<number[]>([]);
  const [primary, setPrimary] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [status, setStatus] = useState<GenerationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const submittingRef = useRef(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [changing, setChanging] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [waveform, setWaveform] = useState<{ id: string; bars: number[] } | null>(null);
  const [waveError, setWaveError] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const mounted = useRef(true);
  const storageKey = `musicbox.generation.pending.${userId}`;

  useEffect(() => {
    mounted.current = true;
    try {
      const stored = sessionStorage.getItem(storageKey);
      if (stored) { const value = JSON.parse(stored) as Pending; pendingRef.current = value; setPending(value); }
    } catch { /* Storage may be disabled; in-memory retries still retain the key. */ }
    const controller = new AbortController();
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      if (document.visibilityState === 'hidden') { timer = setTimeout(refresh, 2000); return; }
      try {
        const [state, result] = await Promise.all([generation.status(controller.signal), generation.jobs(controller.signal)]);
        if (!stopped) { setStatus(state); setJobs(items => mergeGenerationJobs(items, result.jobs)); setLoading(false); }
      } catch {
        if (!stopped) { setStatus(null); setLoading(false); setError('Could not reach your local Studio. Your existing requests are preserved.'); }
      }
      if (!stopped) timer = setTimeout(refresh, 2000);
    };
    void refresh();
    const pause = () => audio.current?.pause();
    window.addEventListener('musicbox:library-play', pause);
    return () => { mounted.current = false; stopped = true; controller.abort(); clearTimeout(timer); audio.current?.pause(); window.removeEventListener('musicbox:library-play', pause); };
  }, [storageKey]);

  useEffect(() => {
    if (!preview || !previewUrl) return;
    const controller = new AbortController();
    let live = true;
    const context = new AudioContext();
    setWaveError(false);
    (async () => {
      try {
        const response = await fetch(previewUrl, { credentials: 'same-origin', signal: controller.signal });
        if (!response.ok) throw new Error('Audio unavailable');
        const decoded = await context.decodeAudioData(await response.arrayBuffer());
        const samples = decoded.getChannelData(0), step = Math.max(1, Math.floor(samples.length / 64));
        const bars = Array.from({ length: 64 }, (_, i) => {
          let sum = 0; for (let j = i * step; j < Math.min((i + 1) * step, samples.length); j++) sum += samples[j] * samples[j];
          return Math.sqrt(sum / step);
        });
        const peak = Math.max(...bars, .0001);
        if (live) setWaveform({ id: preview, bars: bars.map(value => Math.max(.06, value / peak)) });
      } catch { if (live) setWaveError(true); }
      finally { await context.close(); }
    })();
    return () => { live = false; controller.abort(); };
  }, [preview, previewUrl]);

  const create = async () => {
    if (submittingRef.current) return;
    const attempt = pendingRef.current || { key: crypto.randomUUID(), body: { prompt: prompt.trim(), mode, lyrics: mode === 'vocal' ? lyrics.trim() : '', language, reference_track_ids: references, primary_reference_track_id: audioReference ? primary : null } };
    pendingRef.current = attempt; setPending(attempt);
    try { sessionStorage.setItem(storageKey, JSON.stringify(attempt)); } catch { /* Memory fallback. */ }
    submittingRef.current = true; setSubmitting(true); setError('');
    try {
      const { job } = await generation.create(attempt.body, attempt.key);
      if (!mounted.current) return; // Reopening retries the same persisted key.
      setJobs(items => [job, ...items.filter(item => item.id !== job.id)]);
      pendingRef.current = null; setPending(null);
      try { sessionStorage.removeItem(storageKey); } catch { /* Memory fallback. */ }
    } catch (e) {
      if (!mounted.current) return;
      // A definite validation failure can be edited; transport/5xx stays uncertain.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        pendingRef.current = null; setPending(null);
        try { sessionStorage.removeItem(storageKey); } catch { /* Memory fallback. */ }
        setError(e.message);
      } else setError('The request outcome is unknown. Retry the same request to recover it safely.');
    } finally { submittingRef.current = false; if (mounted.current) setSubmitting(false); }
  };

  const selectReference = (id: number) => {
    if (references.includes(id)) { setReferences(items => items.filter(item => item !== id)); if (primary === id) setPrimary(null); }
    else if (references.length < 3) setReferences(items => [...items, id]);
  };
  const playPreview = async (job: GenerationJob, candidate?: GenerationCandidate) => {
    const url = candidate?.audio_url || job.audio_url;
    const key = candidate ? `${job.id}:${candidate.index}` : job.id;
    if (!url || !audio.current) return;
    if (preview === key && previewUrl === url && !audio.current.paused) { audio.current.pause(); return; }
    onPreviewRequested();
    if (preview !== key || previewUrl !== url) { audio.current.src = url; setPreview(key); setPreviewUrl(url); setElapsed(0); }
    try { await audio.current.play(); } catch { setError('Could not play this take. Try again when the local app is available.'); }
  };
  const changeJob = async (job: GenerationJob, index?: number) => {
    if (changing || saving) return;
    setChanging(job.id); setError('');
    try {
      const result = index === undefined ? await generation.cancel(job.id) : await generation.select(job.id, index);
      if (!mounted.current) return;
      setJobs(items => mergeGenerationJobs(items, [result.job]));
      if (index !== undefined && (preview === job.id || preview?.startsWith(job.id + ':'))) {
        audio.current?.pause(); setPreview(null); setPreviewUrl(null);
      }
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setChanging(null); }
  };
  const save = async (job: GenerationJob) => {
    if (saving || changing) return;
    setSaving(job.id); setError('');
    try {
      const { track } = await generation.save(job.id);
      if (!mounted.current) return;
      setJobs(items => items.map(item => item.id === job.id ? { ...item, saved_track_id: track.id } : item));
      await onLibraryChanged();
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setSaving(null); }
  };
  const isYue = status?.capabilities.engine === 'yue2' || status?.capabilities.takes === 8;
  const duration = status?.capabilities.duration_seconds || 20;
  const audioReference = status?.capabilities.audio_reference !== false;
  const completed = jobs.filter(job => job.status === 'completed' && job.request.mode === mode && job.request.duration_seconds === duration && (!isYue || job.request.engine === 'yue2') && job.elapsed_seconds);
  const estimate = completed.length ? Math.round(completed.reduce((sum, job) => sum + job.elapsed_seconds!, 0) / completed.length) : null;
  const eligible = tracks.filter(track => track.playback_status === 'ready');
  const canGenerate = Boolean(!submitting && (pending || (status?.available && prompt.trim() && (mode === 'instrumental' || lyrics.trim()))));

  return <section className="ai-studio" aria-label="AI music studio">
    <audio data-studio-preview ref={audio} preload="none" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={() => setElapsed(audio.current?.currentTime || 0)} onError={() => setError('Preview audio is unavailable. Your take is still saved.')} />
    <header className="studio-heading"><div><span className="eyebrow"><Sparkles size={13} /> YOUR NEXT SOUND</span><h1>Make something <em>only you</em> would.</h1><p>Start with your taste. Give it a direction. Find a new sound.</p></div><span className={`engine-pill ${status?.available ? 'online' : ''}`}><span />{loading ? 'Checking local engine' : status?.available ? status.active ? 'Local engine busy' : 'Local engine ready' : 'Local engine offline'}</span></header>
    <div className="studio-grid">
      <aside className="studio-panel inspirations"><div className="studio-panel-title"><span className="step-number">01</span><h2>Your inspirations</h2><small>{references.length}/3</small></div><p className="studio-help">{audioReference ? 'Choose a few sounds you love. Use one as an audio reference.' : 'Choose a few sounds you love to guide the musical style.'}</p><label className="studio-search"><Search size={15} /><input aria-label="Search inspirations" placeholder="Find a track…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <div className="inspiration-list">{eligible.filter(track => `${track.title} ${track.artist}`.toLowerCase().includes(search.toLowerCase())).map(track => <div key={track.id} className={`inspiration ${references.includes(track.id) ? 'chosen' : ''}`} data-testid="studio-reference"><label><input type="checkbox" checked={references.includes(track.id)} disabled={!references.includes(track.id) && references.length >= 3} onChange={() => selectReference(track.id)} /><span className="inspiration-art" style={{ '--inspiration-color': track.color } as React.CSSProperties}><DiscArtwork /></span><span className="inspiration-info"><strong>{track.title}</strong><small>{track.artist} · {track.genre}</small></span></label>{references.includes(track.id) && audioReference && <button aria-pressed={primary === track.id} className="primary-reference" onClick={() => setPrimary(primary === track.id ? null : track.id)}>{primary === track.id ? <><AudioLines size={12} /> Audio reference</> : 'Use audio reference'}</button>}</div>)}{!eligible.length && <div className="studio-empty small"><Music2 size={25} /><p>Your ready library tracks will appear here.</p></div>}</div><div className="reference-note"><Headphones size={15} /><p>{audioReference ? 'Audio reference uses the first 20 seconds. Other selections add genre cues.' : 'Selections add genre cues. Direct audio conditioning is unavailable in this version.'}</p></div>
      </aside>
      <div className="studio-panel composer"><div className="studio-panel-title"><span className="step-number">02</span><h2>Shape your sound</h2><Sparkles size={16} /></div>
        <div className="studio-modes" aria-label="Generation mode"><button aria-pressed={mode === 'vocal'} onClick={() => setMode('vocal')}><Mic2 size={16} /> With vocals</button><button aria-pressed={mode === 'instrumental'} onClick={() => setMode('instrumental')}><Music2 size={16} /> Instrumental</button></div>
        <label className="studio-field"><span>Music prompt <small>{prompt.length}/1200</small></span><textarea aria-label="Music prompt" maxLength={1200} rows={5} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Late-night indie pop, warm analog synths, soft drums and an intimate voice. A little nostalgic, a little hopeful…" /></label><p className="studio-help prompt-hint">Describe the mood, instruments, rhythm and vocal character.</p>
        {mode === 'vocal' && <><label className="studio-field"><span>Lyrics <small>Your words, your voice</small></span><textarea aria-label="Lyrics" maxLength={4000} rows={6} value={lyrics} onChange={e => setLyrics(e.target.value)} placeholder={'[Verse]\nWrite a short verse…\n\n[Chorus]\nGive it a line to remember.'} /></label><label className="studio-field"><span>Vocal language</span><select aria-label="Vocal language" value={language} onChange={e => setLanguage(e.target.value)}>{(status?.capabilities.languages || Object.keys(languages)).map(code => <option key={code} value={code}>{languages[code] || code}{!(status?.capabilities.tested_languages || ['en']).includes(code) ? ' · not yet evaluated locally' : ''}</option>)}</select></label></>}
        <div className="studio-specs"><span><AudioLines size={15} /><strong>{status?.capabilities.duration_is_maximum ? 'Up to ' : ''}{duration} seconds</strong></span><span>{isYue ? '8 variations' : '1 take'}</span><span>Native WAV</span></div>
        {isYue && <p className="studio-help">YuE2 creates eight variations, one at a time. Listen and choose the one you like best.</p>}
        <div className="studio-unavailable"><button disabled title="Automatic lyrics requires a language model that is not enabled on this laptop.">Auto lyrics</button><button disabled title="Prompt refinement requires a language model that is not enabled on this laptop.">Refine prompt</button><span>Manual controls in this local version</span></div>
        <button className="studio-generate" disabled={!canGenerate} onClick={event => { if (event.detail <= 1) void create(); }}>{submitting ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />}{submitting ? 'Submitting request…' : pending ? 'Retry same request' : jobs.length ? 'Generate another take' : 'Generate take'}<ArrowIcon /></button>
        <p className="studio-timing">{estimate ? `Estimated ${time(estimate)} from your completed ${mode} takes. Timing varies.` : 'CPU generation can take several minutes. Your request survives refresh.'}</p>
        {!loading && !status?.available && <p className="studio-notice">The local model is not running yet. Your previous takes remain available.</p>}
      </div>
      <aside className="studio-panel takes"><div className="studio-panel-title"><span className="step-number">03</span><h2>Your takes</h2><small>{jobs.length}</small></div><p className="studio-help">A private space to listen, keep and explore.</p>
        {loading ? <div className="studio-empty"><LoaderCircle className="spin" size={25} /><p>Loading your takes…</p></div> : !jobs.length ? <div className="studio-empty"><span className="empty-record"><AudioLines size={29} /></span><h3>Your next favorite<br />starts here.</h3><p>Give your ideas a sound.<br />Your first take will appear here.</p><span className="empty-caption">MADE BY YOU · JUST FOR YOU</span></div> : <div className="take-list">{jobs.map((job, index) => {
          const active = preview === job.id || Boolean(preview?.startsWith(job.id + ':')), saved = tracks.find(track => track.id === job.saved_track_id);
          const audition = job.candidates?.find(candidate => candidate.index === (preview?.startsWith(job.id + ':') ? Number(preview.split(':')[1]) : job.selected_candidate_index));
          const observedDuration = audition?.duration_seconds || job.request.duration_seconds;
          return <article className={`take-card ${active ? 'take-selected' : ''}`} key={job.id}><div className="take-heading"><span className="take-index">TAKE {String(jobs.length - index).padStart(2, '0')}</span><span className={`take-status ${job.status}`} role="status">{!terminal(job) && <LoaderCircle size={11} className="spin" />}{phases[job.phase] || phases[job.status] || job.status}</span></div><h3>{job.request.prompt}</h3><p className="take-meta">{job.request.mode === 'vocal' ? languages[job.request.language] + ' vocals' : 'Instrumental'} · {time(observedDuration)} · Seed {audition?.seed || job.request.seed}</p>
            {job.audio_url ? <><div className="take-preview"><button aria-label={active && playing ? 'Pause take' : 'Preview take'} onClick={() => void playPreview(job)}>{active && playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}</button><div className="take-waveform" data-testid="studio-waveform" aria-label="Decoded audio waveform">{active && waveform?.id === preview ? waveform.bars.map((bar, i) => <i key={i} className={i / 64 < elapsed / observedDuration ? 'played' : ''} style={{ height: `${bar * 100}%` }} />) : <span>{active && waveError ? 'Waveform unavailable' : active ? 'Reading audio…' : 'Preview to load waveform'}</span>}</div><small>{active ? time(elapsed) : time(observedDuration)}</small></div><div className="take-actions"><a href={job.audio_url} download={`musicbox-${job.id}${job.selected_candidate_index ? '-variation-' + job.selected_candidate_index : ''}.wav`} aria-label="Download WAV"><Download size={14} /> WAV</a>{job.saved_track_id ? <span className="take-saved"><Check size={13} /> Saved to your library</span> : <button disabled={saving !== null || changing !== null} onClick={() => void save(job)}>{saving === job.id ? <LoaderCircle className="spin" size={13} /> : <Plus size={14} />} Save to library</button>}</div>{job.saved_track_id && <button className="take-library-play" disabled={saved?.playback_status !== 'ready'} onClick={() => saved && onPlayTrack(saved)}><Play size={12} />{saved?.playback_status === 'ready' ? 'Play in library' : saved?.playback_status === 'failed' ? 'Library audio processing failed' : saved ? 'Preparing library audio…' : 'Saved copy is not in this library'}</button>}</> : <div className="take-message">{job.error || (job.status === 'interrupted' ? 'Your request was interrupted. It will not be submitted again automatically.' : job.status === 'queued' ? 'Waiting for the local engine. One take renders at a time.' : job.status === 'cancelled' ? 'Generation cancelled. Completed variations are preserved.' : 'The local model is working on your sound. You can keep editing or leave this page.')}</div>}
            <GenerationVariations job={job} busy={changing !== null || saving !== null} onPreview={candidate => void playPreview(job, candidate)} onSelect={candidateIndex => void changeJob(job, candidateIndex)} onCancel={() => void changeJob(job)} />
          </article>;
        })}</div>}
        <div className="studio-private"><ShieldCheck size={14} /><span>Private to this browser. Keep your cookies to retain access.</span></div>
      </aside>
    </div>
    {error && <div className="studio-error" role="alert"><span>{error}</span><button aria-label="Dismiss Studio message" onClick={() => setError('')}><Check size={15} /></button></div>}
    <div className="studio-footnote"><ShieldCheck size={14} /><span>Created locally with {isYue ? 'YuE2 · Listener selection from eight variations' : 'ACE-Step · Short takes for exploring ideas'}</span><span>Listening quality and lyric accuracy can vary.</span></div>
  </section>;
}

function DiscArtwork() { return <svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><circle cx="24" cy="24" r="18" fill="#101528" /><circle cx="24" cy="24" r="12" stroke="currentColor" strokeOpacity=".7" /><circle cx="24" cy="24" r="5" fill="currentColor" /></svg>; }
function ArrowIcon() { return <span aria-hidden="true">↗</span>; }
