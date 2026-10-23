import { useState, useEffect, useRef } from 'react'
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useAuth } from '../contexts/useAuth'
import Breadcrumbs from '../components/admin/Breadcrumbs'
import ErrorBoundary from '../components/ErrorBoundary'

/* ── Nav config ──────────────────────────────────────── */
const NAV_GROUPS = [
  {
    label: 'OVERVIEW',
    items: [
      { label: 'Dashboard', path: '/admin', icon: '⬡', exact: true },
    ],
  },
  {
    label: 'MANAGE',
    items: [
      { label: 'Tenants',  path: '/admin/tenants', icon: '⬡' },
      { label: 'Plans',    path: '/admin/plans',   icon: '⬡' },
      { label: 'Users',    path: '/admin/users',   icon: '⬡' },
    ],
  },
  {
    label: 'CONFIGURE',
    items: [
      { label: 'Plan Features', path: '/admin/features',    icon: '⬡' },
      { label: 'Webhooks',      path: '/admin/webhooks',    icon: '⬡' },
      { label: 'Rate Limits',   path: '/admin/rate-limits', icon: '⬡' },
      { label: 'Email Settings',path: '/admin/email',       icon: '⬡' },
      { label: 'SSO',           path: '/admin/sso',         icon: '⬡' },
    ],
  },
  {
    label: 'MONITOR',
    items: [
      { label: 'Metrics',         path: '/admin/metrics',  icon: '⬡' },
      { label: 'Audit Log',       path: '/admin/audit',    icon: '⬡' },
      { label: 'Security Events', path: '/admin/security', icon: '⬡' },
    ],
  },
  {
    label: 'BILLING',
    items: [
      { label: 'Revenue',  path: '/admin/billing',  icon: '⬡' },
      { label: 'Invoices', path: '/admin/invoices', icon: '⬡' },
      { label: 'Dunning',  path: '/admin/dunning',  icon: '⬡' },
      { label: 'Settings', path: '/admin/billing-settings', icon: '⬡' },
    ],
  },
  {
    label: 'CUSTOMER SUCCESS',
    items: [
      { label: 'CRM', path: '/admin/crm', icon: '⬡' },
    ],
  },
  {
    label: 'COMPLIANCE',
    items: [
      { label: 'Data Erasure',   path: '/admin/erasure',       icon: '⬡' },
      { label: 'Impersonation',  path: '/admin/impersonation', icon: '⬡' },
    ],
  },
]

const RESTRICTED_PATHS_BY_ROLE = {
  readonly: new Set(['/admin/billing-settings', '/admin/erasure', '/admin/impersonation']),
  product_owner: new Set(['/admin/erasure', '/admin/impersonation', '/admin/billing-settings']),
  billing_admin: new Set(['/admin/erasure', '/admin/impersonation', '/admin/crm']),
  support: new Set(['/admin/billing-settings', '/admin/erasure']),
  security_admin: new Set(['/admin/billing-settings', '/admin/plans', '/admin/crm']),
}

function filterNavForRole(groups, platformRole) {
  if (!platformRole || platformRole === 'super_admin') return groups
  const blocked = RESTRICTED_PATHS_BY_ROLE[platformRole] || new Set()
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !blocked.has(item.path)),
    }))
    .filter((group) => group.items.length > 0)
}

