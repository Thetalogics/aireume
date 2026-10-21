import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Download, Link2, Users, CheckCircle, AlertTriangle, XCircle,
  ArrowLeft, ChevronDown, ChevronUp, FileText, UserCheck,
} from 'lucide-react'
import { printDocument } from '../lib/printDocument'
import { getHandoffPackage, createHandoffShareLink } from '../lib/api'
import usePermissions from '../hooks/usePermissions'
import { safeStr } from '../lib/utils'

// ── Sub-components ──────────────────────────────────────────────────────────────

function FitScoreGauge({ score }) {
  if (score == null) return <span className="text-slate-400 text-sm font-medium">--</span>
  const color =
    score >= 72 ? 'text-emerald-600 bg-emerald-50 ring-emerald-200' :
    score >= 45 ? 'text-amber-600 bg-amber-50 ring-amber-200' :
                  'text-red-600 bg-red-50 ring-red-200'
  return (
    <span className={`inline-flex items-center justify-center min-w-[3rem] px-3 py-1 rounded-full text-sm font-extrabold ring-1 ${color}`}>
      {score}
    </span>
  )
}

function RecommendationBadge({ rec }) {
  if (!rec) return null
  const r = String(rec).toLowerCase()
  const cls =
    r.includes('shortlist') || r.includes('strong hire') || r.includes('hire')
      ? 'bg-green-100 text-green-700 ring-green-200'
      : r.includes('reject') || r.includes('do not')
        ? 'bg-red-100 text-red-700 ring-red-200'
        : 'bg-amber-100 text-amber-700 ring-amber-200'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold ring-1 ${cls}`}>
      {safeStr(rec)}
    </span>
  )
}

function SkillTags({ skills, type = 'matched' }) {
  if (!Array.isArray(skills) || skills.length === 0) return null
  const cls = type === 'matched'
    ? 'bg-green-50 text-green-700 ring-green-200'
    : 'bg-red-50 text-red-600 ring-red-200'
  return (
    <div className="flex flex-wrap gap-1">
      {skills.map((s, i) => (
        <span key={i} className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium ring-1 ${cls}`}>
          {safeStr(s)}
        </span>
      ))}
    </div>
  )
}

function TruncatedText({ text, maxLen = 200 }) {
  const [expanded, setExpanded] = useState(false)
  if (!text) return null
  if (text.length <= maxLen) return <p className="text-sm text-slate-600">{text}</p>
  return (
    <div>
      <p className="text-sm text-slate-600">
        {expanded ? text : text.slice(0, maxLen) + '...'}
      </p>
      <button
        onClick={() => setExpanded(v => !v)}
        className="text-xs font-semibold text-brand-600 hover:text-brand-800 mt-1 flex items-center gap-0.5"
      >
        {expanded ? <><ChevronUp className="w-3 h-3" /> Show less</> : <><ChevronDown className="w-3 h-3" /> Show more</>}
      </button>
    </div>
  )
}

