import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { recommendationLabel } from '../../lib/constants'

const RING = 2 * Math.PI * 46
const EASE = [0.22, 1, 0.36, 1]

function labelOf(skill) {
  if (skill == null) return ''
  if (typeof skill === 'string') return skill.trim()
  return String(skill.skill || skill.name || '').trim()
}

function mustHaveRows(result) {
  const analysis = result?.skill_analysis || {}
  let matched = analysis.matched_required || []
  let missing = analysis.missing_required || []
  if (matched.length + missing.length === 0) {
    const required = result?.jd_analysis?.required_skills || analysis.required_skills || []
    const flat = analysis.matched_skills || result?.matched_skills || []
    const found = new Set(flat.map((skill) => labelOf(skill).toLowerCase()))
    if (Array.isArray(required) && required.length > 0) {
      matched = required.filter((skill) => found.has(labelOf(skill).toLowerCase()))
      missing = required.filter((skill) => !found.has(labelOf(skill).toLowerCase()))
    }
  }
  return [
    ...matched.map((skill) => ({ name: labelOf(skill), found: true })),
    ...missing.map((skill) => ({ name: labelOf(skill), found: false })),
  ].filter((row) => row.name)
}

function yearsOf(result) {
  const exp = result?.score_breakdown?.experience_match
  const actual = typeof exp === 'object' && exp
    ? exp.actual_years
    : result?.candidate_profile?.years_experience
  const required = typeof exp === 'object' && exp
    ? exp.required_years
    : (result?.jd_analysis?.required_years ?? result?.required_years)
  const a = Number(actual)
  const r = Number(required)
  if (!Number.isFinite(a) && !Number.isFinite(r)) return null
  return {
    actual: Number.isFinite(a) ? a : null,
    required: Number.isFinite(r) ? r : null,
    explanation: typeof exp === 'object' && exp ? exp.explanation : '',
  }
}

function listOf(result, key) {
  const direct = result?.[key]
  if (Array.isArray(direct)) return direct.map((item) => String(item)).filter(Boolean)
  const nested = result?.narrative?.[key]
  if (Array.isArray(nested)) return nested.map((item) => String(item)).filter(Boolean)
  return null
}

/**
 * The report's fit graphic. Motion runs once when the report opens.
 */
