import {
  ThumbsUp, ThumbsDown, AlertTriangle,
  CheckCircle, XCircle, Target, Shield, ClipboardList,
  Mail, Loader2, Lightbulb, BookOpen, Cpu, UserCheck, Star,
} from 'lucide-react'
import { useState, useEffect, useRef, memo } from 'react'
import StreamingText from './StreamingText'
import { getNarrative, recordOutcome, recordOutcomeFeedback } from '../lib/api'
import {
  getGenerationMode,
  hasNarrativeContent,
  isDeterministicFallback,
  isNarrativePending,
  needsNarrativeHydration,
} from '../lib/enrichmentUtils'
import { safeStr } from '../lib/utils'
import { usePlanFeature, useHasSubscriptionContext } from '../hooks/useSubscription'
import { PlanLockedButton } from './PlanLockedInline'
import {
  RiskBadge, CollapsibleSection, EmailModal,
  ScoreBreakdownPanel, AnalysisSourceBadge, PendingBanner,
} from './ResultCardParts'
import ResultCardSkillsIntel from './ResultCardSkillsIntel'

// ─── Main ResultCard ──────────────────────────────────────────────────────────

export default memo(function ResultCard({ result, defaultExpandEducation = false, skipNarrativePolling = false }) {
  const inSubscriptionContext = useHasSubscriptionContext()
  const canEmail = usePlanFeature('email_generation', true)
  const [showEmailModal, setShowEmailModal] = useState(false)

  // Outcome feedback state
  const [outcomeStatus, setOutcomeStatus]       = useState(null) // null | 'hired' | 'rejected' | 'withdrawn'
  const [outcomeId, setOutcomeId]               = useState(null)
  const [showStageSelect, setShowStageSelect]   = useState(false)
  const [selectedStage, setSelectedStage]       = useState('')
  const [outcomeNotes, setOutcomeNotes]         = useState('')
  const [savingOutcome, setSavingOutcome]       = useState(false)
  const [showFeedback, setShowFeedback]         = useState(false)
  const [feedbackRating, setFeedbackRating]     = useState(0)
  const [feedbackNotes, setFeedbackNotes]       = useState('')
  const [savingFeedback, setSavingFeedback]     = useState(false)
  const [outcomeError, setOutcomeError]         = useState(null)

  // Outcome handler
  const handleOutcome = (decision) => {
    setOutcomeStatus(decision)
    setShowStageSelect(true)
    setOutcomeError(null)
  }

  const handleConfirmOutcome = async () => {
    if (!candidate_id) return
    setSavingOutcome(true)
    setOutcomeError(null)
    try {
      const data = {
        screening_result_id: result_id,
        decision: outcomeStatus,
      }
      if (selectedStage) data.stage = selectedStage
      if (outcomeNotes.trim()) data.notes = outcomeNotes.trim()
      const result = await recordOutcome(candidate_id, data)
      setOutcomeId(result.outcome_id || result.id)
      setShowStageSelect(false)
      // Show feedback for hired candidates
      if (outcomeStatus === 'hired') {
        setShowFeedback(true)
      }
    } catch (err) {
      setOutcomeError(err.response?.data?.detail || 'Failed to record outcome')
    } finally {
      setSavingOutcome(false)
    }
  }

  const handleSubmitFeedback = async () => {
    if (!outcomeId || feedbackRating === 0) return
    setSavingFeedback(true)
    try {
      await recordOutcomeFeedback(outcomeId, {
        rating: feedbackRating,
        notes: feedbackNotes.trim() || undefined,
      })
      setShowFeedback(false)
    } catch (err) {
      console.error('Failed to submit feedback:', err)
    } finally {
      setSavingFeedback(false)
    }
  }
  
  // Narrative polling state
  const [narrativeData, setNarrativeData]       = useState(null)
  const [narrativeError, setNarrativeError]     = useState(null)
  const [isPolling, setIsPolling]               = useState(false)
  const pollAttemptRef                          = useRef(0)
  const pollingTimeoutRef                       = useRef(null)

  const {
    fit_score, strengths, weaknesses, education_analysis,
    risk_signals, final_recommendation, score_breakdown,
    matched_skills, missing_skills, risk_level,
    result_id, candidate_id,
    explainability, adjacent_skills,
    skill_analysis, edu_timeline_analysis, jd_analysis,
    recommendation_rationale,
    narrative_pending, analysis_quality,
    fit_summary, concerns, score_rationales, risk_summary, skill_depth,
    analysis_id, onet_hot_skills,
  } = result

  // Defensive fallback: use result_id if analysis_id is not available (for backward compatibility)
  const effectiveAnalysisId = analysis_id || result_id

  // Backward compatibility: use concerns if available, otherwise fall back to weaknesses
  const concernsList = concerns || weaknesses || []

  const isPending = final_recommendation === 'Pending' || fit_score === null || fit_score === undefined
  
  // Determine if narrative is ready (either from polling or already in result)
  const narrativeReady = narrativeData !== null || hasNarrativeContent(result)

  // Narrative-only enhancing state — do not conflate with interview kit / voice plan polling
  const isNarrativeEnhancing = skipNarrativePolling
    ? false
    : !narrativeReady && (isPolling || isNarrativePending(result) || needsNarrativeHydration(result))
  
  // Check if narrative is AI-enhanced (real LLM response vs fallback)
  // narrativeData comes from polling, result.narrative_json would be from initial result
  const generationMode = getGenerationMode({
    ...result,
    ...(narrativeData || {}),
    generation_mode: narrativeData?.generation_mode ?? result?.generation_mode,
  })
  const aiEnhanced = generationMode === 'ai'

  const mergedFitSummary = narrativeData?.fit_summary || fit_summary || ''
  const mergedStrengths = narrativeData?.strengths || strengths || []
  const mergedConcerns = narrativeData?.concerns || narrativeData?.weaknesses || concerns || weaknesses || []
  const mergedRecommendationRationale = narrativeData?.recommendation_rationale || recommendation_rationale || ''
  const mergedExplainability = narrativeData?.explainability || explainability || {}
  const mergedCandidateSummary = narrativeData?.candidate_profile_summary || result?.candidate_profile_summary || ''

  // Seed local narrative state when parent already merged LLM fields into result
  useEffect(() => {
    if (narrativeData || !hasNarrativeContent(result)) return
    setNarrativeData({
      ai_enhanced: result.ai_enhanced,
      fit_summary: result.fit_summary,
      strengths: result.strengths,
      concerns: result.concerns,
      weaknesses: result.weaknesses,
      recommendation_rationale: result.recommendation_rationale,
      explainability: result.explainability,
      candidate_profile_summary: result.candidate_profile_summary,
      generation_mode: result.generation_mode,
    })
  }, [result, narrativeData])

  // Narrative polling effect with adaptive timing (skip when parent owns polling)
  useEffect(() => {
    if (skipNarrativePolling || !effectiveAnalysisId || !needsNarrativeHydration(result)) {
      setIsPolling(false)
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current)
        pollingTimeoutRef.current = null
      }
      return
    }
    
    setIsPolling(true)
    setNarrativeError(null)
    pollAttemptRef.current = 0
    
    const MAX_ATTEMPTS = 60 // ~3 min: 15*2s + 45*5s
    
    const getPollDelay = (attempt) => {
      // First 15 attempts: 2s interval (covers first 30s for cloud models)
      // After 15 attempts: 5s interval (for slower local models)
      return attempt < 15 ? 2000 : 5000
    }
    
    const stopPolling = () => {
      setIsPolling(false)
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current)
        pollingTimeoutRef.current = null
      }
    }
    
    const scheduleNextPoll = () => {
      const delay = getPollDelay(pollAttemptRef.current)
      pollingTimeoutRef.current = setTimeout(poll, delay)
    }
    
    const poll = async () => {
      try {
        const response = await getNarrative(effectiveAnalysisId)
        
        if (response.status === 'ready' && response.narrative) {
          setNarrativeData({
            ...response.narrative,
            generation_mode: response.generation_mode,
          })
          stopPolling()
        } else if (response.status === 'fallback' || response.status === 'failed') {
          setNarrativeData({
            ...(response.narrative || {}),
            generation_mode: response.generation_mode,
          })
          if (response.status === 'failed' && !(response.narrative?.fit_summary || response.narrative?.strengths?.length)) {
            setNarrativeError(response.error || 'AI enhancement unavailable')
          }
          stopPolling()
        } else if (response.narrative) {
          setNarrativeData({
            ...response.narrative,
            generation_mode: response.generation_mode,
          })
          if (response.status === 'ready' || response.status === 'fallback' || response.status === 'failed') {
            stopPolling()
          } else {
            pollAttemptRef.current += 1
            if (pollAttemptRef.current >= MAX_ATTEMPTS) {
              stopPolling()
            } else {
              scheduleNextPoll()
            }
          }
        } else {
          pollAttemptRef.current += 1
          if (pollAttemptRef.current >= MAX_ATTEMPTS) {
            stopPolling()
          } else {
            scheduleNextPoll()
          }
        }
      } catch (err) {
        // On error, continue polling until max attempts
        console.debug('Narrative polling error:', err)
        pollAttemptRef.current += 1
        
        if (pollAttemptRef.current >= MAX_ATTEMPTS) {
          stopPolling()
        } else {
          scheduleNextPoll()
        }
      }
    }
    
    // Poll immediately, then schedule next with adaptive delay
    poll()
    
    // Cleanup on unmount
    return () => {
      if (pollingTimeoutRef.current) {
        clearTimeout(pollingTimeoutRef.current)
        pollingTimeoutRef.current = null
      }
    }
  }, [result, effectiveAnalysisId, skipNarrativePolling])

  let badgeColor  = 'bg-amber-100 text-amber-800 ring-amber-200'
  let BadgeIcon   = Target
  if (final_recommendation === 'Shortlist') {
    badgeColor = 'bg-green-100 text-green-800 ring-green-200'
    BadgeIcon  = CheckCircle
  } else if (final_recommendation === 'Reject') {
    badgeColor = 'bg-red-100 text-red-800 ring-red-200'
    BadgeIcon  = XCircle
  } else if (isPending) {
    badgeColor = 'bg-slate-100 text-slate-600 ring-slate-200'
    BadgeIcon  = AlertTriangle
  }

  // Merge narrative data with existing result data (merged* vars computed above)

  return (
    <>
      <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6 md:p-8 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-2xl font-bold text-brand-900 tracking-tight">Analysis Results</h2>
          <div className="flex items-center gap-2 flex-wrap">
            {risk_level && !isPending && <RiskBadge level={risk_level} />}
            <span className={`flex items-center gap-1.5 px-4 py-1.5 rounded-full text-sm font-bold ring-1 ${badgeColor}`}>
              <BadgeIcon className="w-4 h-4" />
              {safeStr(final_recommendation)}
            </span>
            {canEmail ? (
            <button
              onClick={() => setShowEmailModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl ring-1 ring-brand-200 text-sm text-brand-700 hover:bg-brand-50 transition-colors font-semibold"
              title="Generate email"
            >
              <Mail className="w-4 h-4" />
              <span className="hidden sm:inline">Email</span>
            </button>
            ) : inSubscriptionContext ? (
              <PlanLockedButton feature="email_generation" className="!px-3 !py-1.5 !text-sm">Email</PlanLockedButton>
            ) : null}
          </div>
        </div>

        {/* Analysis source badge — shows polling state or AI enhanced status */}
        {!isPending && (
          <AnalysisSourceBadge
            narrativeReady={narrativeReady}
            isPolling={isNarrativeEnhancing}
            analysisQuality={analysis_quality}
            aiEnhanced={aiEnhanced}
            generationMode={generationMode}
          />
        )}

        {/* Fallback indicator — shown when narrative was generated from template */}
        {(narrativeData?.narrative_fallback || result?.narrative_fallback) && (
          <div className="text-xs text-slate-500 italic mb-2">
            Automated summary — AI narrative was unavailable
          </div>
        )}

        {/* Narrative error banner — shown when AI enhancement failed */}
        {narrativeError && (
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
            <svg className="h-4 w-4 flex-shrink-0 text-amber-500" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.168 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
            </svg>
            <span>AI enhancement unavailable: {narrativeError}. Showing standard analysis.</span>
          </div>
        )}

        {/* Standard mode info banner — shown when ai_enhanced is false without error */}
        {narrativeData && isDeterministicFallback({ generation_mode: generationMode }) && !narrativeError && (
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
            <svg className="h-4 w-4 flex-shrink-0 text-amber-500" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clipRule="evenodd" />
            </svg>
            <span>Using deterministic standard analysis. Fit scores and skill matching remain authoritative.</span>
          </div>
        )}

        {/* Pending banner */}
        {isPending && <PendingBanner />}

        {/* Fit Summary Banner */}
        {mergedFitSummary.trim() && (
          <div className="bg-gradient-to-r from-indigo-500 to-blue-600 rounded-2xl p-5 text-white shadow-lg">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-xl bg-white/20 flex items-center justify-center shrink-0">
                <UserCheck className="w-4 h-4 text-white" />
              </div>
              <div>
                <h3 className="text-sm font-bold uppercase tracking-wide text-indigo-100 mb-1">Executive Summary</h3>
                <p className="text-sm leading-relaxed text-white/95">
                  <StreamingText text={safeStr(mergedFitSummary)} isStreaming={isNarrativeEnhancing} />
                </p>
              </div>
            </div>
          </div>
        )}

        {mergedCandidateSummary.trim() && (
          <div className="bg-brand-50 rounded-2xl p-4 ring-1 ring-brand-100">
            <h3 className="text-xs font-bold uppercase tracking-wide text-brand-700 mb-2">Candidate Profile</h3>
            <p className="text-sm text-slate-700 leading-relaxed">{safeStr(mergedCandidateSummary)}</p>
          </div>
        )}

        {/* Score Breakdown */}
        {score_breakdown && Object.keys(score_breakdown).length > 0 && !isPending && (
          <>
            <ScoreBreakdownPanel scoreBreakdown={score_breakdown} recommendationRationale={mergedRecommendationRationale} riskSummary={risk_summary} />
            <p className="text-xs text-slate-500 dark:text-slate-400 px-1">
              Fit scores can differ across workspaces for the same resume because each tenant can calibrate weights, must-haves, and historical hire patterns.
            </p>
          </>
        )}

        <ResultCardSkillsIntel
          skill_analysis={skill_analysis}
          score_breakdown={score_breakdown}
          result={result}
          matched_skills={matched_skills}
          missing_skills={missing_skills}
          skill_depth={skill_depth}
          adjacent_skills={adjacent_skills}
          risk_summary={risk_summary}
        />

        {/* Strengths / Concerns / Risks */}
        <div className="grid md:grid-cols-3 gap-4">
          <div className="bg-green-50 rounded-2xl p-4 ring-1 ring-green-100 border-l-4 border-green-500">
            <div className="flex items-center gap-2 mb-3">
              <ThumbsUp className="w-4 h-4 text-green-600" />
              <h3 className="font-bold text-green-800 text-sm">
                Strengths
                {narrativeData?.strengths && aiEnhanced === true && (
                  <span className="ml-2 text-[10px] font-semibold text-green-600 bg-green-100 px-1.5 py-0.5 rounded-full">AI Enhanced</span>
                )}
              </h3>
            </div>
            <ul className="space-y-1.5">
              {mergedStrengths.length > 0 ? (
                mergedStrengths.slice(0, 5).map((s, i) => (
                  <li key={i} className="text-sm text-green-700 flex items-start gap-2">
                    <span className="text-green-500 mt-1 shrink-0">•</span>{safeStr(s)}
                  </li>
                ))
              ) : <li className="text-sm text-green-600 italic">No specific strengths identified</li>}
            </ul>
          </div>

          <div className="bg-red-50 rounded-2xl p-4 ring-1 ring-red-100 border-l-4 border-red-400">
            <div className="flex items-center gap-2 mb-3">
              <ThumbsDown className="w-4 h-4 text-red-600" />
              <h3 className="font-bold text-red-800 text-sm">
                Concerns
                {(narrativeData?.concerns || narrativeData?.weaknesses) && aiEnhanced === true && (
                  <span className="ml-2 text-[10px] font-semibold text-red-600 bg-red-100 px-1.5 py-0.5 rounded-full">AI Enhanced</span>
                )}
              </h3>
            </div>
            <ul className="space-y-1.5">
              {mergedConcerns.length > 0 ? (
                mergedConcerns.slice(0, 5).map((w, i) => (
                  <li key={i} className="text-sm text-red-700 flex items-start gap-2">
                    <span className="text-red-500 mt-1 shrink-0">•</span>{safeStr(w)}
                  </li>
                ))
              ) : <li className="text-sm text-red-600 italic">No significant concerns</li>}
            </ul>
          </div>

          <div className="bg-amber-50 rounded-2xl p-4 ring-1 ring-amber-100 border-l-4 border-amber-400">
            <div className="flex items-center gap-2 mb-3">
              <Shield className="w-4 h-4 text-amber-600" />
              <h3 className="font-bold text-amber-800 text-sm">Risk Signals</h3>
            </div>
            <ul className="space-y-1.5">
              {risk_signals?.length > 0 ? (
                risk_signals.map((risk, i) => (
                  <li key={i} className="text-sm text-amber-700 flex items-start gap-2">
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                    {safeStr(typeof risk === 'string' ? risk : risk?.description)}
                  </li>
                ))
              ) : <li className="text-sm text-amber-600 italic">No risk signals detected</li>}
            </ul>
          </div>
        </div>

        {/* Explainability - uses score_rationales as fallback when explainability is missing */}
        {(() => {
          // Determine which data source to use: prefer explainability, fall back to score_rationales
          const hasExplainability = mergedExplainability && Object.keys(mergedExplainability).length > 0
          const hasScoreRationales = score_rationales && Object.keys(score_rationales).length > 0
          
          if (!hasExplainability && !hasScoreRationales) return null
          
          // Use explainability if it has meaningful content, otherwise use score_rationales
          const source = hasExplainability ? mergedExplainability : score_rationales
          const isFallback = !hasExplainability && hasScoreRationales
          
          return (
            <CollapsibleSection
              title={isFallback ? "Score Rationales — Why this score?" : "Explainability — Why this score?"}
              icon={Lightbulb}
              iconColor="text-yellow-600"
              bgColor="bg-yellow-50"
            >
              <div className="space-y-3">
                {(source.overall_rationale || source.domain_rationale) && (
                  <div className="p-3 bg-brand-50 rounded-xl ring-1 ring-brand-100">
                    <p className="text-sm font-semibold text-brand-800 mb-1">Overall</p>
                    <p className="text-sm text-slate-600 leading-relaxed">
                      {safeStr(source.overall_rationale || source.domain_rationale)}
                    </p>
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {[
                    { key: 'skill_rationale',       label: 'Skills' },
                    { key: 'experience_rationale',   label: 'Experience' },
                    { key: 'education_rationale',    label: 'Education' },
                    { key: 'timeline_rationale',     label: 'Timeline' },
                    { key: 'domain_rationale',       label: 'Domain Fit' },
                  ].filter(f => source[f.key]).map(f => (
                    <div key={f.key} className="p-3 bg-slate-50 rounded-xl ring-1 ring-slate-100">
                      <p className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">{f.label}</p>
                      <p className="text-xs text-slate-600 leading-relaxed">{safeStr(source[f.key])}</p>
                    </div>
                  ))}
                </div>
              </div>
            </CollapsibleSection>
          )
        })()}

        {/* Education Analysis */}
        <CollapsibleSection
          title="Education Analysis"
          icon={BookOpen}
          iconColor="text-brand-600"
          bgColor="bg-brand-50"
          defaultOpen={defaultExpandEducation}
        >
          <div className="space-y-3">
            {edu_timeline_analysis?.field_alignment && (
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-slate-500 uppercase tracking-wide">Field Alignment</span>
                <span className={`px-2 py-0.5 rounded-full text-xs font-bold ring-1 ${
                  edu_timeline_analysis.field_alignment === 'aligned'
                    ? 'bg-green-100 text-green-700 ring-green-200'
                    : edu_timeline_analysis.field_alignment === 'partially_aligned'
                    ? 'bg-amber-100 text-amber-700 ring-amber-200'
                    : 'bg-red-100 text-red-700 ring-red-200'
                }`}>
                  {safeStr(edu_timeline_analysis.field_alignment).replace('_', ' ')}
                </span>
              </div>
            )}
            <p className="text-sm text-slate-600 leading-relaxed">
              {safeStr(edu_timeline_analysis?.education_analysis || education_analysis) || 'No education analysis available.'}
            </p>
            {edu_timeline_analysis?.timeline_analysis && (
              <div className="mt-2 pt-2 border-t border-brand-50">
                <p className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">Timeline</p>
                <p className="text-sm text-slate-600 leading-relaxed">{safeStr(edu_timeline_analysis.timeline_analysis)}</p>
              </div>
            )}
            {edu_timeline_analysis?.gap_interpretation && (
              <div className="mt-2 pt-2 border-t border-brand-50">
                <p className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">Gap Context</p>
                <p className="text-sm text-slate-600 leading-relaxed italic">{safeStr(edu_timeline_analysis.gap_interpretation)}</p>
              </div>
            )}
          </div>
        </CollapsibleSection>

        {/* Domain Fit & Architecture */}
        {(skill_analysis?.domain_fit_comment || skill_analysis?.architecture_comment) && (
          <CollapsibleSection
            title="Domain Fit & Architecture Assessment"
            icon={Cpu}
            iconColor="text-teal-600"
            bgColor="bg-teal-50"
          >
            <div className="space-y-3">
              {skill_analysis.domain_fit_comment && (
                <div>
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">Domain Fit</p>
                  <p className="text-sm text-slate-600 leading-relaxed">{safeStr(skill_analysis.domain_fit_comment)}</p>
                </div>
              )}
              {skill_analysis.architecture_comment && (
                <div className="pt-2 border-t border-teal-50">
                  <p className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">Architecture & System Design</p>
                  <p className="text-sm text-slate-600 leading-relaxed">{safeStr(skill_analysis.architecture_comment)}</p>
                </div>
              )}
            </div>
          </CollapsibleSection>
        )}

        {/* Outcome Feedback Section */}
        {!isPending && candidate_id && (
          <div className="ring-1 ring-brand-200 rounded-2xl bg-brand-50/40 overflow-hidden">
            <div className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <ClipboardList className="w-4 h-4 text-brand-600" />
                <span className="font-bold text-brand-800 text-sm">Hiring Decision</span>
              </div>

              {/* Outcome Status Badge — shown when outcome is recorded */}
              {outcomeStatus && !showStageSelect ? (
                <div className="flex items-center gap-3">
                  <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold ring-1 ${
                    outcomeStatus === 'hired'
                      ? 'bg-green-100 text-green-700 ring-green-200'
                      : outcomeStatus === 'rejected'
                      ? 'bg-red-100 text-red-700 ring-red-200'
                      : 'bg-slate-100 text-slate-600 ring-slate-200'
                  }`}>
                    {outcomeStatus === 'hired' && <CheckCircle className="w-3.5 h-3.5" />}
                    {outcomeStatus === 'rejected' && <XCircle className="w-3.5 h-3.5" />}
                    {outcomeStatus === 'withdrawn' && <AlertTriangle className="w-3.5 h-3.5" />}
                    {outcomeStatus.charAt(0).toUpperCase() + outcomeStatus.slice(1)}
                  </span>
                  {/* Feedback link for hired candidates */}
                  {outcomeStatus === 'hired' && outcomeId && !showFeedback && (
                    <button
                      onClick={() => setShowFeedback(true)}
                      className="text-xs font-semibold text-brand-600 hover:text-brand-800 transition-colors"
                    >
                      Add feedback
                    </button>
                  )}
                </div>
              ) : !outcomeStatus ? (
                /* Decision Buttons — shown when no outcome recorded */
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-slate-500">Decision:</span>
                  <button
                    onClick={() => handleOutcome('hired')}
                    className="px-3 py-1.5 text-xs rounded-xl font-semibold bg-green-50 text-green-700 ring-1 ring-green-200 hover:bg-green-100 transition-colors"
                  >
                    Hired
                  </button>
                  <button
                    onClick={() => handleOutcome('rejected')}
                    className="px-3 py-1.5 text-xs rounded-xl font-semibold bg-red-50 text-red-700 ring-1 ring-red-200 hover:bg-red-100 transition-colors"
                  >
                    Rejected
                  </button>
                  <button
                    onClick={() => handleOutcome('withdrawn')}
                    className="px-3 py-1.5 text-xs rounded-xl font-semibold bg-slate-50 text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100 transition-colors"
                  >
                    Withdrawn
                  </button>
                </div>
              ) : null}

              {/* Stage Selection — shown after clicking a decision button */}
              {showStageSelect && (
                <div className="mt-3 space-y-3 p-3 bg-white rounded-xl ring-1 ring-brand-100">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-500">Stage:</span>
                    <select
                      value={selectedStage}
                      onChange={(e) => setSelectedStage(e.target.value)}
                      className="text-xs bg-slate-50 border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-300"
                    >
                      <option value="">Select stage (optional)</option>
                      <option value="screening">Screening</option>
                      <option value="phone_screen">Phone Screen</option>
                      <option value="interview">Interview</option>
                      <option value="offer">Offer</option>
                      <option value="onboarded">Onboarded</option>
                    </select>
                  </div>
                  <textarea
                    placeholder="Optional notes about this decision..."
                    value={outcomeNotes}
                    onChange={(e) => setOutcomeNotes(e.target.value)}
                    rows={2}
                    className="w-full text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-brand-300 placeholder:text-slate-300"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleConfirmOutcome}
                      disabled={savingOutcome}
                      className="px-4 py-1.5 text-xs font-bold bg-brand-600 text-white rounded-xl hover:bg-brand-700 disabled:opacity-60 transition-colors flex items-center gap-1.5"
                    >
                      {savingOutcome && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      Confirm
                    </button>
                    <button
                      onClick={() => { setShowStageSelect(false); setOutcomeStatus(null) }}
                      className="px-3 py-1.5 text-xs font-semibold text-slate-500 hover:text-slate-700 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* Feedback Rating — shown after marking as "hired" */}
              {showFeedback && outcomeId && (
                <div className="mt-3 p-3 bg-white rounded-xl ring-1 ring-brand-100 space-y-3">
                  <div>
                    <span className="text-xs font-semibold text-slate-500">Rate this hire:</span>
                    <div className="flex items-center gap-1 mt-1.5">
                      {[1, 2, 3, 4, 5].map((star) => (
                        <button
                          key={star}
                          onClick={() => setFeedbackRating(star)}
                          className="transition-colors"
                        >
                          <Star
                            className={`w-5 h-5 ${
                              star <= feedbackRating
                                ? 'text-amber-400 fill-amber-400'
                                : 'text-slate-300'
                            }`}
                          />
                        </button>
                      ))}
                    </div>
                  </div>
                  <textarea
                    placeholder="Optional feedback notes..."
                    value={feedbackNotes}
                    onChange={(e) => setFeedbackNotes(e.target.value)}
                    rows={2}
                    className="w-full text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-brand-300 placeholder:text-slate-300"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleSubmitFeedback}
                      disabled={savingFeedback || feedbackRating === 0}
                      className="px-4 py-1.5 text-xs font-bold bg-brand-600 text-white rounded-xl hover:bg-brand-700 disabled:opacity-60 transition-colors flex items-center gap-1.5"
                    >
                      {savingFeedback && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                      Submit Feedback
                    </button>
                    <button
                      onClick={() => setShowFeedback(false)}
                      className="px-3 py-1.5 text-xs font-semibold text-slate-500 hover:text-slate-700 transition-colors"
                    >
                      Skip
                    </button>
                  </div>
                </div>
              )}

              {/* Error message */}
              {outcomeError && (
                <p className="mt-2 text-xs text-red-600">{outcomeError}</p>
              )}
            </div>
          </div>
        )}
      </div>

      {showEmailModal && (
        <EmailModal
          candidateId={candidate_id}
          resultId={result_id}
          onClose={() => setShowEmailModal(false)}
        />
      )}
    </>
  )
})
