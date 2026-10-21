export function formatCurrency(amountCents, currency = 'usd') {
  const symbols = { usd: '$', eur: '€', gbp: '£' }
  const symbol = symbols[currency?.toLowerCase()] || '$'
  return `${symbol}${((amountCents || 0) / 100).toFixed(2)}`
}

export function formatDate(dateStr) {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