/* ── Icon map (SVG) ──────────────────────────────────── */
const ICONS = {
  Dashboard:       <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M2 10a8 8 0 1116 0A8 8 0 012 10zm8-4a1 1 0 00-1 1v3H6a1 1 0 100 2h3v3a1 1 0 102 0v-3h3a1 1 0 100-2h-3V7a1 1 0 00-1-1z" clipRule="evenodd" fillRule="evenodd"/></svg>,
  Tenants:         <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M4 4a2 2 0 012-2h8a2 2 0 012 2v12a1 1 0 110 2H4a1 1 0 110-2V4zm3 1h2v2H7V5zm2 4H7v2h2V9zm2-4h2v2h-2V5zm2 4h-2v2h2V9z" clipRule="evenodd"/></svg>,
  Plans:           <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z"/><path fillRule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd"/></svg>,
  Users:           <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z"/></svg>,
  'Plan Features': <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M3 6a3 3 0 013-3h10a1 1 0 01.8 1.6L14.25 7l2.55 2.4A1 1 0 0116 11H6a1 1 0 00-1 1v3a1 1 0 11-2 0V6z" clipRule="evenodd"/></svg>,
  Webhooks:        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd"/></svg>,
  'Rate Limits':   <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd"/></svg>,
  SSO:             <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd"/></svg>,
  'Email Settings':<svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z"/><path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z"/></svg>,
  Metrics:         <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"/></svg>,
  'Audit Log':     <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd"/></svg>,
  'Security Events':<svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd"/></svg>,
  Revenue:         <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path d="M8.433 7.418c.155-.103.346-.196.567-.267v1.698a2.305 2.305 0 01-.567-.267C8.07 8.34 8 8.114 8 8c0-.114.07-.34.433-.582zM11 12.849v-1.698c.22.071.412.164.567.267.364.243.433.468.433.582 0 .114-.07.34-.433.582a2.305 2.305 0 01-.567.267z"/><path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-13a1 1 0 10-2 0v.092a4.535 4.535 0 00-1.676.662C6.602 6.234 6 7.009 6 8c0 .99.602 1.765 1.324 2.246.48.32 1.054.545 1.676.662v1.941c-.391-.127-.68-.317-.843-.504a1 1 0 10-1.51 1.31c.562.649 1.413 1.076 2.353 1.253V15a1 1 0 102 0v-.092a4.535 4.535 0 001.676-.662C13.398 13.766 14 12.991 14 12c0-.99-.602-1.765-1.324-2.246A4.535 4.535 0 0011 9.092V7.151c.391.127.68.317.843.504a1 1 0 101.511-1.31c-.563-.649-1.413-1.076-2.354-1.253V5z" clipRule="evenodd"/></svg>,
  Invoices:        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M5 2a2 2 0 00-2 2v14l3.5-2 3.5 2 3.5-2 3.5 2V4a2 2 0 00-2-2H5zm4.707 3.707a1 1 0 00-1.414-1.414l-3 3a1 1 0 000 1.414l3 3a1 1 0 001.414-1.414L8.414 9H10a3 3 0 013 3 1 1 0 102 0 5 5 0 00-5-5H8.414l1.293-1.293z" clipRule="evenodd"/></svg>,
  Dunning:         <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd"/></svg>,
  Settings:        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd"/></svg>,
  'Data Erasure':  <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd"/></svg>,
  Impersonation:   <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-6-3a2 2 0 11-4 0 2 2 0 014 0zm-2 4a5 5 0 00-4.546 2.916A5.986 5.986 0 0010 16a5.986 5.986 0 004.546-2.084A5 5 0 0010 11z" clipRule="evenodd"/></svg>,
  CRM:             <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clipRule="evenodd"/></svg>,
}

/* ── Sidebar Nav Item ─────────────────────────────────── */
function SidebarItem({ item, onClick }) {
  const location = useLocation()
  const isActive = item.exact
    ? location.pathname === item.path
    : location.pathname === item.path || location.pathname.startsWith(item.path + '/')

  return (
    <NavLink
      to={item.path}
      end={item.exact}
      onClick={onClick}
      className={() =>
        `group flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-150 relative
        ${isActive
          ? 'text-white'
          : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
        }`
      }
    >
      {({ isActive: active }) => (
        <>
          {active && (
            <motion.div
              layoutId="admin-sidebar-active"
              className="absolute inset-0 bg-slate-800 rounded-md border-l-2 border-teal-400"
              transition={{ type: 'spring', stiffness: 350, damping: 30 }}
            />
          )}
          <span className={`shrink-0 relative z-10 ${active ? 'text-teal-400' : 'text-slate-500 group-hover:text-slate-300'}`}>
            {ICONS[item.label] || (
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <circle cx="10" cy="10" r="3"/>
              </svg>
            )}
          </span>
          <span className="truncate relative z-10">{item.label}</span>
        </>
      )}
    </NavLink>
  )
}

