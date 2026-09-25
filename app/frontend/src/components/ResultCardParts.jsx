import {
  AlertTriangle, ChevronDown, ChevronUp,
  CheckCircle, XCircle, TrendingUp, ClipboardList,
  Copy, Check, Mail, X, Loader2,
  Sparkles, Info, UserCheck, Star, CheckCircle2,
} from 'lucide-react'
import { useState } from 'react'
import { generateEmail } from '../lib/api'
import { safeStr, toScoreNumber } from '../lib/utils'

export function ScoreBar({ label, value, color }) {
  const barColor = {
    green:  'bg-green-500',
    blue:   'bg-brand-500',
    amber:  'bg-amber-500',
    purple: 'bg-brand-600',
    teal:   'bg-teal-500',
    rose:   'bg-rose-400',
  }[color] || 'bg-brand-400'
  const score = toScoreNumber(value)

  return (
    <div>
      <div className="flex justify-between items-center mb-1.5">
        <span className="text-xs font-semibold text-slate-600">{label}</span>
        <span className="text-xs font-bold text-brand-700">{score}%</span>
      </div>
      <div className="w-full bg-brand-100 rounded-full h-2">
        <div
          className={`h-2 rounded-full transition-all duration-700 ${barColor}`}
          style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
        />
      </div>
    </div>
  )
}