function InterviewImpression({ label, data }) {
  if (!data) return null
  const impression = data.avg_impression || ''
  const cls =
    impression === 'strong' ? 'bg-emerald-100 text-emerald-700 ring-emerald-200' :
    impression === 'adequate' ? 'bg-amber-100 text-amber-700 ring-amber-200' :
    impression === 'weak' ? 'bg-red-100 text-red-700 ring-red-200' :
    'bg-slate-100 text-slate-600 ring-slate-200'

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-semibold text-slate-500 capitalize">{label}</span>
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold ring-1 ${cls}`}>
        {data.strong ?? 0}S / {data.adequate ?? 0}A / {data.weak ?? 0}W
      </span>
      {impression && (
        <span className={`px-2 py-0.5 rounded-full text-xs font-bold ring-1 ${cls}`}>
          {impression}
        </span>
      )}
    </div>
  )
}

function CandidateCard({ candidate }) {
  const interviewScores = candidate.interview_scores
  const hasInterviewData = interviewScores && (
    interviewScores.technical || interviewScores.behavioral
  )

  return (
    <div className="bg-white rounded-xl shadow-sm border p-6 space-y-4">
      {/* Header: Name + Score + Recommendation */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-lg font-bold text-slate-900 truncate">{safeStr(candidate.name)}</h3>
          {candidate.recommendation && (
            <div className="mt-1">
              <RecommendationBadge rec={candidate.recommendation} />
            </div>
          )}
        </div>
        <div className="flex-shrink-0">
          <FitScoreGauge score={candidate.fit_score} />
        </div>
      </div>

      {/* Strengths */}
      {Array.isArray(candidate.strengths) && candidate.strengths.length > 0 && (
        <div>
          <h4 className="text-xs font-bold text-emerald-700 uppercase tracking-wide mb-1.5 flex items-center gap-1">
            <CheckCircle className="w-3.5 h-3.5" /> Strengths
          </h4>
          <ul className="space-y-0.5">
            {candidate.strengths.map((s, i) => (
              <li key={i} className="text-sm text-emerald-700 pl-4 relative before:content-[''] before:absolute before:left-1 before:top-2 before:w-1.5 before:h-1.5 before:rounded-full before:bg-emerald-400">
                {safeStr(s)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Areas of Concern */}
      {Array.isArray(candidate.weaknesses) && candidate.weaknesses.length > 0 && (
        <div>
          <h4 className="text-xs font-bold text-red-700 uppercase tracking-wide mb-1.5 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5" /> Areas of Concern
          </h4>
          <ul className="space-y-0.5">
            {candidate.weaknesses.map((w, i) => (
              <li key={i} className="text-sm text-red-600 pl-4 relative before:content-[''] before:absolute before:left-1 before:top-2 before:w-1.5 before:h-1.5 before:rounded-full before:bg-red-400">
                {safeStr(w)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Skills */}
      {(Array.isArray(candidate.matched_skills) && candidate.matched_skills.length > 0) ||
       (Array.isArray(candidate.missing_skills) && candidate.missing_skills.length > 0) ? (
        <div className="space-y-2">
          {Array.isArray(candidate.matched_skills) && candidate.matched_skills.length > 0 && (
            <div>
              <span className="text-xs font-semibold text-slate-500">Matched</span>
              <div className="mt-0.5"><SkillTags skills={candidate.matched_skills} type="matched" /></div>
            </div>
          )}
          {Array.isArray(candidate.missing_skills) && candidate.missing_skills.length > 0 && (
            <div>
              <span className="text-xs font-semibold text-slate-500">Missing</span>
              <div className="mt-0.5"><SkillTags skills={candidate.missing_skills} type="missing" /></div>
            </div>
          )}
        </div>
      ) : null}

      {/* Experience */}
      {candidate.experience_summary && (
        <div>
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1 flex items-center gap-1">
            <FileText className="w-3.5 h-3.5" /> Experience
          </h4>
          <TruncatedText text={safeStr(candidate.experience_summary)} />
        </div>
      )}

      {/* Education */}
      {candidate.education_summary && (
        <div>
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-1">Education</h4>
          <p className="text-sm text-slate-600">{safeStr(candidate.education_summary)}</p>
        </div>
      )}

      {/* Interview Performance */}
      {hasInterviewData && (
        <div className="pt-3 border-t border-slate-100">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-2 flex items-center gap-1">
            <UserCheck className="w-3.5 h-3.5" /> Interview Performance
          </h4>
          <div className="space-y-1.5">
            {interviewScores.technical && (
              <InterviewImpression label="Technical" data={interviewScores.technical} />
            )}
            {interviewScores.behavioral && (
              <InterviewImpression label="Behavioral" data={interviewScores.behavioral} />
            )}
          </div>
        </div>
      )}

      {/* Recruiter Notes */}
      {(candidate.recruiter_notes || candidate.recruiter_recommendation) && (
        <div className="pt-3 border-t border-slate-100">
          {candidate.recruiter_notes && (
            <blockquote className="border-l-4 border-brand-200 pl-3 py-1 bg-brand-50/50 rounded-r-lg">
              <p className="text-sm text-slate-700 italic">{safeStr(candidate.recruiter_notes)}</p>
            </blockquote>
          )}
          {candidate.recruiter_recommendation && (
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500">Recruiter:</span>
              <RecommendationBadge rec={candidate.recruiter_recommendation} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ComparisonMatrix({ matrix }) {
  if (!matrix || !matrix.dimensions) return null
  const scoreMap = matrix.candidates || matrix.scores || {}
  const names = Object.keys(scoreMap)
  if (names.length === 0) return null

  // Find max per dimension for highlighting
  const maxPerDim = matrix.dimensions.map((_, dimIdx) => {
    let max = -Infinity
    names.forEach(name => {
      const val = scoreMap[name]?.[dimIdx]
      if (val != null && val > max) max = val
    })
    return max
  })

  function scoreColor(score) {
    if (score == null) return 'bg-slate-50 text-slate-400'
    if (score >= 80) return 'bg-emerald-100 text-emerald-800'
    if (score >= 60) return 'bg-green-50 text-green-800'
    if (score >= 40) return 'bg-amber-50 text-amber-800'
    return 'bg-red-50 text-red-800'
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border p-6">
      <h3 className="text-lg font-bold text-slate-900 mb-4">Comparison Matrix</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="px-4 py-2.5 text-left text-xs font-bold text-slate-500 uppercase tracking-wide">Dimension</th>
              {names.map(name => (
                <th key={name} className="px-4 py-2.5 text-center text-xs font-bold text-slate-700">{name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.dimensions.map((dim, dimIdx) => (
              <tr key={dim} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-2.5 font-semibold text-slate-700">{dim}</td>
                {names.map(name => {
                  const score = scoreMap[name]?.[dimIdx]
                  const isMax = score != null && score === maxPerDim[dimIdx]
                  return (
                    <td key={name} className="px-4 py-2.5 text-center">
                      <span className={`inline-flex items-center justify-center min-w-[2.5rem] px-2 py-0.5 rounded-md text-xs font-bold ${scoreColor(score)} ${isMax ? 'ring-2 ring-brand-400' : ''}`}>
                        {score != null ? score : '--'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main Component ──────────────────────────────────────────────────────────────

export default function HandoffPackage({ initialData = null, publicMode = false }) {
  const { id: jdId } = useParams()
  const navigate = useNavigate()
  const { canWrite } = usePermissions()
  const contentRef = useRef(null)

  const [data, setData] = useState(initialData)
  const [loading, setLoading] = useState(!initialData)
  const [error, setError] = useState(null)
  const [printError, setPrintError] = useState('')
  const [copySuccess, setCopySuccess] = useState(false)
  const [linkLoading, setLinkLoading] = useState(false)

  useEffect(() => {
    if (initialData || publicMode) {
      setData(initialData)
      setLoading(false)
      setError(null)
      return
    }
    if (!jdId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    const load = async () => {
      try {
        const pkg = await getHandoffPackage(jdId)
        if (!cancelled) setData(pkg)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to fetch handoff package')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [jdId, initialData, publicMode])

  const exportAsPdf = () => {
    if (!contentRef.current) return
    try {
      setPrintError('')
      printDocument()
    } catch {
      setPrintError('Could not open the print dialog. Use the browser print option.')
    }
  }

  const copyLink = async () => {
    if (!jdId || !canWrite) return
    setLinkLoading(true)
    try {
      const link = await createHandoffShareLink(jdId)
      await navigator.clipboard.writeText(link.url)
      setCopySuccess(true)
      setTimeout(() => setCopySuccess(false), 3000)
    } catch {
      setError('Could not create share link')
    } finally {
      setLinkLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="text-center py-16 bg-white rounded-3xl ring-1 ring-red-100 shadow-sm">
          <div className="w-16 h-16 rounded-2xl bg-red-50 ring-1 ring-red-200 flex items-center justify-center mx-auto mb-4">
            <AlertTriangle className="w-8 h-8 text-red-300" />
          </div>
          <p className="text-red-600 font-medium">{error}</p>
          <button
            onClick={() => navigate('/requisitions')}
            className="mt-4 text-sm font-semibold text-brand-600 hover:text-brand-800"
          >
            Back to JD Library
          </button>
        </div>
      </div>
    )
  }

  if (!data) return null

  const candidates = data.shortlisted_candidates || []
  const generatedDate = data.generated_at
    ? new Date(data.generated_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    : new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          {!publicMode && (
          <button
            onClick={() => navigate(-1)}
            className="p-2 hover:bg-brand-50 rounded-xl text-slate-400 hover:text-brand-600 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          )}
          <div>
            <h2 className="text-3xl font-extrabold text-brand-900 tracking-tight">
              Hiring Manager Handoff
            </h2>
            <p className="text-slate-500 text-sm mt-1 font-medium">
              {safeStr(data.jd_name)} &middot; {generatedDate}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 bg-brand-50 text-brand-700 rounded-full text-sm font-bold ring-1 ring-brand-200">
            <Users className="w-4 h-4" />
            {candidates.length} candidate{candidates.length !== 1 ? 's' : ''}
          </span>
          {canWrite && !publicMode && (
          <button
            onClick={copyLink}
            disabled={linkLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white text-slate-600 text-xs font-semibold rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors disabled:opacity-50"
          >
            <Link2 className="w-3.5 h-3.5" />
            {copySuccess ? 'HM link copied!' : linkLoading ? 'Creating link…' : 'Copy HM Link'}
          </button>
          )}
          {!publicMode && (
          <button
            onClick={exportAsPdf}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 text-white text-xs font-semibold rounded-lg hover:bg-brand-700 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            Print
          </button>
          )}
        </div>
        {printError && <p className="text-xs text-red-600 mt-2">{printError}</p>}
      </div>

      {/* Content (printable) */}
      <div ref={contentRef} className="space-y-6 p-6 bg-white dark:bg-slate-900 rounded-2xl ring-1 ring-slate-200 dark:ring-slate-700">
        {(data.score_rulers?.resume || data.score_rulers?.voice_interview) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            {data.score_rulers?.resume && (
              <div className="rounded-xl bg-slate-50 dark:bg-slate-800 p-3 ring-1 ring-slate-200 dark:ring-slate-700">
                <p className="font-bold text-slate-700 dark:text-slate-200">Resume screening</p>
                <p className="text-slate-500 dark:text-slate-400 mt-1">
                  Shortlist ≥ {data.score_rulers.resume.shortlist} · Consider ≥ {data.score_rulers.resume.consider} · else Reject
                </p>
              </div>
            )}
            {data.score_rulers?.voice_interview && (
              <div className="rounded-xl bg-slate-50 dark:bg-slate-800 p-3 ring-1 ring-slate-200 dark:ring-slate-700">
                <p className="font-bold text-slate-700 dark:text-slate-200">Voice / interview</p>
                <p className="text-slate-500 dark:text-slate-400 mt-1">
                  Advance to HM ≥ {data.score_rulers.voice_interview.advance_hm} · Advance ≥ {data.score_rulers.voice_interview.advance} · Hold ≥ {data.score_rulers.voice_interview.hold} · else Reject
                </p>
              </div>
            )}
          </div>
        )}
        {candidates.length === 0 ? (
          /* Empty state */
          <div className="text-center py-16">
            <div className="w-16 h-16 rounded-2xl bg-slate-50 ring-1 ring-slate-200 flex items-center justify-center mx-auto mb-4">
              <Users className="w-8 h-8 text-slate-300" />
            </div>
            <p className="text-slate-500 font-medium">
              No shortlisted candidates for this role. Shortlist candidates from the ranking page first.
            </p>
          </div>
        ) : (
          <>
            {/* Candidate Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {candidates.map((c, i) => (
                <CandidateCard key={c.candidate_id || i} candidate={c} />
              ))}
            </div>

            {/* Comparison Matrix */}
            {data.comparison_matrix && <ComparisonMatrix matrix={data.comparison_matrix} />}
          </>
        )}
      </div>
    </div>
  )
}