/* ── Sidebar ─────────────────────────────────────────── */
function Sidebar({ open, onClose }) {
  const { user } = useAuth()
  const navGroups = filterNavForRole(NAV_GROUPS, user?.platform_role)

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <button
          type="button"
          aria-label="Close"
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar panel */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-40 w-64 bg-slate-900 flex flex-col
          transform transition-transform duration-200 ease-in-out
          lg:relative lg:translate-x-0 lg:flex lg:z-auto
          ${open ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-slate-800 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-teal-500 to-cyan-600 flex items-center justify-center shadow-lg shrink-0">
              <svg viewBox="0 0 20 20" fill="white" className="w-4 h-4">
                <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd"/>
              </svg>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-white font-bold text-base tracking-tight">ARIA</span>
              <span className="bg-teal-500/20 text-teal-400 text-xs font-medium px-2 py-0.5 rounded tracking-wide">
                ADMIN
              </span>
            </div>
          </div>
          {/* Close button (mobile) */}
          <button
            onClick={onClose}
            className="lg:hidden text-slate-400 hover:text-white p-1 rounded"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd"/>
            </svg>
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5">
          {navGroups.map(group => (
            <div key={group.label}>
              <p className="text-xs font-semibold uppercase text-slate-500 tracking-wider px-3 mb-1.5">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map(item => (
                  <SidebarItem key={item.path} item={item} onClick={onClose} />
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Back to app link */}
        <div className="px-3 py-3 border-t border-slate-800 shrink-0">
          <NavLink
            to="/"
            className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-white hover:bg-slate-800/60 transition-all"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
              <path fillRule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" clipRule="evenodd"/>
            </svg>
            <span>Back to Recruiter App</span>
          </NavLink>
        </div>
      </aside>
    </>
  )
}

/* ── Top Bar ─────────────────────────────────────────── */
function TopBar({ onMenuClick }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [avatarOpen, setAvatarOpen] = useState(false)
  const avatarRef = useRef(null)
  const initials = user?.email ? user.email[0].toUpperCase() : 'A'

  useEffect(() => {
    function handler(e) {
      if (avatarRef.current && !avatarRef.current.contains(e.target)) {
        setAvatarOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <header className="bg-white border-b border-gray-200 shadow-sm h-14 flex items-center px-4 gap-4 shrink-0">
      {/* Hamburger (mobile) */}
      <button
        onClick={onMenuClick}
        className="lg:hidden p-2 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
        aria-label="Open sidebar"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
          <path fillRule="evenodd" d="M3 5a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM3 10a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM3 15a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd"/>
        </svg>
      </button>

      {/* Breadcrumbs */}
      <div className="flex-1 min-w-0">
        <Breadcrumbs />
      </div>

      {/* Avatar + dropdown */}
      <div ref={avatarRef} className="relative shrink-0">
        <button
          onClick={() => setAvatarOpen(v => !v)}
          className="flex items-center gap-2 rounded-full hover:bg-gray-100 p-1 transition-colors"
          aria-label="User menu"
        >
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-teal-500 to-cyan-600 flex items-center justify-center text-white text-xs font-bold">
            {initials}
          </div>
          <span className="hidden sm:block text-sm text-gray-700 font-medium max-w-[120px] truncate">
            {user?.email}
          </span>
          <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-gray-400">
            <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd"/>
          </svg>
        </button>

        {avatarOpen && (
          <div className="absolute right-0 top-full mt-2 w-52 bg-white border border-gray-200 rounded-xl shadow-lg py-1.5 z-50">
            <div className="px-4 py-2.5 border-b border-gray-100">
              <p className="text-xs text-gray-500">Signed in as</p>
              <p className="text-sm font-semibold text-gray-800 truncate">{user?.email}</p>
              <span className="inline-block mt-1 text-[10px] font-semibold uppercase tracking-wider text-teal-600 bg-teal-50 rounded-full px-2 py-0.5">
                Platform Admin
              </span>
            </div>
            <button
              onClick={() => { setAvatarOpen(false); logout(); navigate('/login') }}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M3 3a1 1 0 00-1 1v12a1 1 0 102 0V4a1 1 0 00-1-1zm10.293 9.293a1 1 0 001.414 1.414l3-3a1 1 0 000-1.414l-3-3a1 1 0 10-1.414 1.414L14.586 9H7a1 1 0 100 2h7.586l-1.293 1.293z" clipRule="evenodd"/>
              </svg>
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  )
}

/* ── Admin Layout ─────────────────────────────────────── */
export default function AdminLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Close sidebar on resize to desktop
  useEffect(() => {
    function handleResize() {
      if (window.innerWidth >= 1024) setSidebarOpen(false)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />

        {/* Content */}
        <main id="main-content" className="flex-1 overflow-y-auto p-6">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
