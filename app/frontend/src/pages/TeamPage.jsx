import { useState, useEffect } from 'react'
import { Users2, UserPlus, Shield, AlertCircle, Check, X } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import { getTeamMembers, inviteTeamMember, startTraining, getTrainingStatus, getTenantAuditLogs } from '../lib/api'
import { useAuth } from '../contexts/useAuth'
import { useOnboarding } from '../contexts/useOnboarding'
import { showError } from '../lib/toast'

const ROLES = ['admin', 'ta_lead', 'recruiter', 'hiring_manager', 'viewer']

const ROLE_LABELS = {
  admin: 'Admin',
  ta_lead: 'TA lead',
  recruiter: 'Recruiter',
  hiring_manager: 'Hiring manager',
  viewer: 'Viewer',
}

function InviteModal({ onSave, onClose }) {
  const { completeChecklistItem } = useOnboarding()
  const [email, setEmail]     = useState('')
  const [role, setRole]       = useState('recruiter')
  const [result, setResult]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  const handleInvite = async () => {
    if (!email.trim()) return
    setLoading(true)
    setError('')
    try {
      const data = await inviteTeamMember(email, role)
      setResult(data)
      if (data.invite_email_sent !== false) {
        completeChecklistItem('invitedTeamMember')
      }
      onSave()
    } catch (err) {
      setError(err.response?.data?.detail || 'Invitation failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white/95 backdrop-blur-xl rounded-3xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-md p-6 card-animate">
        <div className="flex items-center justify-between mb-5">
          <h3 className="font-extrabold text-brand-900 tracking-tight">Invite Team Member</h3>
          <button onClick={onClose} aria-label="Close dialog" className="p-1.5 hover:bg-brand-50 rounded-xl transition-colors">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>
        {!result ? (
          <div className="space-y-4">
            <div>
              <label htmlFor="teampage-email-1" className="block text-sm font-bold text-slate-700 mb-1.5">Email</label>
              <input id="teampage-email-1"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                placeholder="colleague@company.com"
                className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
              />
            </div>
            <div>
              <label htmlFor="teampage-role-2" className="block text-sm font-bold text-slate-700 mb-1.5">Role</label>
              <select id="teampage-role-2"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
              >
                {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
              </select>
            </div>
            {error && (
              <p className="text-sm text-red-600 flex items-center gap-2 font-medium">
                <AlertCircle className="w-4 h-4" />{error}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={onClose}
                className="px-4 py-2 ring-1 ring-brand-200 text-sm font-semibold text-brand-700 rounded-xl hover:bg-brand-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleInvite}
                disabled={loading || !email}
                className="px-4 py-2 btn-brand text-white text-sm font-bold rounded-xl disabled:opacity-60 shadow-brand-sm"
              >
                {loading ? 'Sending...' : 'Create Account'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="p-3.5 bg-green-50 ring-1 ring-green-200 rounded-2xl">
              <p className="text-sm text-green-700 font-bold">Account created successfully!</p>
            </div>
            <p className="text-sm text-slate-600">{result.message || 'Team member invited successfully.'}</p>
            <button
              onClick={onClose}
              className="w-full py-2.5 btn-brand text-white text-sm font-bold rounded-xl shadow-brand-sm"
            >
              Done
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function TrainingDashboard() {
  const [status, setStatus]     = useState(null)
  const [loading, setLoading]   = useState(true)
  const [training, setTraining] = useState(false)

  const load = () => getTrainingStatus().then(setStatus).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  const handleTrain = async () => {
    setTraining(true)
    try {
      await startTraining()
      setTimeout(load, 2000)
    } catch (err) {
      showError(err.response?.data?.detail || 'Prompt personalization failed')
    } finally {
      setTraining(false)
    }
  }

  if (loading) return (
    <div className="flex justify-center py-8">
      <div className="w-6 h-6 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
    </div>
  )

  return (
    <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-5 space-y-4">
      <div>
        <h3 className="font-extrabold text-brand-900 tracking-tight">Prompt personalization</h3>
        <p className="text-sm text-slate-500 mt-0.5">Use your team's labeled hiring outcomes as prompt context. Beta feature; model weights stay unchanged.</p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-brand-50 ring-1 ring-brand-100 rounded-2xl p-4 text-center">
          <p className="text-3xl font-extrabold text-brand-900">{status?.labeled_count || 0}</p>
          <p className="text-xs font-semibold text-brand-700 mt-0.5">Labeled Examples</p>
          <p className="text-xs text-slate-400 mt-1">Need 10+ to personalize</p>
        </div>
        <div className={`rounded-2xl p-4 text-center ring-1 ${status?.trained ? 'bg-green-50 ring-green-200' : 'bg-brand-50 ring-brand-100'}`}>
          <p className={`text-sm font-bold ${status?.trained ? 'text-green-700' : 'text-slate-500'}`}>
            {status?.trained ? 'Prompt personalization ready' : 'Prompt personalization not completed'}
          </p>
          {status?.model_name && <p className="text-xs text-slate-400 mt-1 font-medium">{status.model_name}</p>}
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed">
        Label candidates as "Hired" or "Rejected" from the report page. With at least 10 labeled
        examples, personalization embeds up to 50 in an Ollama Modelfile SYSTEM prompt for your tenant.
      </p>

      <button
        onClick={handleTrain}
        disabled={training || (status?.labeled_count || 0) < 10}
        className="w-full py-2.5 bg-brand-600 hover:bg-brand-700 text-white text-sm font-bold rounded-2xl disabled:opacity-50 transition-colors shadow-brand-sm"
      >
        {training ? 'Starting personalization...' : 'Personalize ARIA'}
      </button>
    </div>
  )
}

function TenantAuditPanel() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getTenantAuditLogs({ limit: 20 })
      .then((data) => setEntries(data.entries || []))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-5 space-y-3">
      <h3 className="font-extrabold text-brand-900 tracking-tight">Activity Log</h3>
      <p className="text-sm text-slate-500">Recent team and workflow events (admin only).</p>
      {loading ? (
        <div className="flex justify-center py-6">
          <div className="w-6 h-6 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-slate-400 py-4">No audit events yet.</p>
      ) : (
        <div className="divide-y divide-brand-50 max-h-64 overflow-y-auto">
          {entries.map((e) => (
            <div key={e.id} className="py-2.5 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-700">{e.action}</span>
                <span className="text-xs text-slate-400">{new Date(e.created_at).toLocaleString()}</span>
              </div>
              <p className="text-xs text-slate-500">{e.actor_email} · {e.resource_type}{e.resource_id ? ` #${e.resource_id}` : ''}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function TeamPage() {
  const { user } = useAuth()
  const [members, setMembers]       = useState([])
  const [loading, setLoading]       = useState(true)
  const [showInvite, setShowInvite] = useState(false)

  const load = () => getTeamMembers().then(setMembers).finally(() => setLoading(false))
  useEffect(() => { load() }, [])

  const isAdmin = user?.role === 'admin'

  return (
    <div>
      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <div className="flex items-center justify-between card-animate">
          <div>
            <h2 className="text-3xl font-extrabold text-brand-900 tracking-tight">Team</h2>
            <p className="text-slate-500 text-sm mt-1 font-medium">Manage your workspace members and ARIA prompt personalization.</p>
          </div>
          {isAdmin && (
            <button
              onClick={() => setShowInvite(true)}
              className="flex items-center gap-2 px-4 py-2.5 btn-brand text-white text-sm font-bold rounded-xl shadow-brand-sm"
            >
              <UserPlus className="w-4 h-4" /> Invite Member
            </button>
          )}
        </div>

        {/* Members list */}
        <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand overflow-hidden card-animate">
          <div className="p-5 border-b border-brand-50">
            <h3 className="font-extrabold text-brand-900 tracking-tight flex items-center gap-2">
              <Users2 className="w-5 h-5 text-brand-500" />
              Team Members
            </h3>
          </div>
          {loading ? (
            <div className="flex justify-center py-10">
              <div className="w-6 h-6 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : members.length === 0 ? (
            <EmptyState
              icon={Users2}
              title="No team members yet"
              description="Invite recruiters and hiring managers to collaborate on candidates and screenings."
              actionLabel="Invite a teammate"
              onAction={() => setShowInvite(true)}
              className="py-12"
            />
          ) : (
            <div className="divide-y divide-brand-50">
              {members.map(m => (
                <div key={m.id} className="flex items-center justify-between px-5 py-4 hover:bg-brand-50/40 transition-colors">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center text-white font-extrabold text-sm shadow-brand-sm">
                      {m.email[0].toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-bold text-brand-900">{m.email}</p>
                      <p className="text-xs text-slate-400 font-medium">{new Date(m.created_at).toLocaleDateString()}</p>
                    </div>
                  </div>
                  <span className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold ring-1 ${
                    m.role === 'admin'          ? 'bg-brand-50 text-brand-700 ring-brand-200' :
                    m.role === 'ta_lead'        ? 'bg-violet-50 text-violet-700 ring-violet-200' :
                    m.role === 'recruiter'      ? 'bg-blue-50 text-blue-700 ring-blue-200' :
                    m.role === 'hiring_manager' ? 'bg-amber-50 text-amber-800 ring-amber-200' :
                    'bg-slate-50 text-slate-600 ring-slate-200'
                  }`}>
                    {m.role === 'admin' && <Shield className="w-3 h-3" />}
                    {ROLE_LABELS[m.role] || m.role}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {isAdmin && <TenantAuditPanel />}

        {/* Prompt personalization dashboard */}
        <TrainingDashboard />
      </main>

      {showInvite && (
        <InviteModal
          onSave={load}
          onClose={() => setShowInvite(false)}
        />
      )}
    </div>
  )
}
