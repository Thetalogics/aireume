export const DEFAULT_WEIGHTS = {
  core_competencies: 0.30,
  experience: 0.20,
  domain_fit: 0.20,
  education: 0.10,
  career_trajectory: 0.10,
  role_excellence: 0.10,
  risk: -0.10,
}

export function isValidWeightTotal(weights) {
  if (!weights || typeof weights !== 'object') return false
  const positive = Object.entries(weights)
    .filter(([key]) => key !== 'risk')
    .reduce((sum, [, value]) => sum + (Number(value) || 0), 0)
  return positive >= 0.98 && positive <= 1.02
}
