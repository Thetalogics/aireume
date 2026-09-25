import { Sparkles, Loader2, AlertTriangle, CheckCircle2, X } from 'lucide-react'
import {
  getGenerationMode,
  isKitPending,
  isNarrativePending,
  isVoiceStrategyPending,
} from '../../lib/enrichmentUtils'

export default function EnrichmentBanner({ result, onDismiss }) {
  if (!result) return null

  const narrativePending = isNarrativePending(result)
  const kitPending = isKitPending(result)
  const voicePending = isVoiceStrategyPending(result)
  const generationMode = getGenerationMode(result)
  const narrativeFallback = (
    generationMode === 'deterministic_fallback' ||
    generationMode === 'failed' ||
    result.narrative_status === 'fallback' ||
    result.narrative_status === 'failed'
  )
  const hasInsights = Boolean(result.fit_summary || (result.strengths && result.strengths.length))
  const allDone = !narrativePending && !kitPending && !voicePending

  if (allDone && (!narrativeFallback || hasInsights)) return null

  let icon = Sparkles
  let tone = 'brand'
  let title = 'Enhancing your report'
  let message = 'Scores are ready. AI insights and interview materials are generating in the background.'

  if (narrativePending) {
    icon = Loader2
    message = 'Generating AI insights — typically ready in under 30 seconds.'
  } else if (kitPending) {
    icon = Loader2
    title = 'Preparing interview kit'
    message = 'AI insights are ready. Screen questions are being prepared (~15–20 seconds).'
  } else if (voicePending) {
    icon = Loader2
    title = 'Building voice interview plan'
    message = 'Interview questions ready. Preparing an instant-start voice interview plan.'
  } else if (narrativeFallback) {
    icon = AlertTriangle
    tone = 'amber'
    title = 'Standard analysis shown'
    message =
      result.narrative_error ||
      'Using deterministic standard analysis. Fit scores and skill matching remain accurate.'
  } else if (allDone) {
    icon = CheckCircle2
    tone = 'emerald'
    title = 'Report complete'
    message = 'All AI enrichment phases are ready.'
  }

  const Icon = icon
  const spin = icon === Loader2

  const toneClasses = {
    brand: 'from-brand-50 to-indigo-50 border-brand-200 text-brand-900',
    amber: 'from-amber-50 to-orange-50 border-amber-200 text-amber-900',
    emerald: 'from-emerald-50 to-teal-50 border-emerald-200 text-emerald-900',
  }

  return (
    <div
      className={`relative mb-4 rounded-2xl border bg-gradient-to-r px-4 py-3 ${toneClasses[tone]}`}
      aria-live="polite"
    >
      <div className="flex items-start gap-3 pr-8">
        <Icon className={`w-5 h-5 shrink-0 mt-0.5 ${spin ? 'animate-spin text-brand-600' : ''}`} />
        <div className="min-w-0">
          <p className="text-sm font-bold">{title}</p>
          <p className="text-xs mt-0.5 opacity-80">{message}</p>
        </div>
      </div>
      {onDismiss && allDone && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="absolute top-3 right-3 p-1 rounded-lg hover:bg-black/5 transition-colors"
        >
          <X className="w-4 h-4 opacity-60" />
        </button>
      )}
    </div>
  )
}
