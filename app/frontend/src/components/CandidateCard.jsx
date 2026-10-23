import { Check, AlertTriangle } from 'lucide-react'
import { personLabel } from '../lib/personName'
import ScoreBadge from './ScoreBadge'
import RecommendationBadge from './RecommendationBadge'
import QuickActions from './QuickActions'
import InterviewOutcomeBadges, { ScoreProgression } from './patterns/InterviewOutcomeBadges'

/**
 * Complete candidate card for list display — 30-second review design.
 *
 * Props:
 * - candidate: object { id, name, email, title, fit_score, status, skills, highlights, job_title }
 * - onStatusChange: function(id, newStatus)
 * - onSelect: function(candidate) — for navigation
 * - selected: boolean — show selection ring
 * - onMouseEnter: function
 * - onMouseLeave: function
 * - className: string
 */
export default function CandidateCard({
  candidate,
  onStatusChange,
  onSelect,
  selected = false,
  onMouseEnter,
  onMouseLeave,
  readOnly = false,
  className = '',
}) {
  const {
    id,
    name,
    email,
    title,
    fit_score,
    call_fit_score,
    call_source,
    consolidated_recommendation,
    status,
    skills = [],
    highlights = [],
    job_title,
  } = candidate || {}

  const topSkills = skills.slice(0, 3)
  const extraSkills = Math.max(0, skills.length - 3)
  const topHighlights = highlights.slice(0, 2)

  return (
    <div
      className={`
        flex flex-col h-full min-h-[280px]
        bg-white rounded-2xl shadow-sm ring-1 ring-brand-100
        transition-all duration-200
        hover:shadow-brand-md hover:ring-brand-200
        ${selected ? 'ring-2 ring-brand-500' : ''}
        ${className}
      `}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* Card header — fixed min height for grid alignment */}
      <button
        type="button"
        className="p-4 cursor-pointer flex-1 flex flex-col min-h-[140px] w-full text-left bg-transparent border-0"
        onClick={() => onSelect?.(candidate)}
      >
        <div className="flex items-start gap-3">
          {call_fit_score != null ? (
            <div className="shrink-0 pt-1">
              <ScoreProgression
                analysisScore={fit_score}
                callScore={call_fit_score}
                callSource={call_source}
                compact
              />
            </div>
          ) : (
            <ScoreBadge score={fit_score} size="md" animated />
          )}

          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-2 min-h-[52px]">
              <div>
                <h3 className="text-base font-semibold text-slate-900 truncate">
                  {personLabel(name, email)}
                </h3>
                {title && (
                  <p className="text-sm text-slate-500 mt-0.5">{title}</p>
                )}
                {job_title && (
                  <p className="text-xs text-slate-400 mt-0.5">
                    Applied: {job_title}
                  </p>
                )}
                {email && !title && !job_title && (
                  <p className="text-sm text-slate-500 mt-0.5">{email}</p>
                )}
              </div>
              <RecommendationBadge score={fit_score} size="sm" />
            </div>
            {(call_fit_score != null || consolidated_recommendation) && call_fit_score == null && (
              <InterviewOutcomeBadges
                analysisScore={fit_score}
                callScore={call_fit_score}
                callSource={call_source}
                consolidatedRecommendation={consolidated_recommendation}
                className="mt-2"
              />
            )}
          </div>
        </div>

        {/* Highlights */}
        {topHighlights.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {topHighlights.map((highlight, idx) => {
              const isConcern = highlight.type === 'concern'
              return (
                <div key={idx} className="flex items-start gap-2">
                  {isConcern ? (
                    <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                  ) : (
                    <Check className="w-4 h-4 text-emerald-500 mt-0.5 shrink-0" />
                  )}
                  <span
                    className={`text-sm leading-relaxed ${
                      isConcern ? 'text-amber-700' : 'text-slate-600'
                    }`}
                  >
                    {highlight.text}
                  </span>
                </div>
              )
            })}
          </div>
        )}

        {/* Skills */}
        {topSkills.length > 0 && (
          <div className="mt-auto pt-3 flex flex-wrap items-center gap-3 min-h-[48px]">
            {topSkills.map((skill, idx) => (
              <div key={idx} className="flex items-center gap-2 min-w-0 flex-1 basis-[40%]">
                <span className="text-xs font-medium text-slate-600 truncate max-w-[72px]">
                  {skill.name}
                </span>
                <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden max-w-[72px]">
                  <div
                    className="h-full rounded-full bg-gradient-brand"
                    style={{ width: `${Math.min(skill.score || 0, 100)}%` }}
                  />
                </div>
              </div>
            ))}
            {extraSkills > 0 && (
              <span className="text-[10px] font-semibold text-slate-400">+{extraSkills} more</span>
            )}
          </div>
        )}
      </button>

      {/* Quick actions footer — pinned to bottom */}
      <div className="px-4 pb-4 pt-2 mt-auto border-t border-brand-50">
        <QuickActions
          candidateId={id}
          currentStatus={status}
          onStatusChange={onStatusChange}
          readOnly={readOnly}
        />
      </div>
    </div>
  )
}
