import { Loader2, Sparkles, MessageSquare, Mic } from 'lucide-react'
import { getGenerationMode } from '../lib/enrichmentUtils'

export function FitBadge({ score }) {
  if (score == null)
    return <span className="px-2.5 py-0.5 rounded-full text-xs font-bold ring-1 bg-slate-50 text-slate-500 ring-slate-200">—</span>
  let color = 'bg-red-50 text-red-700 ring-red-200'
  if (score >= 72) color = 'bg-green-50 text-green-700 ring-green-200'
  else if (score >= 45) color = 'bg-amber-50 text-amber-700 ring-amber-200'
  return <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ring-1 ${color}`}>{score}</span>
}

export function RecommendBadge({ rec }) {
  const styles = {
    Shortlist: 'bg-green-50 text-green-700 ring-green-200',
    Consider:  'bg-amber-50 text-amber-700 ring-amber-200',
    Reject:    'bg-red-50 text-red-700 ring-red-200',
    Pending:   'bg-slate-50 text-slate-600 ring-slate-200',
  }
  return <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ring-1 ${styles[rec] || 'bg-slate-50 text-slate-600 ring-slate-200'}`}>{rec || '—'}</span>
}

export function NarrativeStatusBadge({ result }) {
  const status = result?.narrative_status
  const generationMode = getGenerationMode(result)
  const isPending = status === 'pending' || status === 'processing' || result?.narrative_pending === true

  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-brand-600 bg-brand-50 px-1.5 py-0.5 rounded-md ring-1 ring-brand-200">
        <Loader2 className="w-3 h-3 animate-spin" />
        AI…
      </span>
    )
  }

  if (status === 'ready' && generationMode === 'ai') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-green-700 bg-green-50 px-1.5 py-0.5 rounded-md ring-1 ring-green-200" title="AI-generated narrative">
        <Sparkles className="w-3 h-3" />
        AI
      </span>
    )
  }

  if (status === 'failed' || generationMode === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded-md ring-1 ring-amber-200" title="Standard analysis (AI enhancement unavailable)">
        Std
      </span>
    )
  }

  if (status === 'fallback' || generationMode === 'deterministic_fallback') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded-md ring-1 ring-amber-200" title="Standard deterministic analysis">
        Std
      </span>
    )
  }

  return null
}

export function KitStatusBadge({ result }) {
  const status = result?.interview_kit_status
  if (!status || status === 'skipped') return null
  if (status === 'pending' || status === 'processing') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded-md ring-1 ring-indigo-200" title="Interview kit generating">
        <Loader2 className="w-3 h-3 animate-spin" />
        Kit
      </span>
    )
  }
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-indigo-700 bg-indigo-50 px-1.5 py-0.5 rounded-md ring-1 ring-indigo-200" title="Interview kit ready">
        <MessageSquare className="w-3 h-3" />
        Kit
      </span>
    )
  }
  return null
}

export function VoiceStrategyBadge({ result }) {
  const status = result?.voice_strategy_status
  if (!status || status === 'skipped') return null
  if (status === 'pending' || status === 'processing') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-violet-600 bg-violet-50 px-1.5 py-0.5 rounded-md ring-1 ring-violet-200">
        <Loader2 className="w-3 h-3 animate-spin" />
        Voice
      </span>
    )
  }
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-violet-700 bg-violet-50 px-1.5 py-0.5 rounded-md ring-1 ring-violet-200" title="Voice interview plan ready">
        <Mic className="w-3 h-3" />
        Voice
      </span>
    )
  }
  return null
}

export function EnrichmentStatusBadges({ result }) {
  return (
    <span className="inline-flex items-center gap-1 flex-wrap">
      <NarrativeStatusBadge result={result} />
      <KitStatusBadge result={result} />
      <VoiceStrategyBadge result={result} />
    </span>
  )
}
