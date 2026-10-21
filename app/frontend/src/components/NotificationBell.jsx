import { useState, useRef, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Bell, CheckCheck, X } from 'lucide-react'
import { useNotification } from '../contexts/useNotification'

const TYPE_DOT = {
  success: 'bg-green-500',
  warning: 'bg-amber-500',
  error: 'bg-red-500',
  info: 'bg-brand-500',
}

function timeAgo(iso) {
  const diff = Math.max(0, Date.now() - new Date(iso).getTime())
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function NotificationBell() {
  const {
    notifications,
    unreadCount,
    markNotificationRead,
    markAllNotificationsRead,
    removeNotification,
  } = useNotification()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative flex items-center justify-center w-9 h-9 rounded-full hover:bg-brand-50 dark:hover:bg-white/5 transition-colors"
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : 'Notifications'}
        aria-haspopup="true"
        aria-expanded={open}
      >
        <Bell className="w-5 h-5 text-slate-600 dark:text-dark-text-secondary" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: -8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.95 }}
            transition={{ type: 'spring', stiffness: 400, damping: 28 }}
            className="absolute right-0 top-full mt-2 w-80 max-h-[26rem] overflow-hidden bg-white/95 dark:bg-dark-card/95 backdrop-blur-2xl border border-brand-100 dark:border-white/10 rounded-2xl shadow-brand-lg z-50 flex flex-col"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-brand-50 dark:border-white/10">
              <span className="text-sm font-bold text-slate-800 dark:text-dark-text-primary">
                Notifications
              </span>
              {notifications.length > 0 && (
                <button
                  onClick={markAllNotificationsRead}
                  className="flex items-center gap-1 text-xs font-semibold text-brand-600 hover:text-brand-700"
                >
                  <CheckCheck className="w-3.5 h-3.5" />
                  Mark all read
                </button>
              )}
            </div>

            <div className="overflow-y-auto">
              {notifications.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-400 dark:text-dark-text-secondary">
                  You&apos;re all caught up.
                </div>
              ) : (
                notifications.map((n) => {
                  const body = (
                    <div className="flex items-start gap-3">
                      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${TYPE_DOT[n.type] || TYPE_DOT.info}`} />
                      <div className="min-w-0 flex-1">
                        {n.title && (
                          <p className="text-sm font-semibold text-slate-800 dark:text-dark-text-primary truncate">
                            {n.title}
                          </p>
                        )}
                        {n.message && (
                          <p className="text-xs text-slate-500 dark:text-dark-text-secondary">{n.message}</p>
                        )}
                        <p className="text-[10px] text-slate-400 mt-0.5">{timeAgo(n.createdAt)}</p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          removeNotification(n.id)
                        }}
                        aria-label="Dismiss notification"
                        className="text-slate-300 hover:text-slate-500 shrink-0"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  )
                  const cls = `block px-4 py-3 border-b border-brand-50/70 dark:border-white/5 hover:bg-brand-50/50 dark:hover:bg-white/5 transition-colors ${
                    n.read ? 'opacity-60' : ''
                  }`
                  return n.href ? (
                    <Link
                      key={n.id}
                      to={n.href}
                      className={cls}
                      onClick={() => {
                        markNotificationRead(n.id)
                        setOpen(false)
                      }}
                    >
                      {body}
                    </Link>
                  ) : (
                    <button
                      type="button"
                      key={n.id}
                      className={`${cls} w-full text-left bg-transparent border-0`}
                      onClick={() => markNotificationRead(n.id)}
                    >
                      {body}
                    </button>
                  )
                })
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
