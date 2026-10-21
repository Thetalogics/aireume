export function printDocument() {
  if (typeof window.print !== 'function') {
    throw new Error('Printing is unavailable in this browser')
  }
  window.print()
}
