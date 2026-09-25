/** Shared enrichment phase helpers for narrative, interview kit, and voice strategy. */

import { countKitQuestions } from './liveScreenKitUtils'

export const ENRICHMENT_PHASES = [
  { key: 'parsing', label: 'Parsing resume' },
  { key: 'scoring', label: 'Scoring fit' },
  { key: 'narrative', label: 'AI insights' },
  { key: 'interview_kit', label: 'Interview kit' },
  { key: 'voice_strategy', label: 'Voice interview plan' },
]

export function isNarrativePending(result) {
  const s = result?.narrative_status
  return s === 'pending' || s === 'processing' || result?.narrative_pending === true
}

export function getGenerationMode(result) {
  if (!result) return 'pending'
  if (result.generation_mode) return result.generation_mode
  if (result.ai_enhanced === true) return 'ai'
  if (result.narrative_status === 'failed') return 'failed'
  if (result.narrative_status === 'fallback') return 'deterministic_fallback'
  if (result.narrative_status === 'ready') return 'deterministic_fallback'
  return 'pending'
}

export function isAiGenerated(result) {
  return getGenerationMode(result) === 'ai'
}

export function isDeterministicFallback(result) {
  return getGenerationMode(result) === 'deterministic_fallback'
}

/** True when LLM narrative body fields are present on the result object. */
export function hasNarrativeContent(result) {
  if (!result) return false
  return Boolean(
    (result.fit_summary && String(result.fit_summary).trim()) ||
    (Array.isArray(result.strengths) && result.strengths.length > 0) ||
    (Array.isArray(result.concerns) && result.concerns.length > 0) ||
    (Array.isArray(result.weaknesses) && result.weaknesses.length > 0) ||
    (result.recommendation_rationale && String(result.recommendation_rationale).trim()) ||
    (result.explainability && Object.keys(result.explainability).length > 0) ||
    (result.candidate_profile_summary && String(result.candidate_profile_summary).trim()),
  )
}

/** Poll or refetch when narrative is in-flight or marked ready but not yet hydrated in UI state. */
export function needsNarrativeHydration(result) {
  if (!result) return false
  const status = result.narrative_status || 'pending'
  if (status === 'pending' || status === 'processing') return true
  if ((status === 'ready' || status === 'fallback') && !hasNarrativeContent(result)) return true
  return false
}

export function isKitPending(result) {
  const s = result?.interview_kit_status
  return s === 'pending' || s === 'processing'
}

/** True when kit is ready to show (not generating). */
export function isInterviewKitReady(kit, status) {
  if (status === 'processing' || status === 'pending') return false
  if (!kit) return false
  if (kit.kit_status === 'pending') return false
  const hasThreads = kit.kit_version >= 2 &&
    Array.isArray(kit.threads) && kit.threads.length > 0
  const hasLegacy = ['technical_questions', 'experience_deep_dive_questions', 'behavioral_questions'].some(
    (k) => Array.isArray(kit[k]) && kit[k].length > 0,
  )
  return hasThreads || hasLegacy
}

/** True when kit status says done but question arrays are missing or empty in UI state. */
export function needsKitHydration(result) {
  if (!result) return false
  const status = result.interview_kit_status
  if (status !== 'ready' && status !== 'fallback') return false
  return countKitQuestions(result.interview_questions) === 0
}

/** Refetch report when narrative body or interview kit questions are not hydrated. */
export function needsEnrichmentRefetch(result) {
  return needsNarrativeHydration(result) || needsKitHydration(result)
}

export function isReportCacheable(result) {
  if (!result) return false
  if (needsNarrativeHydration(result)) return false
  const kitStatus = result.interview_kit_status
  if (kitStatus === 'pending' || kitStatus === 'processing') return false
  if ((kitStatus === 'ready' || kitStatus === 'fallback') && needsKitHydration(result)) return false
  return true
}

export function isVoiceStrategyPending(result) {
  const s = result?.voice_strategy_status
  return s === 'pending' || s === 'processing'
}

export function isVoiceStrategyReady(result) {
  return result?.voice_strategy_status === 'ready'
}

