/** Person line for lists. Skill dumps and resume fragments are not names. */
export function personLabel(name, email) {
  const n = String(name || '').replace(/\s+/g, ' ').trim()
  const mail = String(email || '').trim()
  const commas = (n.match(/,/g) || []).length
  const junk = !n || commas >= 2 || n.length <= 3 || /present employer|^\d{1,2}\//i.test(n)
  if (junk) return mail || 'Candidate'
  return n
}