export function RiskBadge({ level }) {
  const styles = {
    Low:    'bg-green-100 text-green-700 ring-green-200',
    Medium: 'bg-amber-100 text-amber-700 ring-amber-200',
    High:   'bg-red-100 text-red-700 ring-red-200',
  }
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ring-1 ${styles[level] || styles.Medium}`}>
      {level} Risk
    </span>
  )
}

export function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      className="p-1.5 rounded-lg hover:bg-brand-50 transition-colors text-slate-400 hover:text-brand-600"
      aria-label="Copy to clipboard"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

export function CollapsibleSection({ title, icon: Icon, iconColor = 'text-brand-600', bgColor = 'bg-brand-50', children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ring-1 ring-brand-100 rounded-2xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between p-4 hover:bg-brand-50/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <div className={`w-6 h-6 rounded-lg ${bgColor} flex items-center justify-center`}>
            <Icon className={`w-3.5 h-3.5 ${iconColor}`} />
          </div>
          <span className="font-bold text-brand-900 text-sm">{title}</span>
        </div>
        {open
          ? <ChevronUp className="w-4 h-4 text-brand-500" />
          : <ChevronDown className="w-4 h-4 text-brand-500" />}
      </button>
      {open && (
        <div className="px-4 pb-4 border-t border-brand-50 pt-3">
          {children}
        </div>
      )}
    </div>
  )
}

// ─── Email modal ──────────────────────────────────────────────────────────────

export function EmailModal({ candidateId, resultId, onClose }) {
  const [type, setType]       = useState('shortlist')
  const [draft, setDraft]     = useState(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied]   = useState(false)

  const EMAIL_TYPES = [
    { value: 'shortlist',      label: 'Shortlist' },
    { value: 'rejection',      label: 'Rejection' },
    { value: 'screening_call', label: 'Screening Call' },
  ]

  const handleGenerate = async () => {
    if (!candidateId) {
      setDraft({ subject: 'N/A', body: 'Save candidate first to generate personalized emails.' })
      return
    }
    setLoading(true)
    try {
      const result = await generateEmail(candidateId, type)
      setDraft(result)
    } catch {
      setDraft({ subject: 'Generation failed', body: 'Could not generate email. Please try again.' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white/95 backdrop-blur-xl rounded-3xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-lg card-animate">
        <div className="flex items-center justify-between p-5 border-b border-brand-50">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-brand-50 flex items-center justify-center">
              <Mail className="w-4 h-4 text-brand-600" />
            </div>
            <h3 className="font-bold text-brand-900">Generate Email</h3>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-brand-50 rounded-xl transition-colors" aria-label="Close email dialog">
            <X className="w-5 h-5 text-slate-500" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div className="flex gap-2">
            {EMAIL_TYPES.map(t => (
              <button
                key={t.value}
                onClick={() => { setType(t.value); setDraft(null) }}
                className={`px-3 py-1.5 rounded-xl text-sm font-semibold transition-all ${
                  type === t.value
                    ? 'bg-brand-600 text-white shadow-brand-sm'
                    : 'bg-brand-50 text-slate-600 hover:bg-brand-100 hover:text-brand-700'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {draft && (
            <div className="space-y-3">
              <div>
                <p className="text-xs font-bold text-slate-500 uppercase tracking-wide">Subject</p>
                <p className="text-sm font-semibold text-brand-900 mt-1">{draft.subject}</p>
              </div>
              <div>
                <label htmlFor="resultcard-body-1" className="text-xs font-bold text-slate-500 uppercase tracking-wide">Body</label>
                <textarea id="resultcard-body-1"
                  value={draft.body}
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                  rows={8}
                  className="w-full mt-1.5 px-3 py-2.5 text-sm ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 rounded-xl resize-none"
                />
              </div>
            </div>
          )}

          <div className="flex justify-between items-center pt-1">
            <button
              onClick={handleGenerate}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 btn-brand text-white text-sm font-bold rounded-xl disabled:opacity-60 shadow-brand-sm"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
              {loading ? 'Generating...' : 'Generate'}
            </button>
            {draft && (
              <button
                onClick={() => { navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
                className="flex items-center gap-2 px-4 py-2 ring-1 ring-brand-200 text-brand-700 text-sm font-semibold rounded-xl hover:bg-brand-50 transition-colors"
              >
                {copied ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4" />}
                {copied ? 'Copied!' : 'Copy Email'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Score Breakdown Panel with expandable evidence ──────────────────────────

export function ScoreBreakdownPanel({ scoreBreakdown, recommendationRationale, riskSummary }) {
  const [showDetails, setShowDetails] = useState(false)

  // Handle both old (scalar) and new (dict) formats gracefully
  const skillBreakdown = scoreBreakdown?.skill_match
  const isSkillDetailed = typeof skillBreakdown === 'object' && skillBreakdown !== null
  const skillScore = toScoreNumber(skillBreakdown)

  const expBreakdown = scoreBreakdown?.experience_match
  const isExpDetailed = typeof expBreakdown === 'object' && expBreakdown !== null
  const expScore = toScoreNumber(expBreakdown)

  // Confidence dot color based on match_type
  const matchTypeColor = (type) => {
    if (type === 'exact') return 'bg-green-500'
    if (type === 'alias') return 'bg-amber-400'
    if (type === 'substring') return 'bg-orange-400'
    if (type === 'hierarchy_inferred') return 'bg-slate-300'
    return 'bg-blue-400'
  }

  const matchTypeLabel = (type) => {
    if (type === 'exact') return 'Exact'
    if (type === 'alias') return 'Alias'
    if (type === 'substring') return 'Partial'
    if (type === 'hierarchy_inferred') return 'Inferred'
    return type || 'Match'
  }

  return (
    <div className="bg-brand-50/60 rounded-2xl p-5 ring-1 ring-brand-100">
      {/* Header with toggle */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-brand-500" />
          <h3 className="text-sm font-bold text-brand-800 uppercase tracking-wide">Score Breakdown</h3>
        </div>
        <button
          onClick={() => setShowDetails(v => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-brand-700 hover:bg-brand-100 transition-colors ring-1 ring-brand-200"
        >
          {showDetails ? (
            <>
              <ChevronUp className="w-3.5 h-3.5" />
              Hide Details
            </>
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              View Score Details
            </>
          )}
        </button>
      </div>

      {/* Score bars — always visible */}
      <div className="grid grid-cols-2 gap-4">
        <ScoreBar label="Skill Match" value={skillScore} color="blue" />
        <ScoreBar label="Experience" value={expScore} color="green" />
        <ScoreBar label="Education" value={scoreBreakdown.education ?? 0} color="amber" />
        <ScoreBar label="Timeline" value={scoreBreakdown.timeline ?? scoreBreakdown.stability ?? 0} color="purple" />
        {scoreBreakdown.architecture != null && (
          <ScoreBar label="Architecture" value={scoreBreakdown.architecture} color="teal" />
        )}
        {scoreBreakdown.domain_fit != null && (
          <ScoreBar label="Domain Fit" value={scoreBreakdown.domain_fit} color="rose" />
        )}
      </div>

      {/* Expandable evidence details */}
      {showDetails && (
        <div className="mt-4 pt-4 border-t border-brand-200 space-y-4">

          {/* ── Skill Match Evidence ── */}
          {isSkillDetailed && (
            <div className="bg-white rounded-xl p-4 ring-1 ring-brand-100">
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle2 className="w-4 h-4 text-brand-600" />
                <h4 className="text-sm font-bold text-slate-800">Skill Match Evidence</h4>
              </div>

              {/* Required skills progress */}
              {skillBreakdown.required_total > 0 && (
                <div className="mb-3">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs font-semibold text-slate-600">
                      Required Skills Matched
                    </span>
                    <span className="text-xs font-bold text-brand-700">
                      {Math.min(skillBreakdown.required_matched, skillBreakdown.required_total)}/{skillBreakdown.required_total}
                    </span>
                  </div>
                  <div className="w-full bg-brand-100 rounded-full h-2.5">
                    <div
                      className="h-2.5 rounded-full bg-brand-500 transition-all duration-500"
                      style={{ width: `${Math.min(100, (skillBreakdown.required_matched / Math.max(skillBreakdown.required_total, 1)) * 100)}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Nice-to-have skills progress */}
              {skillBreakdown.nice_total > 0 && (
                <div className="mb-3">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs font-semibold text-slate-600">
                      Nice-to-Have Skills Matched
                    </span>
                    <span className="text-xs font-bold text-amber-700">
                      {skillBreakdown.nice_matched}/{skillBreakdown.nice_total}
                    </span>
                  </div>
                  <div className="w-full bg-amber-100 rounded-full h-2.5">
                    <div
                      className="h-2.5 rounded-full bg-amber-500 transition-all duration-500"
                      style={{ width: `${Math.min(100, (skillBreakdown.nice_matched / Math.max(skillBreakdown.nice_total, 1)) * 100)}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Missing required skills */}
              {skillBreakdown.missing_required?.length > 0 && (
                <div className="mb-3">
                  <span className="text-xs font-semibold text-red-600 block mb-1.5">Missing Required Skills</span>
                  <div className="flex flex-wrap gap-1.5">
                    {skillBreakdown.missing_required.map((skill, i) => (
                      <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700 ring-1 ring-red-200">
                        <XCircle className="w-3 h-3" />
                        {typeof skill === 'string' ? skill : skill?.skill || String(skill)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Matched skills with confidence */}
              {skillBreakdown.matched_details?.length > 0 && (
                <div className="mb-3">
                  <span className="text-xs font-semibold text-green-700 block mb-1.5">Matched Skills</span>
                  <div className="flex flex-wrap gap-1.5">
                    {skillBreakdown.matched_details.map((m, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-slate-700 ring-1 ring-green-200"
                        title={`${matchTypeLabel(m.match_type)} match — confidence: ${(m.confidence * 100).toFixed(0)}%`}
                      >
                        <span className={`w-2 h-2 rounded-full ${matchTypeColor(m.match_type)}`} />
                        {m.skill}
                        <span className="text-slate-400 text-[10px]">{(m.confidence * 100).toFixed(0)}%</span>
                      </span>
                    ))}
                  </div>
                  <div className="flex items-center gap-3 mt-2 text-[10px] text-slate-400">
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500" /> Exact</span>
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-400" /> Alias</span>
                    <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-orange-400" /> Partial</span>
                  </div>
                </div>
              )}

              {/* Proficiency adjustments */}
              {skillBreakdown.proficiency_adjustments?.length > 0 && (
                <div className="mb-3">
                  <span className="text-xs font-semibold text-indigo-700 block mb-1.5">Proficiency Adjustments</span>
                  <div className="space-y-1">
                    {skillBreakdown.proficiency_adjustments.map((p, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs text-slate-600">
                        <span className="w-2 h-2 rounded-full bg-indigo-400" />
                        <span className="font-medium">{p.skill}</span>
                        <span className="text-slate-400">— required: {String(p.required)}</span>
                        <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 font-semibold text-[10px]">{p.factor}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Team gap bonus */}
              {skillBreakdown.team_gap_bonus > 0 && (
                <div className="flex items-center gap-2 mb-3">
                  <UserCheck className="w-3.5 h-3.5 text-teal-600" />
                  <span className="text-xs font-semibold text-teal-700">
                    Team Gap Bonus: +{skillBreakdown.team_gap_bonus}
                  </span>
                </div>
              )}

              {/* Trend factors applied */}
              {skillBreakdown.trend_factors_applied?.length > 0 && (
                <div>
                  <span className="text-xs font-semibold text-purple-700 block mb-1.5">Market Trend Factors</span>
                  <div className="space-y-1">
                    {skillBreakdown.trend_factors_applied.map((t, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs text-slate-600">
                        <TrendingUp className={`w-3 h-3 ${t.direction === 'rising' ? 'text-green-500' : t.direction === 'falling' ? 'text-red-500' : 'text-slate-400'}`} />
                        <span className="font-medium">{t.skill}</span>
                        <span className="text-slate-400">— {t.direction}</span>
                        <span className={`px-1.5 py-0.5 rounded font-semibold text-[10px] ${
                          t.factor > 1 ? 'bg-green-50 text-green-700' : t.factor < 1 ? 'bg-red-50 text-red-700' : 'bg-slate-50 text-slate-600'
                        }`}>
                          ×{t.factor}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Confidence metadata */}
              {skillBreakdown.confidence_weighted && (
                <div className="mt-3 pt-3 border-t border-brand-100 flex items-center gap-2 text-xs text-slate-500">
                  <Info className="w-3 h-3" />
                  <span>Confidence-weighted scoring (avg: {((skillBreakdown.avg_confidence ?? 1) * 100).toFixed(0)}%)</span>
                </div>
              )}
            </div>
          )}

          {/* ── Experience Evidence ── */}
          {isExpDetailed && (
            <div className="bg-white rounded-xl p-4 ring-1 ring-brand-100">
              <div className="flex items-center gap-2 mb-3">
                <Star className="w-4 h-4 text-green-600" />
                <h4 className="text-sm font-bold text-slate-800">Experience</h4>
              </div>
              <div className="flex items-baseline gap-2 mb-2">
                {expBreakdown.actual_years != null && (
                  <span className="text-lg font-bold text-slate-800">{expBreakdown.actual_years}y</span>
                )}
                {expBreakdown.required_years != null && (
                  <span className="text-xs text-slate-500">vs {expBreakdown.required_years}y required</span>
                )}
              </div>
              {expBreakdown.explanation && (
                <p className="text-xs text-slate-600 italic">{expBreakdown.explanation}</p>
              )}
            </div>
          )}

          {/* ── Other score dimensions as simple bars ── */}
          <div className="bg-white rounded-xl p-4 ring-1 ring-brand-100">
            <div className="flex items-center gap-2 mb-3">
              <ClipboardList className="w-4 h-4 text-brand-600" />
              <h4 className="text-sm font-bold text-slate-800">Other Dimensions</h4>
            </div>
            <div className="space-y-2">
              <ScoreBar label="Education" value={scoreBreakdown.education ?? 0} color="amber" />
              <ScoreBar label="Timeline" value={scoreBreakdown.timeline ?? scoreBreakdown.stability ?? 0} color="purple" />
              {scoreBreakdown.architecture != null && (
                <ScoreBar label="Architecture" value={scoreBreakdown.architecture} color="teal" />
              )}
              {scoreBreakdown.domain_fit != null && (
                <ScoreBar label="Domain Fit" value={scoreBreakdown.domain_fit} color="rose" />
              )}
              {scoreBreakdown.risk_penalty > 0 && (
                <ScoreBar label="Risk Penalty" value={scoreBreakdown.risk_penalty} color="rose" />
              )}
            </div>
          </div>

          {/* Rationale & seniority */}
          {recommendationRationale && (
            <p className="text-xs text-slate-500 italic">{safeStr(recommendationRationale)}</p>
          )}
          {riskSummary?.seniority_alignment && (
            <div className="flex items-center gap-2">
              <Info className="w-3.5 h-3.5 text-brand-500" />
              <span className="text-xs font-semibold text-brand-700">Seniority Alignment:</span>
              <span className="text-xs text-slate-600">{safeStr(riskSummary.seniority_alignment)}</span>
            </div>
          )}
        </div>
      )}

      {/* Non-expanded rationale & seniority (always show) */}
      {!showDetails && recommendationRationale && (
        <p className="text-xs text-slate-500 mt-3 italic">{safeStr(recommendationRationale)}</p>
      )}
      {!showDetails && riskSummary?.seniority_alignment && (
        <div className="mt-3 pt-3 border-t border-brand-100 flex items-center gap-2">
          <Info className="w-3.5 h-3.5 text-brand-500" />
          <span className="text-xs font-semibold text-brand-700">Seniority Alignment:</span>
          <span className="text-xs text-slate-600">{safeStr(riskSummary.seniority_alignment)}</span>
        </div>
      )}
    </div>
  )
}

// ─── Analysis source badge ────────────────────────────────────────────────────

export function AnalysisSourceBadge({ narrativeReady, isPolling, analysisQuality, aiEnhanced, generationMode }) {
  if (isPolling) {
    return (
      <div className="flex items-center gap-3 p-3 bg-brand-50 ring-1 ring-brand-200 rounded-2xl">
        <div className="w-4 h-4 rounded-full border-2 border-brand-300 border-t-brand-600 animate-spin shrink-0" />
        <p className="text-xs font-semibold text-brand-700 flex-1">
          AI analysis enhancing report…
        </p>
      </div>
    )
  }

  // Only show "AI Generated Report" badge for real LLM narratives.
  if (narrativeReady && (generationMode === 'ai' || aiEnhanced === true)) {
    return (
      <div className="flex items-center gap-3 p-3 bg-green-50 ring-1 ring-green-200 rounded-2xl">
        <Sparkles className="w-4 h-4 text-green-600 shrink-0" />
        <p className="text-xs font-semibold text-green-700 flex-1">
          AI Generated Report
        </p>
        {analysisQuality && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ring-1 shrink-0 ${
            analysisQuality === 'high'   ? 'bg-green-100 text-green-700 ring-green-200' :
            analysisQuality === 'medium' ? 'bg-amber-100 text-amber-700 ring-amber-200' :
                                          'bg-red-100 text-red-700 ring-red-200'
          }`}>
            {analysisQuality} quality
          </span>
        )}
      </div>
    )
  }

  // Show explicit standard mode for deterministic fallbacks.
  if (narrativeReady && (generationMode === 'deterministic_fallback' || aiEnhanced === false)) {
    return (
      <div className="flex items-center gap-3 p-3 bg-amber-50 ring-1 ring-amber-200 rounded-2xl">
        <CheckCircle className="w-4 h-4 text-amber-600 shrink-0" />
        <p className="text-xs font-semibold text-amber-800 flex-1">
          Standard Deterministic Report
        </p>
      </div>
    )
  }

  return null
}

// ─── Pending banner (kept for isPending / null fit_score case) ────────────────

export function PendingBanner() {
  return (
    <div className="flex items-center gap-3 p-4 bg-slate-50 ring-1 ring-slate-200 rounded-2xl">
      <AlertTriangle className="w-5 h-5 text-slate-400 shrink-0" />
      <div>
        <p className="text-sm font-semibold text-slate-600">Automated analysis unavailable</p>
        <p className="text-xs text-slate-400 mt-0.5">Manual review required — try again or contact support if this persists.</p>
      </div>
    </div>
  )
}
