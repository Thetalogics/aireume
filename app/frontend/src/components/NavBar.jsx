import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard, Briefcase, Users, BarChart3, Columns,
  Users2, Settings, Shield, LogOut, Sparkles,
  MoreHorizontal, Moon, Sun, Mic, FolderKanban, GitCompare, ScanSearch, Video,
} from 'lucide-react'
import { NAV, REQUISITIONS } from '../lib/uxLabels'

/** Active state for primary nav items (includes nested routes). */
function isPrimaryNavActive(pathname, itemPath) {
  if (itemPath === '/') return pathname === '/'
  if (itemPath === '/requisitions') return pathname.startsWith('/requisitions')
  if (itemPath === '/candidates') return pathname.startsWith('/candidates')
  if (itemPath === '/analyze') {
    return pathname.startsWith('/analyze')
  }
  return pathname === itemPath || pathname.startsWith(`${itemPath}/`)
}
import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAuth } from '../contexts/useAuth'
import { useBranding } from '../contexts/useBranding'
import usePermissions from '../hooks/usePermissions'
import { useSubscription } from '../hooks/useSubscription'
import { useTheme } from '../contexts/useTheme'
import { STATUS_CONFIG } from '../lib/constants'
import JobCenter from './patterns/JobCenter'
import NotificationBell from './NotificationBell'

/* ── Static config ───────────────────────────────────── */

const PRIMARY_NAV_RECRUITER = [
  { label: NAV.home, path: '/', icon: LayoutDashboard },
  { label: NAV.requisitions, path: '/requisitions', icon: Briefcase, feature: 'requisitions' },
  { label: NAV.analyze, path: '/analyze', icon: ScanSearch },
  { label: NAV.candidates, path: '/candidates', icon: Users },
  { label: NAV.interviews, path: '/ai-interviews', icon: Mic, feature: 'ai_interviews' },
  { label: NAV.pipeline, path: '/pipeline', icon: Columns, feature: 'pipeline' },
]

const PRIMARY_NAV_HM = [
  { label: NAV.hmDashboard, path: '/requisitions', icon: Briefcase, feature: 'requisitions' },
  { label: NAV.candidates, path: '/candidates', icon: Users },
]

const USER_MENU_LINKS = [
  { label: NAV.compare, path: '/compare', icon: GitCompare, feature: 'compare' },
  { label: NAV.analytics, path: '/analytics', icon: BarChart3, feature: 'analytics' },
  { label: REQUISITIONS.openRequestsNav, path: '/requisitions/open-requests', icon: Briefcase, feature: 'requisitions', assignOnly: true },
  { label: NAV.team, path: '/team', icon: Users2 },
  { label: NAV.interviewReview, path: '/video', icon: Video, feature: 'video_analysis' },
  { label: NAV.settings, path: '/settings', icon: Settings },
]

/* ── Desktop user menu dropdown ──────────────────────── */

