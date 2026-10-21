import { DEFAULT_WEIGHTS } from './universalWeights'
import { useState, useEffect } from 'react'
import { SlidersHorizontal, RotateCcw, Save, AlertCircle, Check } from 'lucide-react'


const WEIGHT_PRESETS = {
  Balanced: {
    core_competencies: 0.30,
    experience: 0.20,
    domain_fit: 0.20,
    education: 0.10,
    career_trajectory: 0.10,
    role_excellence: 0.10,
    risk: -0.10,
  },
  'Skill-Heavy': {
    core_competencies: 0.40,
    experience: 0.20,
    domain_fit: 0.15,
    education: 0.05,
    career_trajectory: 0.10,
    role_excellence: 0.10,
    risk: -0.10,
  },
  'Experience-Heavy': {
    core_competencies: 0.25,
    experience: 0.35,
    domain_fit: 0.15,
    education: 0.05,
    career_trajectory: 0.10,
    role_excellence: 0.10,
    risk: -0.10,
  },
  'Domain-Focused': {
    core_competencies: 0.25,
    experience: 0.20,
    domain_fit: 0.30,
    education: 0.05,
    career_trajectory: 0.10,
    role_excellence: 0.10,
    risk: -0.10,
  },
}



export default function UniversalWeightsPanel({ 
  weights, 
  onChange, 
  roleCategory = null,
  roleExcellenceLabel = null,
  className = '' 
}) {
  const [preset, setPreset] = useState('Custom')
  const [total, setTotal] = useState(100)
  const [showValidation, setShowValidation] = useState(false)

  // Calculate total (excluding risk penalty)
  useEffect(() => {
    const positiveWeights = Object.entries(weights)
      .filter(([key]) => key !== 'risk')
      .reduce((sum, [, value]) => sum + (value || 0), 0)
    setTotal(Math.round(positiveWeights * 100))
  }, [weights])

  const applyPreset = (name) => {
    setPreset(name)
    onChange(WEIGHT_PRESETS[name])
    setShowValidation(false)
  }

  const updateWeight = (key, value) => {
    const val = parseFloat(value) / 100
    onChange({ ...weights, [key]: val })
    setPreset('Custom')
    setShowValidation(true)
  }

  const resetToDefaults = () => {
    onChange(DEFAULT_WEIGHTS)
    setPreset('Balanced')
    setShowValidation(false)
  }

  const getAdaptiveLabel = (key) => {
    // Universal labels
    const universalLabels = {
      experience: 'Experience Level',
      education: 'Education & Credentials',
      career_trajectory: 'Career Progression',
      risk: 'Risk Assessment',
    }

    if (universalLabels[key]) return universalLabels[key]

    // Adaptive labels based on role category
    if (key === 'core_competencies') {
      const labels = {
        technical: 'Tech Stack Match',
        sales: 'Sales Competencies',
        hr: 'HR Competencies',
        marketing: 'Marketing Competencies',
        operations: 'Operations Skills',
        leadership: 'Leadership Competencies',
      }
      return labels[roleCategory?.toLowerCase()] || 'Core Competencies'
    }

    if (key === 'domain_fit') {
      const labels = {
        technical: 'Technical Domain',
        sales: 'Sales Domain',
        hr: 'HR Specialization',
        marketing: 'Marketing Channel',
        operations: 'Operations Domain',
        leadership: 'Industry Expertise',
      }
      return labels[roleCategory?.toLowerCase()] || 'Domain/Industry Fit'
    }

    if (key === 'role_excellence') {
      // Use custom label if provided
      if (roleExcellenceLabel) return roleExcellenceLabel
      
      const labels = {
        technical: 'System Design & Architecture',
        sales: 'Revenue Achievement',
        hr: 'Strategic HR Impact',
        marketing: 'Campaign Strategy',
        operations: 'Process Optimization',
        leadership: 'Strategic Vision',
      }
      return labels[roleCategory?.toLowerCase()] || 'Role Excellence'
    }

    return key
  }

  const getTooltip = (key) => {
    const tooltips = {
      core_competencies: 'Essential skills and competencies required for this role',
      experience: 'Years of experience and seniority level in the role',
      domain_fit: 'Relevant domain or industry expertise',
      education: 'Degrees, certifications, and continuous learning',
      career_trajectory: 'Career growth trajectory, job stability, and progression patterns',
      role_excellence: 'Role-specific differentiator and excellence indicator',
      risk: 'Penalty for red flags, gaps, and inconsistencies',
    }
    return tooltips[key] || ''
  }

  const isValidTotal = total >= 98 && total <= 102

  return (
    <div className={`bg-white border border-slate-200 rounded-2xl shadow-sm ${className}`}>
      {/* Header */}
      <div className="p-4 border-b border-slate-200 bg-slate-50 rounded-t-2xl">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-slate-600" />
            <p className="text-xs font-semibold text-slate-800 uppercase tracking-wide">
              Scoring Weights
            </p>
            {roleCategory && (
              <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs font-semibold rounded">
                {roleCategory.toUpperCase()}
              </span>
            )}
          </div>
          <div className="flex gap-1 flex-wrap">
            {Object.keys(WEIGHT_PRESETS).map(name => (
              <button
                key={name}
                onClick={() => applyPreset(name)}
                className={`px-2.5 py-1 text-xs rounded-lg font-medium transition-all ${
                  preset === name
                    ? 'bg-indigo-600 text-white shadow-md'
                    : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:ring-indigo-200 hover:text-indigo-700'
                }`}
              >
                {name}
              </button>
            ))}
            <button
              onClick={resetToDefaults}
              className="p-1.5 text-slate-600 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-all"
              title="Reset to defaults"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Weights Grid */}
      <div className="p-4 space-y-4">
        {/* Positive Weights */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Object.entries(weights)
            .filter(([key]) => key !== 'risk')
            .map(([key, value]) => (
              <div key={key} className="space-y-2">
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-xs font-semibold text-slate-700">
                      {getAdaptiveLabel(key)}
                    </span>
                    <p className="text-xs text-slate-500 mt-0.5" title={getTooltip(key)}>
                      {getTooltip(key).substring(0, 50)}...
                    </p>
                  </div>
                  <span className="text-sm font-bold text-indigo-700 ml-2">
                    {Math.round((value || 0) * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="5"
                  value={Math.round((value || 0) * 100)}
                  onChange={(e) => updateWeight(key, e.target.value)}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                />
              </div>
            ))}
        </div>

        {/* Risk Penalty */}
        <div className="pt-4 border-t border-slate-200">
          <div className="space-y-2">
            <div className="flex justify-between items-start">
              <div>
                <span className="text-xs font-semibold text-red-700">
                  {getAdaptiveLabel('risk')}
                </span>
                <p className="text-xs text-slate-500 mt-0.5">
                  Penalty for red flags, gaps, and inconsistencies
                </p>
              </div>
              <span className="text-sm font-bold text-red-700 ml-2">
                {Math.round(Math.abs(weights.risk || 0) * 100)}%
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="20"
              step="5"
              value={Math.round(Math.abs(weights.risk || 0) * 100)}
              onChange={(e) => updateWeight('risk', -parseFloat(e.target.value))}
              className="w-full h-2 bg-red-100 rounded-lg appearance-none cursor-pointer accent-red-600"
            />
          </div>
        </div>

        {/* Validation */}
        {showValidation && (
          <div className={`p-3 rounded-lg border ${
            isValidTotal 
              ? 'bg-green-50 border-green-200' 
              : 'bg-yellow-50 border-yellow-200'
          }`}>
            <div className="flex items-start gap-2">
              {isValidTotal ? (
                <Check className="w-4 h-4 text-green-600 mt-0.5 flex-shrink-0" />
              ) : (
                <AlertCircle className="w-4 h-4 text-yellow-600 mt-0.5 flex-shrink-0" />
              )}
              <div>
                <p className={`text-xs font-semibold ${
                  isValidTotal ? 'text-green-800' : 'text-yellow-800'
                }`}>
                  {isValidTotal ? 'Weights are balanced' : 'Weights should sum to 100%'}
                </p>
                <p className={`text-xs mt-1 ${
                  isValidTotal ? 'text-green-600' : 'text-yellow-600'
                }`}>
                  Current total: {total}% (excluding risk penalty)
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
