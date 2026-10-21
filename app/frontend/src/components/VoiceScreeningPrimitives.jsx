import { STATUS_CONFIG } from './voiceScreeningConstants'

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.scheduled
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${cfg.color}`}>
      <Icon className="w-3 h-3" />
      {cfg.label}
    </span>
  )
}

export function Section({ title, icon: Icon, children, description, action }) {
  return (
    <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6 card-animate">
      <div className="flex items-start justify-between mb-5">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-2xl bg-brand-50 ring-1 ring-brand-100 flex items-center justify-center shrink-0">
            <Icon className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <h3 className="font-extrabold text-brand-900 text-lg tracking-tight">{title}</h3>
            {description && <p className="text-sm text-slate-500 mt-0.5">{description}</p>}
          </div>
        </div>
        {action}
      </div>
      {children}
    </div>
  )
}

export function Field({ label, children, hint }) {
  return (
    <div>
      <label htmlFor="voicescreeningpage-field-1" className="block text-sm font-semibold text-slate-700 mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-xs text-slate-400 mt-1">{hint}</p>}
    </div>
  )
}

export function TextInput({ value, onChange, placeholder, type = 'text' }) {
  return (
    <input id="voicescreeningpage-field-1"
      type={type}
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent text-sm transition-all outline-none"
    />
  )
}

export function Select({ value, onChange, options }) {
  return (
    <select
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent text-sm transition-all outline-none appearance-none"
    >
      {options.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

export function DayPicker({ value, onChange }) {
  const days = value || [1, 2, 3, 4, 5]
  function toggle(dayIdx) {
    const next = days.includes(dayIdx)
      ? days.filter(d => d !== dayIdx)
      : [...days, dayIdx].sort()
    onChange(next)
  }
  return (
    <div className="flex gap-1.5">
      {DAY_NAMES.map((name, idx) => {
        const dayNum = idx + 1
        const active = days.includes(dayNum)
        return (
          <button
            key={idx}
            onClick={() => toggle(dayNum)}
            className={`w-9 h-9 rounded-lg text-xs font-bold transition-all ${
              active
                ? 'bg-brand-600 text-white shadow-sm'
                : 'bg-slate-100 text-slate-400 hover:bg-slate-200'
            }`}
          >
            {name}
          </button>
        )
      })}
    </div>
  )
}
