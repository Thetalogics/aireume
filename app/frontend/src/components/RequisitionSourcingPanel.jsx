import { SOURCING_CHANNELS } from './requisitionSourcingConstants'
import { Link } from 'react-router-dom'
import { Search, Users, Upload, ExternalLink } from 'lucide-react'
import { Button, Card } from './ui'
import { REQUISITIONS } from '../lib/uxLabels'


export default function RequisitionSourcingPanel({
  req,
  searchBrief,
  onSearchBriefChange,
  onSave,
  saving,
  canWrite,
  onAddCandidates,
  onScreen,
}) {
  const brief = typeof req?.search_brief_json === 'object' ? req.search_brief_json : {}
  const activeChannels = new Set(brief.channels || [])
  const channelNotes = brief.channel_notes || {}

  const toggleChannel = (id) => {
    const next = new Set(activeChannels)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onSearchBriefChange({
      ...brief,
      latest_strategy: searchBrief,
      channels: [...next],
      channel_notes: channelNotes,
    })
  }

  const setChannelNote = (id, value) => {
    onSearchBriefChange({
      ...brief,
      latest_strategy: searchBrief,
      channels: [...activeChannels],
      channel_notes: { ...channelNotes, [id]: value },
    })
  }

  const handleSave = () => {
    onSave({
      latest_strategy: searchBrief,
      channels: [...activeChannels],
      channel_notes: channelNotes,
      hm_feedback_history: brief.hm_feedback_history || [],
    })
  }

  return (
    <Card className="p-6 space-y-6">
      <div>
        <h3 className="text-lg font-bold text-brand-900">{REQUISITIONS.sourcingTabTitle}</h3>
        <p className="text-sm text-slate-500 mt-1">{REQUISITIONS.sourcingTabHint}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {canWrite && (
          <>
            <Button size="sm" onClick={onAddCandidates}>
              <Users className="w-4 h-4" />
              Add from pool
            </Button>
            <Button size="sm" variant="secondary" onClick={onScreen}>
              <Upload className="w-4 h-4" />
              Screen new resume
            </Button>
            <Link
              to={`/candidates?requisition_id=${req.id}`}
              className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-600 hover:text-brand-800 px-3 py-1.5"
            >
              <Search className="w-4 h-4" />
              Search candidates
            </Link>
          </>
        )}
      </div>

      <div>
        <p className="text-sm font-semibold text-slate-700 mb-2">{REQUISITIONS.sourcingChannelsLabel}</p>
        <div className="space-y-2">
          {SOURCING_CHANNELS.map((ch) => (
            <div key={ch.id} className="rounded-xl ring-1 ring-brand-100 p-3">
              <label htmlFor={`sourcing-channel-${ch.id}`} className="flex items-start gap-2 cursor-pointer">
                <input
                  id={`sourcing-channel-${ch.id}`}
                  type="checkbox"
                  checked={activeChannels.has(ch.id)}
                  onChange={() => toggleChannel(ch.id)}
                  disabled={!canWrite}
                  className="mt-1"
                />
                <span className="text-sm font-semibold text-brand-900">{ch.label}</span>
              </label>
              <p className="text-xs text-slate-500 ml-6 mt-0.5">{ch.hint}</p>
              {activeChannels.has(ch.id) && canWrite && (
                <input
                  type="text"
                  value={channelNotes[ch.id] || ''}
                  onChange={(e) => setChannelNote(ch.id, e.target.value)}
                  placeholder="Notes for this channel (search strings, agencies, links…)"
                  className="mt-2 w-full rounded-lg border border-brand-200 px-3 py-1.5 text-xs"
                />
              )}
              {ch.id === 'linkedin' && activeChannels.has(ch.id) && (
                <p className="mt-2 text-[11px] text-slate-400 flex items-center gap-1">
                  <ExternalLink className="w-3 h-3" />
                  {REQUISITIONS.sourcingLinkedInHint}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <label htmlFor="requisitionsourcingpanel-field-1" className="block text-sm font-semibold text-slate-700">{REQUISITIONS.searchBriefTab}</label>
        <p className="text-xs text-slate-500 mb-2">{REQUISITIONS.searchBriefHint}</p>
        <textarea id="requisitionsourcingpanel-field-1"
          value={searchBrief}
          onChange={(e) => onSearchBriefChange({
            ...brief,
            latest_strategy: e.target.value,
            channels: [...activeChannels],
            channel_notes: channelNotes,
          })}
          rows={5}
          disabled={!canWrite}
          className="w-full rounded-xl border border-brand-200 px-3 py-2 text-sm disabled:opacity-60"
          placeholder="Target companies, titles, keywords, HM feedback-driven adjustments…"
        />
      </div>

      {(brief.hm_feedback_history || []).length > 0 && (
        <div className="rounded-xl bg-blue-50 ring-1 ring-blue-100 p-3">
          <p className="text-xs font-bold text-blue-900 uppercase tracking-wider mb-2">HM feedback applied</p>
          <ul className="text-xs text-blue-800 space-y-1">
            {brief.hm_feedback_history.slice(-3).map((h, i) => (
              <li key={i}>{h.notes || h.reason_code || 'Feedback recorded'}</li>
            ))}
          </ul>
        </div>
      )}

      {canWrite && (
        <Button onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : REQUISITIONS.sourcingSaveCta}
        </Button>
      )}
    </Card>
  )
}
