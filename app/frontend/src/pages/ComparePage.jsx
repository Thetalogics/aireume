import { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { GitCompare, Trophy, Check, Download, ChevronDown, ChevronUp, MessageCircle, Gavel, Zap, AlertTriangle, FileText, Target } from 'lucide-react'
import { getHistory, compareResults, exportCsv, getTeamProfiles, getTeamGapAnalysis } from '../lib/api'
import ComparisonMatrix from '../components/ComparisonMatrix'
import EmptyState from '../components/EmptyState'
import { safeStr, toScoreNumber } from '../lib/utils'
import { personLabel } from '../lib/personName'
import { recommendationLabel } from '../lib/constants'

function ScoreCell({ value, isWinner, color = 'brand' }) {
  const colors = {
    brand: 'text-brand-700 bg-brand-50 ring-brand-200',
    green: 'text-green-700 bg-green-50 ring-green-200',
    amber: 'text-amber-700 bg-amber-50 ring-amber-200',
  }
  const score = toScoreNumber(value)
  return (
    <div className={`flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-xl font-bold text-sm ring-1 ${isWinner ? colors[color] : 'text-slate-600 bg-slate-50 ring-slate-200'}`}>
      {isWinner && <Trophy className="w-3.5 h-3.5" />}
      {score}%
    </div>
  )
}

function QualityBadge({ quality }) {
  const styles = {
    high: 'bg-green-100 text-green-800 ring-1 ring-green-200',
    medium: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
    low: 'bg-red-100 text-red-800 ring-1 ring-red-200',
  }
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase ${styles[quality] || styles.medium}`}>
      {quality || 'medium'}
    </span>
  )
}

function VerdictBadge({ verdict, confidence }) {
  const styles = {
    Shortlist: 'bg-green-100 text-green-800 ring-green-200',
    Reject: 'bg-red-100 text-red-800 ring-red-200',
    Consider: 'bg-amber-100 text-amber-800 ring-amber-200',
  }
  const color = styles[verdict] || 'bg-slate-100 text-slate-700 ring-slate-200'
  return (
    <div className={`inline-flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-xl ring-1 ${color}`}>
      <span className="text-xs font-bold uppercase">{verdict || 'N/A'}</span>
      {confidence > 0 && (
        <span className="text-[10px] font-medium opacity-75">{Math.round(confidence * 100)}% confidence</span>
      )}
    </div>
  )
}

function CollapsibleSection({ title, icon: Icon, defaultOpen = false, children }) {
  const [isOpen, setIsOpen] = useState(defaultOpen)
  return (
    <div className="bg-white/90 dark:bg-slate-800/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 dark:ring-slate-700 shadow-sm overflow-hidden">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-brand-50/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          {Icon && <Icon className="w-4 h-4 text-brand-600" />}
          <h4 className="text-sm font-bold text-brand-900">{title}</h4>
        </div>
        {isOpen ? (
          <ChevronUp className="w-4 h-4 text-brand-500" />
        ) : (
          <ChevronDown className="w-4 h-4 text-brand-500" />
        )}
      </button>
      {isOpen && <div className="px-5 pb-4 border-t border-brand-50">{children}</div>}
    </div>
  )
}

export default function ComparePage() {
  const location = useLocation()
  const navigate  = useNavigate()
  const [history, setHistory]         = useState([])
  const [selected, setSelected]       = useState(location.state?.ids || [])
  const [comparison, setComparison]   = useState(null)
  const [loading, setLoading]         = useState(false)
  const [histLoading, setHistLoading] = useState(true)
  const [error, setError]             = useState('')
  const [teamProfiles, setTeamProfiles] = useState([])
  const [selectedTeamProfile, setSelectedTeamProfile] = useState('')
  const [teamGaps, setTeamGaps] = useState([])

  useEffect(() => {
    getHistory().then(data => { setHistory(data); setHistLoading(false) }).catch(() => setHistLoading(false))
    getTeamProfiles()
      .then((data) => setTeamProfiles(Array.isArray(data) ? data : data?.profiles || []))
      .catch(() => setTeamProfiles([]))
  }, [])

  useEffect(() => {
    if (!selectedTeamProfile) {
      setTeamGaps([])
      return
    }
    getTeamGapAnalysis(selectedTeamProfile)
      .then((data) => {
        const gaps = data?.gaps || data?.missing_skills || data?.team_gaps || []
        setTeamGaps(Array.isArray(gaps) ? gaps : [])
      })
      .catch(() => setTeamGaps([]))
  }, [selectedTeamProfile])

  const toggleSelect = (id) => {
    if (selected.includes(id)) {
      setSelected(prev => prev.filter(x => x !== id))
    } else if (selected.length < 5) {
      setSelected(prev => [...prev, id])
    }
  }

  const handleCompare = async () => {
    if (selected.length < 2) { setError('Select at least 2 candidates'); return }
    setError('')
    setLoading(true)
    try {
      const data = await compareResults(selected)
      setComparison(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Comparison failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="text-slate-900 dark:text-slate-100">
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <div className="card-animate">
          <h2 className="text-3xl font-extrabold text-brand-900 dark:text-slate-100 tracking-tight">Candidate Comparison</h2>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1 font-medium">Select 2–5 candidates from history to compare side-by-side.</p>
        </div>

        {/* Selector */}
        {!comparison && (
          <div className="bg-white/90 dark:bg-slate-800/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 dark:ring-slate-700 shadow-brand p-5 space-y-4 card-animate">
            <div className="flex items-center justify-between">
              <p className="text-sm font-bold text-brand-700">
                <span className="text-brand-600">{selected.length}</span>/5 selected
              </p>
              <button
                onClick={handleCompare}
                disabled={selected.length < 2 || loading}
                className="flex items-center gap-2 px-4 py-2 btn-brand text-white text-sm font-bold rounded-xl disabled:opacity-50 shadow-brand-sm"
              >
                <GitCompare className="w-4 h-4" />
                {loading ? 'Comparing...' : 'Compare'}
              </button>
            </div>
            {error && <p className="text-sm text-red-600 font-medium">{error}</p>}

            {histLoading ? (
              <div className="flex justify-center py-8">
                <div className="w-6 h-6 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
              </div>
            ) : history.length === 0 ? (
              <EmptyState
                icon={GitCompare}
                title="No analyses to compare yet"
                description="Analyze at least two resumes and they'll appear here so you can compare candidates side-by-side."
                actionLabel="Analyze a resume"
                actionHref="/analyze"
                className="py-10"
              />
            ) : (
              <div className="space-y-1.5 max-h-80 overflow-y-auto">
                {history.map(r => (
                  <button
                    type="button"
                    key={r.id}
                    onClick={() => toggleSelect(r.id)}
                    className={`flex items-center gap-3 p-3 rounded-2xl ring-1 cursor-pointer transition-all w-full text-left bg-transparent ${
                      selected.includes(r.id)
                        ? 'ring-brand-300 bg-brand-50'
                        : 'ring-slate-200 hover:ring-brand-200 hover:bg-brand-50/40'
                    }`}
                  >
                    <div className={`w-5 h-5 rounded-lg ring-2 flex items-center justify-center shrink-0 transition-all ${
                      selected.includes(r.id)
                        ? 'ring-brand-600 bg-brand-600'
                        : 'ring-slate-300'
                    }`}>
                      {selected.includes(r.id) && <Check className="w-3 h-3 text-white" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-brand-900 truncate">{personLabel(r.candidate_name, r.candidate_email || r.email) || `Result #${r.id}`}</p>
                      {(r.candidate_email || r.email) && personLabel(r.candidate_name, r.candidate_email || r.email) !== (r.candidate_email || r.email) && (
                        <p className="text-xs text-slate-500 truncate">{r.candidate_email || r.email}</p>
                      )}
                      <p className="text-xs text-slate-400 font-medium">
                        {r.job_role && <span>{r.job_role} · </span>}
                        {new Date(r.timestamp).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-bold px-2.5 py-0.5 rounded-full ring-1 ${
                        r.fit_score == null  ? 'bg-slate-50 text-slate-500 ring-slate-200' :
                        r.fit_score >= 72    ? 'bg-green-50 text-green-700 ring-green-200' :
                        r.fit_score >= 45    ? 'bg-amber-50 text-amber-700 ring-amber-200' :
                                               'bg-red-50 text-red-700 ring-red-200'
                      }`}>{r.fit_score ?? '—'}</span>
                      <span className={`text-xs font-medium ${
                        r.final_recommendation === 'Shortlist' ? 'text-green-700' :
                        r.final_recommendation === 'Reject'    ? 'text-red-700'   :
                        r.final_recommendation === 'Pending'   ? 'text-slate-400' : 'text-amber-700'
                      }`}>{recommendationLabel(r.final_recommendation)}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Comparison table */}
        {comparison && (
          <div className="space-y-4 card-animate">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-extrabold text-brand-900 tracking-tight">Comparison Results</h3>
              <div className="flex gap-2">
                <button
                  onClick={() => setComparison(null)}
                  className="px-3 py-2 ring-1 ring-brand-200 text-sm font-semibold rounded-xl hover:bg-brand-50 text-brand-700 transition-colors"
                >
                  New Comparison
                </button>
                <button
                  onClick={() => exportCsv(selected)}
                  className="flex items-center gap-2 px-3 py-2 ring-1 ring-brand-200 text-sm font-semibold rounded-xl hover:bg-brand-50 text-brand-700 transition-colors"
                >
                  <Download className="w-4 h-4" /> Export
                </button>
              </div>
            </div>

            <div className="bg-white/90 dark:bg-slate-800/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 dark:ring-slate-700 shadow-brand overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-brand-50 border-b border-brand-100">
                  <tr>
                    <th className="px-4 py-3.5 text-left text-xs font-bold text-brand-700 uppercase tracking-wide w-36">Category</th>
                    {comparison.candidates.map(c => (
                      <th key={c.id} className="px-4 py-3.5 text-center text-xs font-bold text-brand-900">
                        <div>{personLabel(c.candidate_name, c.candidate_email)}</div>
                        {c.winners?.overall && (
                          <div className="flex items-center justify-center gap-1 text-amber-600 mt-0.5">
                            <Trophy className="w-3 h-3" /> Best Overall
                          </div>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'Fit Score',    key: 'fit_score',                       winnerKey: 'overall',    isScore: false },
                    { label: 'Recommendation', key: 'final_recommendation',          winnerKey: null,         isScore: false },
                    { label: 'Skill Match',  key: 'score_breakdown.skill_match',     winnerKey: 'skills',     isScore: true  },
                    { label: 'Experience',   key: 'score_breakdown.experience_match',winnerKey: 'experience', isScore: true  },
                    { label: 'Education',    key: 'score_breakdown.education',       winnerKey: 'education',  isScore: true  },
                    { label: 'Stability',    key: 'score_breakdown.stability',       winnerKey: 'stability',  isScore: true  },
                    { label: 'Risk Level',   key: 'risk_level',                      winnerKey: null,         isScore: false },
                    { label: 'Employment Gaps', key: 'employment_gaps',              winnerKey: null,         isScore: false, isGaps: true },
                    { label: 'Analysis Quality', key: 'analysis_quality',            winnerKey: null,         isScore: false, isQuality: true },
                  ].map(row => (
                    <tr key={row.label} className="border-b border-brand-50 hover:bg-brand-50/30 transition-colors">
                      <td className="px-4 py-3.5 text-xs font-bold text-brand-700 uppercase tracking-wide">{row.label}</td>
                      {comparison.candidates.map(c => {
                        const val = row.key.includes('.')
                          ? row.key.split('.').reduce((obj, k) => obj?.[k], c)
                          : c[row.key]
                        const isWinner = row.winnerKey ? c.winners?.[row.winnerKey] : false
                        return (
                          <td key={c.id} className="px-4 py-3.5 text-center">
                            {row.isScore ? (
                              <ScoreCell value={val ?? 0} isWinner={isWinner} color="brand" />
                            ) : row.key === 'fit_score' ? (
                              <span className={`font-extrabold text-xl ${isWinner ? 'text-amber-600' : 'text-brand-900'}`}>{val ?? '—'}</span>
                            ) : row.key === 'risk_level' ? (
                              <span className={`text-xs font-bold ${
                                !val          ? 'text-slate-400' :
                                val === 'Low' ? 'text-green-700' :
                                val === 'High'? 'text-red-700'   : 'text-amber-700'
                              }`}>{val || '—'}</span>
                            ) : row.isGaps ? (
                              <span className={`text-sm font-bold ${
                                val === 0 ? 'text-green-700' : val === 1 ? 'text-amber-700' : 'text-red-700'
                              }`}>
                                {val ?? 0}
                              </span>
                            ) : row.isQuality ? (
                              <QualityBadge quality={val} />
                            ) : (
                              <span className={`text-xs font-bold ${
                                val === 'Shortlist' ? 'text-green-700' :
                                val === 'Reject'    ? 'text-red-700'   :
                                val === 'Pending'   ? 'text-slate-400' : 'text-amber-700'
                              }`}>{recommendationLabel(val)}</span>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                  <tr className="bg-brand-50/60">
                    <td className="px-4 py-3.5 text-xs font-bold text-brand-700 uppercase tracking-wide">Actions</td>
                    {comparison.candidates.map(c => (
                      <td key={c.id} className="px-4 py-3.5 text-center">
                        <button
                          onClick={() => navigate('/report', { state: { result: c } })}
                          className="text-xs text-brand-600 hover:text-brand-700 font-bold hover:underline"
                        >
                          View Report
                        </button>
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Strengths & Weaknesses Section */}
            <CollapsibleSection title="Strengths & Weaknesses" defaultOpen={true}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-brand-50/50 rounded-xl p-4 ring-1 ring-brand-100">
                    <h5 className="text-sm font-bold text-brand-900 mb-3">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    <div className="space-y-3">
                      <div>
                        <p className="text-xs font-semibold text-green-700 mb-1.5 flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-green-500" />
                          Strengths
                        </p>
                        <ul className="space-y-1">
                          {c.strengths?.length > 0 ? c.strengths.map((s, i) => (
                            <li key={i} className="text-xs text-slate-700 flex items-start gap-1.5">
                              <span className="text-green-500 mt-0.5">•</span>
                              {safeStr(s)}
                            </li>
                          )) : (
                            <li className="text-xs text-slate-400 italic">No strengths listed</li>
                          )}
                        </ul>
                      </div>
                      <div>
                        <p className="text-xs font-semibold text-red-700 mb-1.5 flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-red-500" />
                          Weaknesses
                        </p>
                        <ul className="space-y-1">
                          {c.weaknesses?.length > 0 ? c.weaknesses.map((w, i) => (
                            <li key={i} className="text-xs text-slate-700 flex items-start gap-1.5">
                              <span className="text-red-500 mt-0.5">•</span>
                              {safeStr(w)}
                            </li>
                          )) : (
                            <li className="text-xs text-slate-400 italic">No weaknesses listed</li>
                          )}
                        </ul>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Interview Questions Preview */}
            <CollapsibleSection title="Interview Questions Preview" icon={MessageCircle}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-brand-50/50 rounded-xl p-4 ring-1 ring-brand-100">
                    <h5 className="text-sm font-bold text-brand-900 mb-3">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    <div className="space-y-2">
                      {c.interview_questions_preview?.length > 0 ? (
                        c.interview_questions_preview.map((q, i) => (
                          <div key={i} className="flex gap-2">
                            <span className="text-xs font-bold text-brand-500 shrink-0">Q{i + 1}:</span>
                            <p className="text-xs text-slate-700">{safeStr(q)}</p>
                          </div>
                        ))
                      ) : (
                        <p className="text-xs text-slate-400 italic">No questions available</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Fit Summaries */}
            <CollapsibleSection title="AI Fit Summaries" icon={FileText} defaultOpen={true}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-brand-50/50 rounded-xl p-4 ring-1 ring-brand-100">
                    <h5 className="text-sm font-bold text-brand-900 mb-2">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    {c.fit_summary ? (
                      <p className="text-xs text-slate-700 leading-relaxed">{safeStr(c.fit_summary)}</p>
                    ) : (
                      <p className="text-xs text-slate-400 italic">No summary available</p>
                    )}
                    {c.recommendation_rationale && (
                      <p className="text-[11px] text-slate-500 mt-2 italic border-t border-brand-100 pt-2">
                        {safeStr(c.recommendation_rationale)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Hiring Decision */}
            <CollapsibleSection title="Hiring Decision" icon={Gavel} defaultOpen={true}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-brand-50/50 rounded-xl p-4 ring-1 ring-brand-100">
                    <div className="flex items-center justify-between mb-3">
                      <h5 className="text-sm font-bold text-brand-900">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                      <VerdictBadge
                        verdict={c.hiring_decision?.verdict}
                        confidence={c.hiring_decision?.confidence}
                      />
                    </div>
                    {c.hiring_decision?.action_items?.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[10px] font-bold text-brand-700 uppercase tracking-wide">Next Steps</p>
                        {c.hiring_decision.action_items.map((item, i) => (
                          <div key={i} className="flex items-start gap-1.5">
                            <Target className="w-3 h-3 text-brand-500 mt-0.5 shrink-0" />
                            <p className="text-xs text-slate-700">{safeStr(item)}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Dealbreakers */}
            <CollapsibleSection title="Dealbreakers" icon={AlertTriangle}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className={`rounded-xl p-4 ring-1 ${c.dealbreakers?.length > 0 ? 'bg-red-50/70 ring-red-200' : 'bg-green-50/70 ring-green-200'}`}>
                    <div className="flex items-center gap-2 mb-2">
                      {c.dealbreakers?.length > 0 ? (
                        <AlertTriangle className="w-4 h-4 text-red-600" />
                      ) : (
                        <Check className="w-4 h-4 text-green-600" />
                      )}
                      <h5 className="text-sm font-bold text-brand-900">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    </div>
                    {c.dealbreakers?.length > 0 ? (
                      <ul className="space-y-1">
                        {c.dealbreakers.map((d, i) => (
                          <li key={i} className="text-xs text-red-700 flex items-start gap-1.5">
                            <span className="text-red-500 mt-0.5">•</span>
                            {safeStr(d)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-green-700 font-medium">No dealbreakers identified</p>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Differentiators */}
            <CollapsibleSection title="Differentiators" icon={Zap}>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-amber-50/50 rounded-xl p-4 ring-1 ring-amber-200">
                    <div className="flex items-center gap-2 mb-2">
                      <Zap className="w-4 h-4 text-amber-600" />
                      <h5 className="text-sm font-bold text-brand-900">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    </div>
                    {c.differentiators?.length > 0 ? (
                      <ul className="space-y-1">
                        {c.differentiators.map((d, i) => (
                          <li key={i} className="text-xs text-amber-800 flex items-start gap-1.5">
                            <span className="text-amber-500 mt-0.5">★</span>
                            {safeStr(d)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-slate-400 italic">No differentiators listed</p>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Adjacent Skills */}
            <CollapsibleSection title="Adjacent Skills">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-4">
                {comparison.candidates.map(c => (
                  <div key={c.id} className="bg-brand-50/50 rounded-xl p-4 ring-1 ring-brand-100">
                    <h5 className="text-sm font-bold text-brand-900 mb-3">{personLabel(c.candidate_name, c.candidate_email)}</h5>
                    <div className="flex flex-wrap gap-2">
                      {c.adjacent_skills?.length > 0 ? (
                        c.adjacent_skills.map((skill, i) => (
                          <span
                            key={i}
                            className="px-2.5 py-1 text-xs font-medium text-indigo-700 bg-indigo-50 rounded-full ring-1 ring-indigo-200"
                          >
                            {safeStr(skill)}
                          </span>
                        ))
                      ) : (
                        <p className="text-xs text-slate-400 italic">No adjacent skills listed</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </CollapsibleSection>

            {/* Team coverage gap */}
            {teamProfiles.length > 0 && (
              <div className="bg-white/90 dark:bg-slate-800/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 dark:ring-slate-700 shadow-sm p-5">
                <h4 className="text-sm font-bold text-brand-900 mb-3">Team Coverage Gap</h4>
                <select
                  value={selectedTeamProfile}
                  onChange={(e) => setSelectedTeamProfile(e.target.value)}
                  className="w-full max-w-sm px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm mb-3"
                >
                  <option value="">Select team skill profile…</option>
                  {teamProfiles.map((p) => (
                    <option key={p.id} value={p.id}>{p.name || p.team_name || `Profile #${p.id}`}</option>
                  ))}
                </select>
                {selectedTeamProfile && teamGaps.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {teamGaps.map((g, i) => (
                      <span
                        key={i}
                        className="px-2.5 py-1 text-xs font-medium text-amber-800 bg-amber-50 rounded-full ring-1 ring-amber-200"
                      >
                        {typeof g === 'string' ? g : g.skill || g.name}
                      </span>
                    ))}
                  </div>
                ) : selectedTeamProfile ? (
                  <p className="text-xs text-slate-400">No team gaps identified for this profile.</p>
                ) : (
                  <p className="text-xs text-slate-400">Compare candidates against your team&apos;s skill coverage.</p>
                )}
              </div>
            )}

            {/* Skill-Level Comparison */}
            <ComparisonMatrix
              candidateIds={comparison.candidates.map(c => c.candidate_id).filter(Boolean)}
              screeningResultId={comparison.candidates[0]?.id}
              teamGaps={teamGaps.map((g) => (typeof g === 'string' ? g : g.skill || g.name)).filter(Boolean)}
            />
          </div>
        )}
      </main>
    </div>
  )
}
