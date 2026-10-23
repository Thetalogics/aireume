import { describe, it, expect } from 'vitest'
import { personLabel } from '../lib/personName'

describe('personLabel', () => {
  it('keeps a normal name', () => {
    expect(personLabel('Ada Lovelace', 'ada@example.com')).toBe('Ada Lovelace')
  })

  it('replaces a comma-separated skill list with the email', () => {
    expect(personLabel('Python, SQL, Excel, Java', 'ada@example.com')).toBe('ada@example.com')
  })

  it('falls back when both name and email are empty', () => {
    expect(personLabel('', '')).toBe('Candidate')
  })
})
