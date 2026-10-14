'use client';
import { useState } from 'react';
import { ListMusic, Play, Plus, Sparkles } from 'lucide-react';
import { Discovery, TasteMix, Track } from './lib/api';

export default function DiscoveryPanel({ data, error, autoplay, choosing, disabled, onToggle, onPlayMix, onSave, onPlayNext, onRefresh }: {
  data: Discovery | null; error: string; autoplay: boolean; choosing: boolean; disabled: boolean;
  onToggle: () => void; onPlayMix: (mix: TasteMix) => void; onSave: (mix: TasteMix) => Promise<void>;
  onPlayNext: (track: Track) => void; onRefresh: () => void;
}) {
  const [saving, setSaving] = useState<string | null>(null);
  const next=data?.next_items[0];
  return <section className="taste-discovery" aria-label="Taste discovery">
    <div className="discovery-heading"><div><span className="eyebrow subtle">DIFFERENT SIDES OF YOUR TASTE</span><h3>Your taste mixes</h3></div><button className="text-button" disabled={disabled} onClick={onRefresh}>Refresh mixes</button></div>
    <p className="discovery-description">Separate listening interests, grouped by sound. Each mix explores one side of your taste.</p>
    {error ? <p className="discovery-description" role="status">{error}</p> : !data ? <p className="discovery-description">Finding your listening interests…</p> : !data.interest_groups.length ? <div className="taste-empty"><Sparkles size={20} /><p>Like tracks or listen for at least 30 seconds to start building your taste mixes. Your starting recommendations are exploratory.</p></div> : <div className="taste-mixes">{data.interest_groups.map((mix,i) => <article className="taste-mix" data-testid="taste-mix" key={mix.id} style={{ '--mix-accent': ['#83aaff','#b095ff','#f16b85'][i%3] } as React.CSSProperties}>
      <div className="mix-icon"><ListMusic size={23} /><span>{mix.items.length} tracks</span></div><h4 dir="auto">{mix.title}</h4><p>{mix.signal_count} taste {mix.signal_count===1?'signal':'signals'} · Audio similarity</p>
      <ol>{mix.items.slice(0,3).map(track => <li key={track.id}><strong dir="auto">{track.title}</strong><span dir="auto">{track.artist}</span></li>)}</ol>
      <div className="mix-actions"><button disabled={!mix.items.length} onClick={() => onPlayMix(mix)}><Play size={14} fill="currentColor" /> Play mix</button><button disabled={saving!==null || !mix.items.length} onClick={async () => {setSaving(mix.id);try {await onSave(mix);} finally {setSaving(null);}}}><Plus size={14} />{saving===mix.id?'Saving…':'Save mix'}</button></div>
    </article>)}</div>}
    {!!data?.additional_interest_count && <p className="discovery-description">Showing your six strongest interests. Other taste signals are kept for future mixes.</p>}
    <div className="smart-next"><div><span className="eyebrow subtle">NEXT UP</span><h4 dir="auto">{choosing?'Choosing your next track…':next?.title || 'Your next sound'}</h4><p>{data?.next_strategy==='recent_context'?'Following your current sound and recent listening.':data?.next_strategy==='lasting_interests'?'A match for one of your listening interests.':'An exploratory pick until your listening taste develops.'}</p></div><div className="smart-next-actions"><button aria-pressed={autoplay} className={`personalize-button ${autoplay?'personalize-active':''}`} disabled={disabled} onClick={onToggle}><Sparkles size={15} /> Smart autoplay</button>{next && <button disabled={choosing} className="text-button" onClick={() => onPlayNext(next)}><Play size={14} /> Play next</button>}</div></div>
  </section>;
}
