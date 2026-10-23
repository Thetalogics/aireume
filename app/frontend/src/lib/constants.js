/**
 * Centralized constants for the ARIA frontend application.
 *
 * Contains status configurations, score color mappings, recommendation labels,
 * and pipeline stage definitions shared across CandidatesPage and RequisitionDetailPage,
 * KanbanBoard, and other candidate-facing views.
 */

import {
  Clock,
  CheckCircle2,
  XCircle,
  Search,
  UserCheck,
  AlertCircle,
  AlertTriangle,
} from 'lucide-react'

/* ────────────────────────────────────────────────────────────────
   Status configuration
   ─────────────────────────────────────────────────────────────── */

/**
 * Ordered list of candidate statuses used for filters, dropdowns, and bulk actions.
 * @type {string[]}
 */
export const STATUS_OPTIONS = ['pending', 'shortlisted', 'rejected', 'in-review', 'hired']

/**
 * Full configuration for each candidate status.
 * Includes display label, Tailwind color classes for badges, a dot color for
 * inline indicators, and the associated Lucide icon component.
 *
 * @type {Record<string, {
 *   label: string,
 *   color: string,
 *   dotColor: string,
 *   icon: import('react').ComponentType
 * }>}
 */
export const STATUS_CONFIG = {
  pending: {
    label: 'Pending',
    color: 'bg-slate-100 text-slate-700 ring-slate-200',
    dotColor: 'bg-slate-400',
    icon: Clock,
  },
  shortlisted: {
    label: 'Shortlisted',
    color: 'bg-green-100 text-green-700 ring-green-200',
    dotColor: 'bg-green-500',
    icon: CheckCircle2,
  },
  rejected: {
    label: 'Rejected',
    color: 'bg-red-100 text-red-700 ring-red-200',
    dotColor: 'bg-red-500',
    icon: XCircle,
  },
  'in-review': {
    label: 'In Review',
    color: 'bg-amber-100 text-amber-700 ring-amber-200',
    dotColor: 'bg-amber-500',
    icon: Search,
  },
  hired: {
    label: 'Hired',
    color: 'bg-emerald-100 text-emerald-700 ring-emerald-200',
    dotColor: 'bg-emerald-500',
    icon: UserCheck,
  },
}

/* ────────────────────────────────────────────────────────────────
   Score thresholds & color mapping
   ─────────────────────────────────────────────────────────────── */

/**
 * Score threshold ranges for categorizing candidate fit scores.
 * @type {Record<string, { min: number, max: number }>}
 */
export const SCORE_THRESHOLDS = {
  HIGH:   { min: 80, max: 100 },
  MEDIUM: { min: 60, max: 79 },
  LOW:    { min: 40, max: 59 },
  POOR:   { min: 0,  max: 39 },
}

/**
 * Tailwind color classes mapped to each score threshold tier.
 * @type {Record<string, { bg: string, text: string, border: string, ring: string }>}
 */
export const SCORE_COLORS = {
  high: {
    bg: 'bg-green-50',
    text: 'text-green-700',
    border: 'border-green-200',
    ring: 'ring-green-200',
  },
  medium: {
    bg: 'bg-amber-50',
    text: 'text-amber-700',
    border: 'border-amber-200',
    ring: 'ring-amber-200',
  },
  low: {
    bg: 'bg-orange-50',
    text: 'text-orange-700',
    border: 'border-orange-200',
    ring: 'ring-orange-200',
  },
  poor: {
    bg: 'bg-red-50',
    text: 'text-red-700',
    border: 'border-red-200',
    ring: 'ring-red-200',
  },
}

/**
 * Human-readable recommendation labels mapped to score threshold tiers.
 * @type {Record<string, string>}
 */
export const RECOMMENDATION_LABELS = {
  high:   'Strong Match',
  medium: 'Consider',
  low:    'Weak Match',
  poor:   'Not Recommended',
}

/** Display label for a stored recommendation. Status "Rejected" stays a status. */
export function recommendationLabel(value) {
  if (value === 'Reject') return 'Not a fit'
  if (value === 'Shortlist') return 'Advance'
  if (value == null || value === '') return '—'
  return String(value)
}

