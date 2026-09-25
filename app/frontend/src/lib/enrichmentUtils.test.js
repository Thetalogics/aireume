import { describe, it, expect } from 'vitest'
import {
  isNarrativePending,
  isKitPending,
  isVoiceStrategyReady,
  mergeNarrativePollResult,
  shouldContinueNarrativePoll,
  getEnrichmentPhaseStatus,
  hasNarrativeContent,
  needsNarrativeHydration,
  needsKitHydration,
  needsEnrichmentRefetch,
  isReportCacheable,
  isInterviewKitReady,
  getGenerationMode,
  isAiGenerated,
  isDeterministicFallback,
} from './enrichmentUtils'

describe('enrichmentUtils', () => {
  it('detects narrative pending states', () => {
    expect(isNarrativePending({ narrative_status: 'pending' })).toBe(true)
    expect(isNarrativePending({ narrative_status: 'processing' })).toBe(true)
    expect(isNarrativePending({ narrative_status: 'ready' })).toBe(false)
  })

  it('detects kit pending states', () => {
    expect(isKitPending({ interview_kit_status: 'processing' })).toBe(true)
    expect(isKitPending({ interview_kit_status: 'ready' })).toBe(false)
  })

  it('maps kit fallback to fallback phase (not complete)', () => {
    const phases = getEnrichmentPhaseStatus({
      narrative_status: 'ready',
      interview_kit_status: 'fallback',
    })
    expect(phases.interview_kit).toBe('fallback')
    expect(phases.narrative).toBe('complete')
  })

  it('maps kit ready to complete', () => {
    const phases = getEnrichmentPhaseStatus({
      narrative_status: 'ready',
      interview_kit_status: 'ready',
    })
    expect(phases.interview_kit).toBe('complete')
  })

  it('merges poll result into previous state', () => {
    const prev = { fit_score: 80, interview_kit_status: 'pending' }
    const merged = mergeNarrativePollResult(prev, {
      status: 'ready',
      generation_mode: 'ai',
      interview_kit_status: 'processing',
      voice_strategy_status: 'pending',
      narrative: { strengths: ['Leadership'] },
    })
    expect(merged.narrative_status).toBe('ready')
    expect(merged.generation_mode).toBe('ai')
    expect(merged.ai_enhanced).toBe(true)
    expect(merged.strengths).toEqual(['Leadership'])
    expect(merged.narrative_pending).toBe(false)
    expect(merged.interview_kit_status).toBe('processing')
  })

  it('uses generation_mode as the source of truth for AI/fallback labels', () => {
    expect(getGenerationMode({ generation_mode: 'ai', ai_enhanced: false })).toBe('ai')
    expect(isAiGenerated({ generation_mode: 'ai' })).toBe(true)
    expect(isDeterministicFallback({ generation_mode: 'deterministic_fallback' })).toBe(true)
    expect(getGenerationMode({ narrative_status: 'fallback' })).toBe('deterministic_fallback')
  })

  it('detects missing narrative hydration when status is ready but body empty', () => {
    expect(needsNarrativeHydration({ narrative_status: 'ready', strengths: [] })).toBe(true)
    expect(needsNarrativeHydration({
      narrative_status: 'ready',
      fit_summary: 'Strong FP&A background.',
    })).toBe(false)
    expect(hasNarrativeContent({ strengths: ['Excel'] })).toBe(true)
  })

  it('continues polling while kit is pending', () => {
    expect(shouldContinueNarrativePoll({
      status: 'ready',
      interview_kit_status: 'processing',
    })).toBe(true)
  })

  it('stops polling when narrative and kit are both ready with questions', () => {
    expect(shouldContinueNarrativePoll({
      status: 'ready',
      interview_kit_status: 'ready',
      narrative: {
        interview_questions: { technical_questions: [{ text: 'Q1' }] },
      },
    })).toBe(false)
  })

  it('continues polling when kit status is ready but questions are empty', () => {
    expect(shouldContinueNarrativePoll({
      status: 'ready',
      interview_kit_status: 'ready',
      narrative: { interview_questions: { technical_questions: [] } },
    })).toBe(true)
  })

  it('detects kit hydration gap when status is ready but questions missing', () => {
    expect(needsKitHydration({ interview_kit_status: 'ready' })).toBe(true)
    expect(needsKitHydration({
      interview_kit_status: 'ready',
      interview_questions: { technical_questions: [{ text: 'Q1' }] },
    })).toBe(false)
    expect(needsEnrichmentRefetch({
      narrative_status: 'ready',
      fit_summary: 'Done',
      interview_kit_status: 'ready',
    })).toBe(true)
  })

  it('preserves interview questions when poll narrative omits them', () => {
    const prev = {
      interview_questions: {
        technical_questions: [{ text: 'Stored Q' }],
      },
    }
    const merged = mergeNarrativePollResult(prev, {
      status: 'ready',
      interview_kit_status: 'ready',
      narrative: { strengths: ['Excel'] },
    })
    expect(merged.interview_questions.technical_questions[0].text).toBe('Stored Q')
    expect(merged.strengths).toEqual(['Excel'])
  })

  it('does not cache report until kit questions exist', () => {
    expect(isReportCacheable({
      narrative_status: 'ready',
      fit_summary: 'Summary',
      interview_kit_status: 'ready',
    })).toBe(false)
    expect(isReportCacheable({
      narrative_status: 'ready',
      fit_summary: 'Summary',
      interview_kit_status: 'ready',
      interview_questions: { technical_questions: [{ text: 'Q' }] },
    })).toBe(true)
  })

  it('maps enrichment phases from result', () => {
    const phases = getEnrichmentPhaseStatus({
      narrative_status: 'ready',
      interview_kit_status: 'processing',
      voice_strategy_status: 'pending',
    })
    expect(phases.narrative).toBe('complete')
    expect(phases.interview_kit).toBe('active')
    expect(phases.voice_strategy).toBe('pending')
  })

  it('isInterviewKitReady false while processing', () => {
    expect(isInterviewKitReady({ threads: [] }, 'processing')).toBe(false)
  })

  it('isInterviewKitReady true for v3 playbook kit', () => {
    expect(isInterviewKitReady({
      kit_version: 3,
      threads: [{ steps: [{ spoken_text: 'At Acme — what did you own?' }] }],
    }, 'ready')).toBe(true)
  })
})
