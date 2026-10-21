import { forwardRef } from 'react'

/**
 * IconButton — an accessible wrapper for icon-only buttons.
 *
 * Enforces an accessible name via the required `label` prop, which is applied
 * as `aria-label` (and `title` for a hover tooltip). Use this instead of a bare
 * `<button><Icon /></button>` so screen-reader users know what the control does.
 *
 * Usage:
 *   <IconButton label="Download PDF" onClick={handleDownload}>
 *     <Download className="w-4 h-4" />
 *   </IconButton>
 */
const IconButton = forwardRef(function IconButton(
  { label, children, className = '', type = 'button', pressed, ...props },
  ref
) {
  if (!label && process.env.NODE_ENV !== 'production') {
    console.error('IconButton requires a `label` prop for accessibility.')
  }
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className={
        'inline-flex items-center justify-center rounded-lg transition-colors ' +
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ' +
        className
      }
      {...props}
    >
      {children}
    </button>
  )
})

export default IconButton