/**
 * Returns the color configuration for a given numeric score.
 *
 * @param {number|null|undefined} score - The candidate fit score.
 * @returns {{ bg: string, text: string, border: string, ring: string }|null}
 *   The Tailwind color config object, or `null` when the score is missing.
 */
export function getScoreColor(score) {
  if (score == null) return null
  if (score >= SCORE_THRESHOLDS.HIGH.min)   return SCORE_COLORS.high
  if (score >= SCORE_THRESHOLDS.MEDIUM.min) return SCORE_COLORS.medium
  if (score >= SCORE_THRESHOLDS.LOW.min)    return SCORE_COLORS.low
  return SCORE_COLORS.poor
}

/**
 * Returns a raw hex color for a given numeric score, using the same tier
 * thresholds as getScoreColor. Useful for SVG stroke/fill where Tailwind
 * classes cannot be applied (e.g. gauges, charts).
 *
 * @param {number|null|undefined} score
 * @returns {string} hex color
 */
export function getScoreHexColor(score) {
  if (score == null) return '#94a3b8' // slate-400
  if (score >= SCORE_THRESHOLDS.HIGH.min)   return '#22c55e' // green-500
  if (score >= SCORE_THRESHOLDS.MEDIUM.min) return '#f59e0b' // amber-500
  if (score >= SCORE_THRESHOLDS.LOW.min)    return '#fb923c' // orange-400
  return '#ef4444' // red-500
}

/**
 * Returns a recommendation descriptor for a given numeric score.
 *
 * @param {number|null|undefined} score - The candidate fit score.
 * @returns {{
 *   label: string,
 *   color: string,
 *   icon: import('react').ComponentType | null
 * }}
 *   An object with a human-readable label, Tailwind text color class, and icon.
 */
export function getRecommendation(score) {
  if (score == null) {
    return { label: '—', color: 'text-slate-400', icon: null }
  }
  if (score >= SCORE_THRESHOLDS.HIGH.min) {
    return {
      label: RECOMMENDATION_LABELS.high,
      color: 'text-green-700',
      icon: CheckCircle2,
    }
  }
  if (score >= SCORE_THRESHOLDS.MEDIUM.min) {
    return {
      label: RECOMMENDATION_LABELS.medium,
      color: 'text-amber-700',
      icon: AlertCircle,
    }
  }
  if (score >= SCORE_THRESHOLDS.LOW.min) {
    return {
      label: RECOMMENDATION_LABELS.low,
      color: 'text-orange-700',
      icon: AlertTriangle,
    }
  }
  return {
    label: RECOMMENDATION_LABELS.poor,
    color: 'text-red-700',
    icon: XCircle,
  }
}

/**
 * Report-facing fit tier label (aligned with SCORE_THRESHOLDS).
 */
export function getFitTierLabel(score) {
  const rec = getRecommendation(score)
  if (score == null) return { label: '—', className: 'bg-slate-100 text-slate-600' }
  if (score >= SCORE_THRESHOLDS.HIGH.min) {
    return { label: 'Strong Fit', className: 'bg-emerald-100 text-emerald-700' }
  }
  if (score >= SCORE_THRESHOLDS.MEDIUM.min) {
    return { label: 'Moderate Fit', className: 'bg-amber-100 text-amber-700' }
  }
  if (score >= SCORE_THRESHOLDS.LOW.min) {
    return { label: 'Low Fit', className: 'bg-orange-100 text-orange-700' }
  }
  return { label: 'Poor Fit', className: 'bg-red-100 text-red-700' }
}


/**
 * Ordered pipeline stages used for Kanban board columns.
 * @type {string[]}
 */
export const PIPELINE_STAGES = ['pending', 'in-review', 'shortlisted', 'rejected', 'hired']

/**
 * Industry options presented during tenant onboarding.
 * @type {string[]}
 */
export const INDUSTRIES = ['Technology', 'Healthcare', 'Finance', 'Legal', 'Manufacturing', 'Other']

/**
 * Company size buckets presented during tenant onboarding.
 * @type {string[]}
 */
export const COMPANY_SIZES = ['1-10', '11-50', '51-200', '201-500', '500+']
