import { describe, expect, it } from 'vitest'
import { createOperationId } from './createOperationId'

describe('createOperationId', () => {
  it('creates a stable-format key once per call', () => {
    const a = createOperationId('voice')
    const b = createOperationId('voice')
    expect(a).toMatch(/^voice-/)
    expect(a).not.toBe(b)
    expect(a.length).toBeGreaterThan(10)
  })
})