export default function ReportFitGraphic({ result }) {
  const reduce = useReducedMotion()
  const score = Number(result?.fit_score ?? result?.deterministic_score)
  const [shown, setShown] = useState(Number.isFinite(score) ? score : 0)

  useEffect(() => {
    if (!Number.isFinite(score)) return undefined
    if (reduce !== false) {
      setShown(score)
      return undefined
    }
    let frame
    const start = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - start - 120) / 800)
      const eased = t < 0 ? 0 : 1 - Math.pow(1 - t, 3)
      setShown(t < 0 ? 0 : Math.round(score * eased))
      if (t < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [reduce, score])

  if (!Number.isFinite(score)) return null

  const rows = mustHaveRows(result)
  const years = yearsOf(result)
  const span = years
    ? Math.max(10, Math.ceil(Math.max(years.actual || 0, years.required || 0) / 5) * 5)
    : 10
  const dealbreakers = listOf(result, 'dealbreakers')
  const differentiators = listOf(result, 'differentiators')
  const verdict = recommendationLabel(result?.hiring_decision?.verdict || result?.final_recommendation)
  const missing = rows.find((row) => !row.found)
  const concern = missing?.name || (Array.isArray(result?.concerns) ? result.concerns[0] : '')
  const action = result?.hiring_decision?.action_items?.[0]
  const summary = result?.fit_summary || result?.narrative?.fit_summary || ''
  const offset = RING - RING * (Math.max(0, Math.min(100, score)) / 100)
  const still = reduce !== false
  const reveal = { duration: still ? 0 : 0.64, ease: EASE }

  return (
    <section className="space-y-3" data-ui="report-fit" aria-label={`Score ${score} of 100`}>
      <div className="bg-white rounded-2xl p-4 sm:p-5 grid grid-cols-1 sm:grid-cols-[132px_1fr] gap-4 items-center">
        <svg className="w-[120px] h-[120px]" viewBox="0 0 120 120" aria-hidden="true">
          <circle cx="60" cy="60" r="46" fill="none" stroke="#F4E4C4" strokeWidth="14" />
          <motion.circle
            cx="60" cy="60" r="46" fill="none" stroke="#3B82F6" strokeWidth="14"
            strokeDasharray={RING}
            initial={{ strokeDashoffset: still ? offset : RING }}
            animate={{ strokeDashoffset: offset }}
            transition={{ duration: still ? 0 : 0.8, ease: EASE, delay: still ? 0 : 0.12 }}
            transform="rotate(-90 60 60)"
          />
          <text x="60" y="66" textAnchor="middle" fontSize="28" fontWeight="800" fill="currentColor">{shown}</text>
        </svg>
        <div>
          <p className="text-5xl font-extrabold tracking-tight text-brand-900 leading-none">
            <span aria-hidden="true">{shown}</span>
            <span className="text-base font-semibold text-brand-600"> / 100</span>
          </p>
          <div className="flex flex-wrap gap-2 my-3">
            {verdict && verdict !== '—' && (
              <motion.span className="px-3 py-1.5 rounded-full text-sm font-bold bg-brand-600 text-white" initial={still ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={reveal}>
                {verdict}
              </motion.span>
            )}
            {concern && (
              <motion.span className="px-3 py-1.5 rounded-full text-sm font-bold bg-amber-100 text-amber-800" initial={still ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ ...reveal, delay: 0.08 }}>
                {missing ? `${concern} gap` : concern}
              </motion.span>
            )}
            {action && (
              <motion.span className="px-3 py-1.5 rounded-full text-sm font-bold bg-brand-100 text-brand-700" initial={still ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ ...reveal, delay: 0.16 }}>
                {action}
              </motion.span>
            )}
          </div>
          {summary && <p className="text-base text-brand-900 leading-relaxed max-w-[46ch]">{summary}</p>}
        </div>
      </div>

      {(rows.length > 0 || years) && (
        <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_0.8fr] gap-3">
          {rows.length > 0 && (
            <div className="bg-white rounded-2xl p-4 sm:p-5">
              <h2 className="text-sm font-bold text-brand-900 mb-2">Asked, and what the resume showed</h2>
              <ul>
                {rows.map((row, index) => (
                  <motion.li
                    key={`${row.name}-${index}`}
                    className="grid grid-cols-[14px_1fr_auto] gap-3 items-center py-2 border-t border-brand-100 first:border-t-0"
                    initial={still ? false : { opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ ...reveal, delay: index * 0.08 }}
                  >
                    <span className={`w-3.5 h-3.5 rounded ${row.found ? 'bg-brand-500' : 'bg-amber-500'}`} aria-hidden="true" />
                    <span className="text-sm font-semibold text-brand-900">{row.name}</span>
                    <span className={`text-xs font-bold rounded-full px-2.5 py-1 ${row.found ? 'bg-brand-100 text-brand-700' : 'bg-amber-100 text-amber-800'}`}>
                      {row.found ? 'Found' : 'Missing'}
                    </span>
                  </motion.li>
                ))}
              </ul>
            </div>
          )}
          {years && (
            <div className="bg-white rounded-2xl p-4 sm:p-5">
              <h2 className="text-sm font-bold text-brand-900 mb-3">Years against the bar</h2>
              <div className="relative h-24">
                <div className="absolute left-0 right-0 top-11 h-2.5 rounded-full bg-brand-100 overflow-hidden">
                  {years.actual != null && (
                    <motion.div
                      className="h-full bg-brand-500 origin-left"
                      initial={{ scaleX: still ? years.actual / span : 0 }}
                      animate={{ scaleX: years.actual / span }}
                      transition={{ duration: still ? 0 : 0.8, ease: EASE, delay: still ? 0 : 0.7 }}
                    />
                  )}
                </div>
                {years.required != null && (
                  <div className="absolute top-1 text-center w-20" style={{ left: `${(years.required / span) * 100}%`, transform: 'translateX(-50%)' }}>
                    <b className="block text-xs font-bold text-brand-900">Bar, {years.required} years</b>
                    <i className="block w-0.5 h-7 bg-brand-900 mx-auto mt-1" />
                  </div>
                )}
                {years.actual != null && (
                  <motion.div
                    className="absolute top-5"
                    style={{ left: `${(years.actual / span) * 100}%` }}
                    initial={still ? { opacity: 1, x: '-50%' } : { opacity: 0, x: 'calc(-50% - 36px)' }}
                    animate={{ opacity: 1, x: '-50%' }}
                    transition={{ duration: still ? 0 : 0.8, ease: EASE, delay: still ? 0 : 0.7 }}
                  >
                    <span className="block bg-brand-600 text-white text-xs font-bold rounded-lg px-2 py-1">{years.actual} years</span>
                  </motion.div>
                )}
                <div className="absolute left-0 right-0 top-16 flex justify-between text-xs text-brand-800">
                  <span>0</span>
                  <span>{span} years</span>
                </div>
              </div>
              <p className="text-sm text-brand-900 mt-2">
                {years.explanation || (years.actual != null && years.required != null
                  ? `${years.actual} years against a ${years.required}-year bar.`
                  : '')}
              </p>
            </div>
          )}
        </div>
      )}

      {(dealbreakers || differentiators) && (
        <div className="bg-white rounded-2xl p-4 sm:p-5 flex flex-col sm:flex-row sm:justify-between gap-3 text-sm text-brand-900">
          {dealbreakers && (
            <p>
              <strong>{dealbreakers.length === 0 ? 'No deal-breakers.' : 'Deal-breakers.'}</strong>
              {' '}
              {dealbreakers.length === 0 ? 'Nothing in the resume blocks the role.' : dealbreakers.join(', ')}
            </p>
          )}
          {differentiators && differentiators.length > 0 && (
            <p><strong>{differentiators[0]}.</strong> Named as a differentiator, not a must-have.</p>
          )}
        </div>
      )}
    </section>
  )
}
