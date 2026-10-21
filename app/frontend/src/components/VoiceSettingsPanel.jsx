import { Bell, Clock, Loader2, Mic, PhoneCall, Save, Shield, Volume2 } from 'lucide-react'
import { suggestInterviewOpening } from '../lib/api'
import { Section, Field, TextInput, Select, DayPicker } from './VoiceScreeningPrimitives'
import { OPENING_PLACEHOLDERS, VOICE_OPTIONS, GREETING_OPTIONS, DETAIL_OPTIONS, FOLLOW_UP_OPTIONS, TIMEZONE_OPTIONS } from './voiceScreeningConstants'

export default function VoiceSettingsPanel({
  draft,
  setDraft,
  saving,
  hasChanges,
  onSave,
  onCancel,
  isAdmin,
  suggestingOpening,
  setSuggestingOpening,
  setError,
}) {
  return (
          <div className="space-y-6">
            {/* Bot Identity */}
            <Section
              title="Bot Identity"
              icon={Volume2}
              description="Configure how the AI bot presents itself to candidates"
              action={
                <div className="flex gap-2">
                  <button
                    onClick={onSave}
                    disabled={saving || !hasChanges}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                    Save
                  </button>
                </div>
              }
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Bot Name">
                  <TextInput
                    value={draft.bot_name}
                    onChange={v => setDraft({ ...draft, bot_name: v })}
                    placeholder="ARIA"
                  />
                </Field>
                <Field label="Voice">
                  <Select
                    value={draft.bot_voice_gender}
                    onChange={v => setDraft({ ...draft, bot_voice_gender: v })}
                    options={VOICE_OPTIONS}
                  />
                </Field>
                <Field label="Greeting Style">
                  <Select
                    value={draft.greeting_style}
                    onChange={v => setDraft({ ...draft, greeting_style: v })}
                    options={GREETING_OPTIONS}
                  />
                </Field>
                <Field label="Caller ID Name">
                  <TextInput
                    value={draft.caller_id_name}
                    onChange={v => setDraft({ ...draft, caller_id_name: v })}
                    placeholder="ARIA Screening"
                  />
                </Field>
                <Field label="Outbound Phone Number">
                  <TextInput
                    value={draft.outbound_phone_number}
                    onChange={v => setDraft({ ...draft, outbound_phone_number: v })}
                    placeholder="+14155551234"
                    hint="E.164 format"
                  />
                </Field>
              </div>
            </Section>

            {/* Schedule & Business Hours */}
            <Section title="Schedule & Business Hours" icon={Clock} description="When the bot is allowed to make calls">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <Field label="Timezone">
                  <Select
                    value={draft.timezone}
                    onChange={v => setDraft({ ...draft, timezone: v })}
                    options={TIMEZONE_OPTIONS}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Start Time">
                    <TextInput
                      value={draft.business_hours_start}
                      onChange={v => setDraft({ ...draft, business_hours_start: v })}
                      placeholder="09:00"
                    />
                  </Field>
                  <Field label="End Time">
                    <TextInput
                      value={draft.business_hours_end}
                      onChange={v => setDraft({ ...draft, business_hours_end: v })}
                      placeholder="17:00"
                    />
                  </Field>
                </div>
              </div>
              <Field label="Allowed Days">
                <DayPicker
                  value={draft.allowed_days}
                  onChange={v => setDraft({ ...draft, allowed_days: v })}
                />
              </Field>
            </Section>

            {/* Call Behavior */}
            <Section title="Call Behavior" icon={PhoneCall} description="Duration, retries, and follow-up settings">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                <Field label="Min Duration (sec)">
                  <TextInput
                    value={draft.call_duration_min}
                    onChange={v => setDraft({ ...draft, call_duration_min: parseInt(v) || 180 })}
                    type="number"
                  />
                </Field>
                <Field label="Max Duration (sec)">
                  <TextInput
                    value={draft.call_duration_max}
                    onChange={v => setDraft({ ...draft, call_duration_max: parseInt(v) || 420 })}
                    type="number"
                  />
                </Field>
                <Field label="Max Retries">
                  <TextInput
                    value={draft.max_retries}
                    onChange={v => setDraft({ ...draft, max_retries: parseInt(v) || 3 })}
                    type="number"
                  />
                </Field>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Assessment Detail Level">
                  <Select
                    value={draft.assessment_detail_level}
                    onChange={v => setDraft({ ...draft, assessment_detail_level: v })}
                    options={DETAIL_OPTIONS}
                  />
                </Field>
                <Field label="Follow-up Aggressiveness">
                  <Select
                    value={draft.follow_up_aggressiveness}
                    onChange={v => setDraft({ ...draft, follow_up_aggressiveness: v })}
                    options={FOLLOW_UP_OPTIONS}
                  />
                </Field>
              </div>
              <div className="mt-4 flex items-center gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={draft.auto_update_status ?? true}
                    onChange={e => setDraft({ ...draft, auto_update_status: e.target.checked })}
                    className="w-4 h-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className="text-sm font-medium text-slate-700">Auto-update candidate status after screening</span>
                </label>
              </div>
            </Section>

            {/* Compliance */}
            <Section title="Compliance" icon={Shield} description="Consent recording and custom scripts">
              {isAdmin && (
                <div className="mb-6 pb-6 border-b border-slate-100 space-y-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-slate-800">Custom interview opening</p>
                      <p className="text-xs text-slate-500 mt-1">
                        Replaces the default voice and live-screen opener. Recording consent stays a separate step.
                        Placeholders: {OPENING_PLACEHOLDERS}
                      </p>
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer shrink-0">
                      <input
                        type="checkbox"
                        checked={draft.use_custom_interview_opening ?? false}
                        onChange={e => setDraft({ ...draft, use_custom_interview_opening: e.target.checked })}
                        className="w-4 h-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                      />
                      <span className="text-sm font-medium text-slate-700">Enabled</span>
                    </label>
                  </div>
                  <Field label="Company context (optional, for AI draft)">
                    <textarea
                      value={draft.company_about_blurb || ''}
                      onChange={e => setDraft({ ...draft, company_about_blurb: e.target.value || null })}
                      placeholder="Brief description of your company for AI-assisted drafting..."
                      rows={2}
                      className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent text-sm transition-all outline-none resize-none"
                    />
                  </Field>
                  <Field label="Opening script">
                    <textarea
                      value={draft.interview_opening_script || ''}
                      onChange={e => setDraft({ ...draft, interview_opening_script: e.target.value || null })}
                      placeholder={`Hi {candidate_first_name}, this is {bot_name} from {company_name} about the {role_title} role...`}
                      rows={4}
                      disabled={!(draft.use_custom_interview_opening ?? false)}
                      className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent text-sm transition-all outline-none resize-none disabled:bg-slate-50 disabled:text-slate-400"
                    />
                  </Field>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={suggestingOpening}
                      onClick={async () => {
                        try {
                          setSuggestingOpening(true)
                          const { script } = await suggestInterviewOpening({
                            company_about: draft.company_about_blurb || undefined,
                            tone: draft.greeting_style || 'professional',
                          })
                          setDraft(prev => ({
                            ...prev,
                            use_custom_interview_opening: true,
                            interview_opening_script: script,
                          }))
                        } catch (err) {
                          setError(err.message || 'Failed to suggest opening')
                        } finally {
                          setSuggestingOpening(false)
                        }
                      }}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-brand-700 bg-brand-50 hover:bg-brand-100 rounded-lg transition-colors disabled:opacity-50"
                    >
                      {suggestingOpening ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Mic className="w-3.5 h-3.5" />}
                      Suggest draft with AI
                    </button>
                  </div>
                </div>
              )}
              <Field label="Custom Consent Script (optional)">
                <textarea
                  value={draft.consent_script || ''}
                  onChange={e => setDraft({ ...draft, consent_script: e.target.value || null })}
                  placeholder="Leave empty to use default consent script..."
                  rows={3}
                  className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent text-sm transition-all outline-none resize-none"
                />
              </Field>
            </Section>

            {/* Notifications */}
            <Section title="Notifications" icon={Bell} description="Candidate reminders and notification preferences">
              <div className="space-y-4">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={draft.notification_enabled ?? false}
                    onChange={e => setDraft({ ...draft, notification_enabled: e.target.checked })}
                    className="w-4 h-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className="text-sm font-medium text-slate-700">Send candidate SMS/email reminder before scheduled calls</span>
                </label>
                {(draft.notification_enabled) && (
                  <Field label="Reminder Lead Time (minutes)" hint="How many minutes before the call to send the reminder">
                    <TextInput
                      value={draft.notification_lead_minutes ?? 30}
                      onChange={v => setDraft({ ...draft, notification_lead_minutes: parseInt(v) || 30 })}
                      type="number"
                      placeholder="30"
                    />
                  </Field>
                )}
                <p className="text-xs text-slate-400 italic">Notification dispatch requires Twilio SMS / email integration to be configured.</p>
              </div>
            </Section>

            {/* Sticky Save Bar — visible when settings have unsaved changes */}
            {hasChanges && (
              <div className="sticky bottom-0 z-10 bg-white/95 backdrop-blur-md rounded-2xl ring-1 ring-brand-200 shadow-lg px-6 py-4 flex items-center justify-between mt-2">
                <p className="text-sm font-medium text-slate-600">You have unsaved changes</p>
                <div className="flex gap-3">
                  <button
                    onClick={onCancel}
                    className="px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100 rounded-xl transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={onSave}
                    disabled={saving}
                    className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-xl transition-colors disabled:opacity-50 shadow-sm shadow-brand-200"
                  >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    Save Changes
                  </button>
                </div>
              </div>
            )}
          </div>

  )
}