function UserMenu({ user, tenant, logout, onClose, menuLinks }) {
  const navigate = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const isPlatformAdmin = user?.is_platform_admin || !!user?.platform_role
  const initials = user?.email ? user.email[0].toUpperCase() : '?'

  function handleNav(path) {
    onClose()
    navigate(path)
  }

  return (
    <motion.div
      role="menu"
      initial={{ opacity: 0, y: -8, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -4, scale: 0.95 }}
      transition={{ type: 'spring', stiffness: 400, damping: 28 }}
      className="absolute right-0 top-full mt-2 w-64 rounded-2xl popover-surface py-1.5 z-50"
    >
      {/* User info */}
      <div className="px-4 py-3 border-b border-brand-50 dark:border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center text-white text-sm font-bold shrink-0">
            {initials}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800 dark:text-dark-text-primary truncate">{user?.email}</p>
            <span className="inline-block mt-0.5 text-[11px] font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-300 bg-brand-50 dark:bg-brand-900/40 rounded-full px-2 py-0.5">
              {user?.role || 'user'}
            </span>
          </div>
        </div>
      </div>

      {/* Links */}
      <div className="py-1">
        {menuLinks.map(item => (
          <button
            key={item.path}
            role="menuitem"
            onClick={() => handleNav(item.path)}
            className="popover-item"
          >
            <item.icon className="popover-item-icon" />
            {item.label}
          </button>
        ))}
      </div>

      <div className="mx-3 my-1 border-t border-brand-50 dark:border-white/10" />

      {/* Theme toggle */}
      <button
        role="menuitem"
        onClick={toggleTheme}
        className="popover-item"
      >
        {theme === 'dark' ? <Sun className="popover-item-icon" /> : <Moon className="popover-item-icon" />}
        {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
      </button>

      {/* Logout */}
      <button
        role="menuitem"
        onClick={() => { onClose(); logout(); navigate('/login') }}
        className="popover-item text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 hover:text-red-700 dark:hover:text-red-300"
      >
        <LogOut className="w-4 h-4 shrink-0" />
        Sign out
      </button>
    </motion.div>
  )
}

/* ── Mobile bottom tab bar ───────────────────────────── */

function MobileTabBar({ location, canWrite, primaryNav, menuLinks }) {
  const [moreOpen, setMoreOpen] = useState(false)
  const sheetRef = useRef(null)

  // Close sheet on route change
  useEffect(() => { setMoreOpen(false) }, [location.pathname])

  // Close sheet on outside tap
  useEffect(() => {
    function handleClick(e) {
      if (sheetRef.current && !sheetRef.current.contains(e.target)) {
        setMoreOpen(false)
      }
    }
    if (moreOpen) document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [moreOpen])

  const tabs = primaryNav.filter(item => canWrite || item.path !== '/analyze')

  return (
    <>
      {/* Backdrop */}
      {moreOpen && (
        <button type="button" aria-label="Close" className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40 md:hidden" onClick={() => setMoreOpen(false)} />
      )}

      {/* More sheet */}
      {moreOpen && (
        <div
          id="mobile-more-sheet"
          ref={sheetRef}
          className="fixed bottom-16 left-0 right-0 popover-surface rounded-t-2xl shadow-brand-xl z-50 md:hidden animate-fade-up border-t-0"
        >
          <MobileMoreSheet onNavigate={() => setMoreOpen(false)} menuLinks={menuLinks} />
        </div>
      )}

      {/* Tab bar */}
      <nav className="fixed bottom-0 left-0 right-0 bg-white/80 dark:bg-dark-card/80 backdrop-blur-2xl border-t border-brand-100/60 dark:border-white/10 z-40 md:hidden">
        <div className="flex items-center justify-around h-16 px-2">
          {tabs.map(tab => {
            const active = isPrimaryNavActive(location.pathname, tab.path)
            return (
              <Link
                key={tab.path}
                to={tab.path}
                aria-current={active ? 'page' : undefined}
                className={`relative flex flex-col items-center gap-0.5 px-2 py-1.5 rounded-lg transition-colors ${
                  active ? 'text-brand-600' : 'text-slate-500'
                }`}
              >
                <motion.div
                  animate={{ scale: active ? 1.1 : 1 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 15 }}
                >
                  <tab.icon className="w-5 h-5" />
                </motion.div>
                <span className="text-xs font-medium">{tab.label}</span>
                {active && (
                  <motion.span
                    layoutId="mobile-tab-indicator"
                    className="absolute -top-px left-1/2 -translate-x-1/2 w-8 h-0.5 bg-brand-600 rounded-full"
                    transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                  />
                )}
              </Link>
            )
          })}
          <button
            type="button"
            aria-label="More navigation"
            aria-expanded={moreOpen}
            aria-controls="mobile-more-sheet"
            onClick={() => setMoreOpen(v => !v)}
            className={`flex flex-col items-center gap-0.5 px-2 py-1.5 rounded-lg transition-colors ${
              moreOpen ? 'text-brand-600' : 'text-slate-500'
            }`}
          >
            <MoreHorizontal className="w-5 h-5" />
            <span className="text-xs font-medium">More</span>
          </button>
        </div>
      </nav>
    </>
  )
}

/* ── Mobile "More" sheet content ─────────────────────── */

function MobileMoreSheet({ onNavigate, menuLinks }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const isPlatformAdmin = user?.is_platform_admin || !!user?.platform_role
  const initials = user?.email ? user.email[0].toUpperCase() : '?'

  function handleNav(path) {
    onNavigate()
    navigate(path)
  }

  return (
    <div className="max-w-md mx-auto p-4 pb-6">
      {/* User info */}
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center text-white text-sm font-bold">
          {initials}
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800 truncate">{user?.email}</p>
          <span className="inline-block text-[11px] font-semibold uppercase tracking-wider text-brand-600 bg-brand-50 rounded-full px-2 py-0.5">
            {user?.role || 'user'}
          </span>
        </div>
      </div>

      {/* Links grid */}
      <div className="grid grid-cols-3 gap-3">
        {menuLinks.map(item => (
          <button
            key={item.path}
            onClick={() => handleNav(item.path)}
            className="flex flex-col items-center gap-1.5 p-3 rounded-xl text-slate-600 hover:bg-brand-50 hover:text-brand-700 transition-colors"
          >
            <item.icon className="w-5 h-5" />
            <span className="text-xs font-medium">{item.label}</span>
          </button>
        ))}

      </div>

      <div className="mt-4 pt-3 border-t border-brand-50 dark:border-white/10">
        <button
          onClick={() => { onNavigate(); logout(); navigate('/login') }}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 rounded-xl transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Sign out
        </button>
      </div>
    </div>
  )
}

/* ── Main Component ──────────────────────────────────── */

export default function NavBar() {
  const { user, tenant, logout } = useAuth()
  const { branding } = useBranding()
  const { canWrite, isHiringManager, canAssign } = usePermissions()
  const { isFeatureAvailable } = useSubscription()

  const filterByFeature = (items) =>
    items.filter((item) => !item.feature || isFeatureAvailable(item.feature))

  const primaryNav = filterByFeature(isHiringManager ? PRIMARY_NAV_HM : PRIMARY_NAV_RECRUITER).map((item) =>
    item.path === '/analyze' && isFeatureAvailable('requisitions')
      ? { ...item, label: NAV.screenResumes }
      : item,
  )
  const menuLinks = filterByFeature(USER_MENU_LINKS).filter(
    (item) => !item.assignOnly || canAssign,
  )
  const location = useLocation()
  const navigate = useNavigate()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userMenuRef = useRef(null)
  const mobileMenuRef = useRef(null)
  const initials = user?.email ? user.email[0].toUpperCase() : '?'
  const isPlatformAdmin = user?.is_platform_admin || !!user?.platform_role
  const brandName = branding?.brand_name || 'ARIA'
  const brandLogo = branding?.brand_logo_url
  const brandColor = branding?.brand_primary_color

  // Close user menu on outside click
  useEffect(() => {
    function handleClick(e) {
      if (
        (userMenuRef.current && userMenuRef.current.contains(e.target)) ||
        (mobileMenuRef.current && mobileMenuRef.current.contains(e.target))
      ) {
        return
      }
      setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <>
      <header className="bg-white/70 dark:bg-dark-card/70 backdrop-blur-2xl border-b border-brand-100/60 dark:border-white/10 sticky top-0 z-30 print:hidden">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-4">

          {/* Logo */}
          <Link to="/" className="flex items-center gap-2.5 shrink-0 group">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName} className="w-8 h-8 rounded-xl object-contain" />
            ) : (
              <div
                className="w-8 h-8 rounded-xl bg-gradient-to-br from-brand-600 to-brand-500 flex items-center justify-center shadow-brand-sm group-hover:shadow-brand transition-shadow"
                style={brandColor ? { background: brandColor } : undefined}
              >
                <Sparkles className="w-4 h-4 text-white" />
              </div>
            )}
            <span className="text-lg font-bold text-brand-900 tracking-tight">{brandName}</span>
            {tenant?.name && tenant.name.trim().toLowerCase() !== brandName.trim().toLowerCase() && (
              <span className="hidden sm:inline text-xs text-brand-600 border border-brand-200 bg-brand-50 rounded-full px-2.5 py-0.5 ml-1 font-medium">
                {tenant.name}
              </span>
            )}
          </Link>

          {/* Desktop primary nav */}
          <nav className="hidden md:flex items-center gap-1">
            {primaryNav.filter(item => canWrite || item.path !== '/analyze').map(item => {
              const active = isPrimaryNavActive(location.pathname, item.path)
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  aria-current={active ? 'page' : undefined}
                  className={`relative flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                    active
                      ? 'text-brand-700'
                      : 'text-slate-500 hover:text-brand-700 hover:bg-brand-50/60'
                  }`}
                >
                  <item.icon className={`w-4 h-4 ${active ? 'text-brand-600' : ''}`} />
                  {item.label}
                  {/* Animated active underline */}
                  {active && (
                    <motion.span
                      layoutId="nav-active-underline"
                      className="absolute -bottom-[9px] left-1/2 -translate-x-1/2 w-5 h-0.5 bg-brand-600 rounded-full"
                      transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                    />
                  )}
                </Link>
              )
            })}
          </nav>

          {/* Right: progress + admin link + avatar (desktop) */}
          <div className="hidden md:flex items-center gap-3">
            <JobCenter />
            <NotificationBell />
            {isPlatformAdmin && (
              <Link
                to="/admin"
                title="Admin Portal"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold text-teal-700 bg-teal-50 hover:bg-teal-100 border border-teal-200 transition-colors"
              >
                <Shield className="w-3.5 h-3.5" />
                Admin
              </Link>
            )}
            <div ref={userMenuRef} className="relative">
              <button
                onClick={() => setUserMenuOpen(v => !v)}
                className="flex items-center gap-2 rounded-full p-1 hover:bg-brand-50 transition-colors"
                aria-label="User menu"
              >
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center text-white text-xs font-bold">
                  {initials}
                </div>
              </button>

              <AnimatePresence>
                {userMenuOpen && (
                  <UserMenu user={user} tenant={tenant} logout={logout} onClose={() => setUserMenuOpen(false)} menuLinks={menuLinks} />
                )}
              </AnimatePresence>
            </div>
          </div>

          {/* Mobile: logo already shown, just show progress + avatar on right */}
          <div className="md:hidden flex items-center gap-2">
            <JobCenter />
            <div ref={mobileMenuRef} className="relative">
              <button
                type="button"
                aria-label="Open account menu"
                aria-expanded={userMenuOpen}
                onClick={() => setUserMenuOpen(v => !v)}
                className="w-8 h-8 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center text-white text-xs font-bold"
              >
                {initials}
              </button>
              <AnimatePresence>
                {userMenuOpen && (
                  <UserMenu user={user} tenant={tenant} logout={logout} onClose={() => setUserMenuOpen(false)} menuLinks={menuLinks} />
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile bottom tab bar */}
      <MobileTabBar location={location} canWrite={canWrite} primaryNav={primaryNav} menuLinks={menuLinks} />
    </>
  )
}
