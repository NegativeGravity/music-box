import { Check, LoaderCircle, Play } from 'lucide-react';
import { GenerationCandidate, GenerationJob, terminal } from './lib/generation';

export default function GenerationVariations({ job, busy, onPreview, onSelect, onCancel }: {
  job: GenerationJob; busy: boolean; onPreview: (candidate: GenerationCandidate) => void;
  onSelect: (index: number) => void; onCancel: () => void;
}) {
  if (!job.candidates?.length) return null;
  const ready = job.candidates.filter(candidate => candidate.status === 'completed').length;
  return <div className="variation-group">
    <div className="variation-summary"><strong>{ready} of {job.candidates.length} ready</strong><span>Choose your favorite</span></div>
    <div className="variation-grid">{job.candidates.map(candidate => {
      const selected = job.selected_candidate_index === candidate.index;
      return <div className={`variation ${selected ? 'selected' : ''}`} key={candidate.index} data-testid="studio-variation">
        <div><strong>Variation {String(candidate.index).padStart(2, '0')}</strong><small>{candidate.status === 'completed' ? `${Math.round(candidate.duration_seconds || 0)}s${candidate.duration_capped ? ' · duration limit' : ''}` : candidate.status}</small></div>
        {candidate.audio_url ? <div className="variation-actions"><button aria-label={`Preview variation ${candidate.index}`} onClick={() => onPreview(candidate)}><Play size={12} /></button><button aria-label={`Use variation ${candidate.index}`} aria-pressed={selected} disabled={busy || selected} onClick={() => onSelect(candidate.index)}>{selected ? <><Check size={11} /> Selected</> : 'Use this'}</button></div> : candidate.status === 'rendering' ? <LoaderCircle className="spin" size={14} /> : <span className="variation-waiting" aria-hidden="true">·</span>}
        {candidate.error && <p className="variation-error">{candidate.error}</p>}
      </div>;
    })}</div>
    {!terminal(job) && <button className="variation-cancel" disabled={busy} onClick={onCancel}>Cancel generation</button>}
  </div>;
}
