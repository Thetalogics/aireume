import { Sparkles, FileSearch, Phone, ArrowRight } from 'lucide-react'
import { Badge, Card } from '../ui'
import { ScoreProgression } from './InterviewOutcomeBadges'

const RECOMMENDATION_STYLES = {
  advance_hm: { label: 'Advance to HM', color: 'emerald' },
  strong_advance: { label: 'Strong Advance', color: 'emerald' },
  advance: { label: 'Advance', color: 'green' },
  hold: { label: 'Hold', color: 'amber' },
  reject: { label: 'Not a fit', color: 'red' },
  strong_reject: { label: 'Not a fit', color: 'red' },
}

function scoreColor(score) {
  if (score == null) return 'text-slate-400'
  if (score >= 70) return 'text-emerald-600'
  if (score >= 45) return 'text-amber-600'
  return 'text-red-500'
}

function ScorePill({ label, score, icon: Icon }) {
  return (
    <div className="flex-1 min-w-[120px] rounded-2xl bg-white/80 ring-1 ring-brand-100 p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-4 h-4 text-brand-500" />
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{label}</span>
      </div>
      <p className={`text-3xl font-black tabular-nums ${scoreColor(score)}`}>
        {score != null ? score : '—'}
        {score != null && <span className="text-base font-semibold text-slate-400">/100</span>}
      </p>
    </div>
  )
}

/**
 * Hero showing Analysis score + Call score + Consolidated recommendation.
 */
export default function ConsolidatedScoreHero({
  analysisScore,
  callScore,
  callSource,
  consolidatedRecommendation,
  consolidatedReasoning,
  className = '',
}) {
  const rec = RECOMMENDATION_STYLES[consolidatedRecommendation] || {
    label: consolidatedRecommendation?.replace(/_/g, ' ') || 'Pending',
    color: 'slate',
  }

  const callLabel = callSource === 'human'
    ? 'Live Screen'
    : callSource === 'ai'
      ? 'AI Call'
      : 'Call Score'

  const hasCall = callScore != null

  return (
    <Card className={`overflow-hidden ${className}`}>
      <div className="bg-gradient-to-br from-brand-700 via-brand-600 to-brand-500 p-5 sm:p-6">
        <div className="flex items-center gap-2 mb-4">
          <Sparkles className="w-4 h-4 text-white/90" />
          <h3 className="text-sm font-bold text-white uppercase tracking-wide">Hiring Signal</h3>
        </div>

        <div className="mb-4 rounded-2xl bg-white/15 ring-1 ring-white/25 px-4 py-3">
          <p className="text-[10px] font-semibold text-white/70 uppercase tracking-wider mb-1">Combined score</p>
          <div className="text-2xl sm:text-3xl text-white">
            <ScoreProgression
              analysisScore={analysisScore}
              callScore={callScore}
              callSource={callSource}
              inverted
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          <ScorePill label="Analysis" score={analysisScore} icon={FileSearch} />
          {hasCall ? (
            <ScorePill label={callLabel} score={callScore} icon={Phone} />
          ) : (
            <div className="flex-1 min-w-[120px] rounded-2xl bg-white/10 ring-1 ring-white/20 p-4 flex items-center justify-center">
              <p className="text-xs text-white/80 text-center">Complete a live or AI screen to unlock call score</p>
            </div>
          )}
        </div>

        {consolidatedRecommendation && (
          <div className="flex flex-wrap items-center gap-2">
            <Badge color={rec.color} className="text-sm font-bold px-3 py-1">
              Recommend: {rec.label}
            </Badge>
            {hasCall && (
              <span className="inline-flex items-center gap-1 text-xs text-white/80">
                <ArrowRight className="w-3 h-3" />
                Consolidated from analysis + call
              </span>
            )}
          </div>
        )}
      </div>

      {consolidatedReasoning && (
        <div className="px-5 py-4 bg-brand-50/80 border-t border-brand-100">
          <p className="text-sm text-slate-700 leading-relaxed">{consolidatedReasoning}</p>
        </div>
      )}
    </Card>
  )
}
