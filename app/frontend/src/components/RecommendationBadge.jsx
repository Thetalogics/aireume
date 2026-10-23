import { getRecommendation, getScoreColor, recommendationLabel } from '../lib/constants'

/**
 * Pill-shaped recommendation badge.
 *
 * Props:
 * - score: number (0-100) — derives recommendation from score
 * - recommendation: string (optional override) — "Strong Match", "Consider", etc.
 * - size: 'sm' | 'md' — default 'sm'
 * - className: string
 */
export default function RecommendationBadge({
  score,
  recommendation,
  size = 'sm',
  className = '',
}) {
  const rec = getRecommendation(score)
  const colorConfig = getScoreColor(score)

  const label = recommendationLabel(recommendation || rec.label)
  const Icon = rec.icon

  const sizeClasses = {
    sm: 'text-xs px-3 py-1',
    md: 'text-sm px-4 py-1.5',
  }

  return (
    <span
      aria-label={`Recommendation: ${label}`}
      className={`
        inline-flex items-center gap-1.5 rounded-full font-medium
        ${colorConfig?.bg || 'bg-slate-50'}
        ${colorConfig?.text || 'text-slate-700'}
        ${sizeClasses[size] || sizeClasses.sm}
        ${className}
      `}
    >
      {Icon && <Icon className="w-3.5 h-3.5" />}
      <span>{label}</span>
    </span>
  )
}
