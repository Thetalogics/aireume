import { useState, useEffect, useRef } from 'react'
import { FileText, Download, CheckCircle, AlertCircle, Send } from 'lucide-react'
import { printDocument } from '../lib/printDocument'
import { getScorecard, saveOverallAssessment } from '../lib/api'

/** Coerce any value to a render-safe string. */
function safeStr(v) {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v) } catch { return String(v) }
}

const RATING_COLOR = {
  strong: 'text-emerald-600 bg-emerald-50',
  adequate: 'text-amber-600 bg-amber-50',
  weak: 'text-red-600 bg-red-50',
}

function DimensionCard({ dimension, label, icon: Icon }) {
  if (!dimension) return null
  const total = dimension.total_questions || 0
  const evaluated = dimension.evaluated_count || 0
  const evaluators = dimension.evaluators || []

  return (
    <div className="p-4 bg-white rounded-xl ring-1 ring-slate-200">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 text-brand-600" />
          <span className="font-bold text-sm text-slate-800">{label}</span>
        </div>
        <span className="text-xs text-slate-400">{evaluated}/{total} evaluated</span>
      </div>

      <div className="flex gap-3 mb-3">
        {dimension.strong_count > 0 && (
          <div className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span className="text-xs text-slate-600">{dimension.strong_count} Strong</span>
          </div>
        )}
        {dimension.adequate_count > 0 && (
          <div className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-amber-400" />
            <span className="text-xs text-slate-600">{dimension.adequate_count} Adequate</span>
          </div>
        )}
        {dimension.weak_count > 0 && (
          <div className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-400" />
            <span className="text-xs text-slate-600">{dimension.weak_count} Weak</span>
          </div>
        )}
        {evaluated === 0 && (
          <span className="text-xs text-slate-400 italic">Not yet evaluated</span>
        )}
      </div>

      {dimension.key_notes?.length > 0 && (
        <div className="space-y-1 mb-3">
          {dimension.key_notes.map((note, i) => (
            <p key={i} className="text-xs text-slate-500 pl-3 border-l-2 border-slate-200">{note}</p>
          ))}
        </div>
      )}

      {evaluators.length > 0 && (
        <div className="mt-2 pt-2 border-t border-slate-100">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Team Evaluations</p>
          <div className="space-y-1">
            {evaluators.map((ev, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="text-slate-500 truncate max-w-[60%]" title={ev.email}>{ev.email}</span>
                <span className={`px-1.5 py-0.5 rounded text-xs font-semibold capitalize ${RATING_COLOR[ev.rating] || 'text-slate-500 bg-slate-100'}`}>
                  Q{ev.question_index + 1}: {ev.rating}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function InterviewScorecard({ resultId, showHeading = false }) {
  const [scorecard, setScorecard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [printError, setPrintError] = useState('')
  const [overall, setOverall] = useState('')
  const [recommendation, setRecommendation] = useState('')
  const [savingOverall, setSavingOverall] = useState(false)
  const scorecardRef = useRef(null)

  useEffect(() => {
    if (!resultId) return
    const loadScorecard = async () => {
      try {
        const data = await getScorecard(resultId)
        setScorecard(data)
        setOverall(data.overall_assessment || '')
        setRecommendation(data.recruiter_recommendation || '')
      } catch (err) {
        console.error('Failed to load scorecard:', err)
        setError('Failed to load scorecard')
      }
      setLoading(false)
    }
    loadScorecard()
  }, [resultId])

  const handleSaveOverall = async () => {
    setSavingOverall(true)
    try {
      await saveOverallAssessment(resultId, {
        overall_assessment: overall,
        recruiter_recommendation: recommendation,
      })
    } catch (err) {
      console.error('Failed to save overall assessment:', err)
    }
    setSavingOverall(false)
  }

  const exportAsPdf = () => {
    if (!scorecardRef.current) return
    try {
      setPrintError('')
      printDocument()
    } catch (err) {
      console.error('Print failed:', err)
      setPrintError('Could not open the print dialog. Use the browser print option.')
    }
  }

  if (loading) return <div className="text-center text-sm text-slate-400 py-8">Loading scorecard...</div>
  if (error) return <div className="text-center text-sm text-red-400 py-8">{error}</div>
  if (!scorecard) return null

  const evaluatedCount =
    (scorecard.technical_summary?.evaluated_count || 0) +
    (scorecard.behavioral_summary?.evaluated_count || 0) +
    (scorecard.culture_fit_summary?.evaluated_count || 0) +
    (scorecard.experience_deep_dive_summary?.evaluated_count || 0)

  if (evaluatedCount === 0 && !scorecard.debrief && !scorecard.overall_assessment) {
    return null
  }

  return (
    <div className="space-y-4">
      {/* Section heading — only when showHeading is true */}
      {showHeading && (
        <div className="flex items-center gap-2 mb-4">
          <FileText className="w-5 h-5 text-brand-600" />
          <h2 className="text-lg font-bold text-slate-900">Recruiter Scorecard</h2>
        </div>
      )}

      {/* Export Button */}
      <div className="flex justify-end gap-2">
        <button
          onClick={exportAsPdf}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 text-white text-xs font-semibold rounded-lg hover:bg-brand-700 transition-colors"
        >
          <Download className="w-3.5 h-3.5" />
          Print scorecard
        </button>
      </div>
      {printError && <p className="text-xs text-red-600 mb-3">{printError}</p>}

      {/* Printable Scorecard */}
      <div ref={scorecardRef} className="space-y-4 p-6 bg-white rounded-2xl ring-1 ring-slate-200">
        {/* Header */}
        <div className="flex items-start justify-between pb-4 border-b border-slate-100">
          <div>
            <h3 className="text-lg font-bold text-slate-900">{safeStr(scorecard.candidate_name)}</h3>
            <p className="text-sm text-slate-500">{safeStr(scorecard.role_title)}</p>
          </div>
          <div className="text-right">
            {scorecard.fit_score != null && (
              <div className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-bold ${
                scorecard.fit_score >= 72 ? 'bg-emerald-100 text-emerald-700' :
                scorecard.fit_score >= 45 ? 'bg-amber-100 text-amber-700' :
                'bg-red-100 text-red-700'
              }`}>
                {scorecard.fit_score}% Fit
              </div>
            )}
            {scorecard.recommendation && (
              <p className="text-xs text-slate-400 mt-1">AI: {safeStr(scorecard.recommendation)}</p>
            )}
          </div>
        </div>

        {/* Dimension Summaries */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <DimensionCard dimension={scorecard.technical_summary} label="Technical" icon={FileText} />
          <DimensionCard dimension={scorecard.behavioral_summary} label="Behavioral" icon={CheckCircle} />
          <DimensionCard dimension={scorecard.culture_fit_summary} label="Culture Fit" icon={AlertCircle} />
          <DimensionCard dimension={scorecard.experience_deep_dive_summary} label="Experience Deep-Dive" icon={FileText} />
        </div>

        {/* Recruiter Debrief Section */}
        {scorecard?.debrief && (
          <div className="mt-6 p-5 bg-gradient-to-br from-brand-50 to-indigo-50 rounded-2xl ring-1 ring-brand-200">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-brand-600" />
                <h3 className="font-bold text-brand-800">Recruiter Debrief</h3>
              </div>
              <div className="flex items-center gap-3">
                {/* Recruiter Score Badge */}
                {scorecard.recruiter_score != null && (
                  <div className={`px-3 py-1 rounded-full text-sm font-bold ${
                    scorecard.recruiter_score >= 70 ? 'bg-emerald-100 text-emerald-700' :
                    scorecard.recruiter_score >= 40 ? 'bg-amber-100 text-amber-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    {scorecard.recruiter_score}/100
                  </div>
                )}
                {/* Recommendation Label */}
                {scorecard.recruiter_recommendation && (
                  <div className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide ${
                    scorecard.recruiter_recommendation.toLowerCase() === 'advance' ? 'bg-green-100 text-green-700' :
                    scorecard.recruiter_recommendation.toLowerCase() === 'hold' ? 'bg-amber-100 text-amber-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    {scorecard.recruiter_recommendation.charAt(0).toUpperCase() + scorecard.recruiter_recommendation.slice(1)}
                  </div>
                )}
              </div>
            </div>

            {/* Debrief Content */}
            <div className="space-y-3">
              {scorecard.debrief.overview && (
                <div>
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Overview</span>
                  <p className="text-sm text-slate-700 mt-1">{scorecard.debrief.overview}</p>
                </div>
              )}
              {scorecard.debrief.strengths && (
                <div>
                  <span className="text-xs font-semibold text-emerald-600 uppercase tracking-wide">Strengths Observed</span>
                  <p className="text-sm text-slate-700 mt-1">{scorecard.debrief.strengths}</p>
                </div>
              )}
              {scorecard.debrief.concerns && (
                <div>
                  <span className="text-xs font-semibold text-amber-600 uppercase tracking-wide">Concerns</span>
                  <p className="text-sm text-slate-700 mt-1">{scorecard.debrief.concerns}</p>
                </div>
              )}
              {scorecard.debrief.recommendation_rationale && (
                <div>
                  <span className="text-xs font-semibold text-brand-600 uppercase tracking-wide">Recommendation Rationale</span>
                  <p className="text-sm text-slate-700 mt-1">{scorecard.debrief.recommendation_rationale}</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Strengths & Concerns */}
        {(scorecard.strengths_confirmed?.length > 0 || scorecard.concerns_identified?.length > 0) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {scorecard.strengths_confirmed?.length > 0 && (
              <div className="p-3 bg-emerald-50 rounded-xl">
                <span className="text-xs font-semibold text-emerald-700 uppercase">Strengths Confirmed</span>
                <ul className="mt-2 space-y-1">
                  {scorecard.strengths_confirmed.map((s, i) => (
                    <li key={i} className="text-xs text-emerald-600">{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {scorecard.concerns_identified?.length > 0 && (
              <div className="p-3 bg-red-50 rounded-xl">
                <span className="text-xs font-semibold text-red-700 uppercase">Concerns Identified</span>
                <ul className="mt-2 space-y-1">
                  {scorecard.concerns_identified.map((c, i) => (
                    <li key={i} className="text-xs text-red-600">{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Overall Assessment (editable) */}
        <div className="pt-3 border-t border-slate-100">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-bold text-slate-800">Recruiter Assessment</span>
            <div className="flex items-center gap-2">
              <select
                value={recommendation}
                onChange={(e) => setRecommendation(e.target.value)}
                className="text-xs bg-white border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-300"
              >
                <option value="">Select recommendation</option>
                <option value="advance">Advance to Next Round</option>
                <option value="hold">Hold / Need More Info</option>
                <option value="reject">Do Not Advance</option>
              </select>
              <button
                onClick={handleSaveOverall}
                disabled={savingOverall}
                className="flex items-center gap-1 px-2 py-1 bg-brand-100 text-brand-700 text-xs font-semibold rounded-lg hover:bg-brand-200 transition-colors disabled:opacity-50"
              >
                <Send className="w-3 h-3" />
                {savingOverall ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
          <textarea
            value={overall}
            onChange={(e) => setOverall(e.target.value)}
            placeholder="Write your overall assessment of this candidate for the hiring manager..."
            rows={4}
            className="w-full text-sm text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-brand-300 focus:border-brand-300 placeholder:text-slate-300"
          />

          <div className="mt-2 text-xs text-slate-400">
            Evaluated by: {safeStr(scorecard.evaluator_email)}
            {scorecard.evaluated_at && ` \u2022 ${new Date(scorecard.evaluated_at).toLocaleDateString()}`}
          </div>
        </div>
      </div>
    </div>
  )
}
