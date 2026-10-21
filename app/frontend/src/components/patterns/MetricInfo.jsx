import { Info } from 'lucide-react'
import { useMetricGlossary } from './useMetricGlossary'

export default function MetricInfo({ metricKey, label, className = '' }) {
  const metrics = useMetricGlossary()
  const entry = metrics.find((m) => m.key === metricKey)

  if (!entry) {
    return label ? <span className={className}>{label}</span> : null
  }

  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <span>{label || entry.label}</span>
      <span className="group relative inline-flex">
        <Info className="w-3.5 h-3.5 text-slate-400 cursor-help" aria-hidden />
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 rounded-lg bg-slate-900 text-white text-xs p-3 opacity-0 group-hover:opacity-100 transition-opacity z-20 shadow-lg"
        >
          <span className="font-semibold block mb-1">{entry.label}</span>
          {entry.definition}
          {entry.caveats && (
            <span className="block mt-1 text-slate-300">{entry.caveats}</span>
          )}
        </span>
      </span>
    </span>
  )
}
