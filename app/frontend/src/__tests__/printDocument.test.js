import { expect, it, vi } from 'vitest'
import { printDocument } from '../lib/printDocument'

it('opens the browser print dialog for the current document', () => {
  const print = vi.fn()
  vi.stubGlobal('print', print)
  printDocument()
  expect(print).toHaveBeenCalledOnce()
})