export function isVoiceStrategySkipped(result) {
  return result?.voice_strategy_status === 'skipped'
}

export function getEnrichmentPhaseStatus(result) {
  const narrative = result?.narrative_status || 'pending'
  const kit = result?.interview_kit_status || 'pending'
  const voice = result?.voice_strategy_status || 'pending'

  return {
    narrative: normalizeStatus(narrative),
    interview_kit: normalizeKitStatus(kit, narrative),
    voice_strategy: normalizeVoiceStatus(voice, narrative),
  }
}

function normalizeStatus(status) {
  if (status === 'ready') return 'complete'
  if (status === 'fallback' || status === 'failed') return 'fallback'
  if (status === 'processing') return 'active'
  return 'pending'
}

function normalizeKitStatus(kit, narrative) {
  if (kit === 'ready') return 'complete'
  if (kit === 'fallback') return 'fallback'
  if (kit === 'skipped') return 'skipped'
  if (kit === 'processing') return 'active'
  if (narrative === 'ready' || narrative === 'fallback') return 'pending'
  return 'waiting'
}

function normalizeVoiceStatus(voice, narrative) {
  if (voice === 'ready') return 'complete'
  if (voice === 'skipped') return 'skipped'
  if (voice === 'fallback' || voice === 'failed') return 'fallback'
  if (voice === 'processing') return 'active'
  if (narrative === 'ready' || narrative === 'fallback') return 'pending'
  return 'waiting'
}

function resolveInterviewQuestions(narrative, prev) {
  const fromNarrative = narrative?.interview_questions
  if (fromNarrative && countKitQuestions(fromNarrative) > 0) {
    return fromNarrative
  }
  if (prev?.interview_questions && countKitQuestions(prev.interview_questions) > 0) {
    return prev.interview_questions
  }
  return fromNarrative ?? prev?.interview_questions
}

export function mergeNarrativePollResult(prev, data) {
  const kit = data.interview_kit_status
  const voice = data.voice_strategy_status
  const narrative = data.narrative || {}
  const narrativeDone = data.status === 'ready' || data.status === 'fallback' || data.status === 'failed'
  const interviewQuestions = resolveInterviewQuestions(narrative, prev)

  return {
    ...narrative,
    ...(interviewQuestions != null ? { interview_questions: interviewQuestions } : {}),
    narrative_status: data.status,
    narrative_pending: !narrativeDone,
    interview_kit_status: kit ?? prev?.interview_kit_status,
    interview_kit_error: data.interview_kit_error ?? prev?.interview_kit_error,
    voice_strategy_status: voice ?? prev?.voice_strategy_status,
    narrative_error: data.error ?? prev?.narrative_error,
    generation_mode: data.generation_mode ?? prev?.generation_mode ?? (
      data.status === 'ready' && narrative.ai_enhanced === true ? 'ai' :
      data.status === 'failed' ? 'failed' :
      narrativeDone ? 'deterministic_fallback' :
      'pending'
    ),
    ai_enhanced: (data.generation_mode ?? prev?.generation_mode) === 'ai' || narrative.ai_enhanced === true,
    call_fit_score: data.call_fit_score ?? prev?.call_fit_score,
    call_source: data.call_source ?? prev?.call_source,
    consolidated_recommendation: data.consolidated_recommendation ?? prev?.consolidated_recommendation,
    consolidated_reasoning: data.consolidated_reasoning ?? prev?.consolidated_reasoning,
  }
}

export function shouldContinueNarrativePoll(data) {
  const narrativeDone = data.status === 'ready' || data.status === 'fallback' || data.status === 'failed'
  const kitStatus = data.interview_kit_status
  const kitDone =
    !kitStatus ||
    kitStatus === 'ready' ||
    kitStatus === 'fallback' ||
    kitStatus === 'skipped'
  const kitHasQuestions =
    kitStatus === 'skipped' ||
    countKitQuestions(data.narrative?.interview_questions) > 0
  return !(narrativeDone && kitDone && kitHasQuestions)
}
