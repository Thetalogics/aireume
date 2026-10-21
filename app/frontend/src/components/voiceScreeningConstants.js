import {
  Phone, Calendar, PhoneCall, PhoneOff, CheckCircle2, XCircle, AlertTriangle, X,
} from 'lucide-react'

export const OPENING_PLACEHOLDERS = '{candidate_first_name}, {role_title}, {company_name}, {bot_name}'

export const STATUS_CONFIG = {
  scheduled:  { label: 'Scheduled',  color: 'bg-blue-100 text-blue-700',   icon: Calendar },
  ringing:    { label: 'Ringing',    color: 'bg-amber-100 text-amber-700', icon: Phone },
  in_progress:{ label: 'In Progress',color: 'bg-green-100 text-green-700', icon: PhoneCall },
  completed:  { label: 'Completed',  color: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2 },
  failed:     { label: 'Failed',     color: 'bg-red-100 text-red-700',     icon: XCircle },
  no_answer:  { label: 'No Answer',  color: 'bg-orange-100 text-orange-700', icon: PhoneOff },
  escalated:  { label: 'Escalated',  color: 'bg-purple-100 text-purple-700', icon: AlertTriangle },
  cancelled:  { label: 'Cancelled',  color: 'bg-slate-100 text-slate-600', icon: X },
}

export const VOICE_OPTIONS = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
]
export const GREETING_OPTIONS = [
  { value: 'professional', label: 'Professional' },
  { value: 'casual', label: 'Casual' },
  { value: 'friendly', label: 'Friendly' },
]
export const DETAIL_OPTIONS = [
  { value: 'brief', label: 'Brief Summary' },
  { value: 'full', label: 'Full Detail' },
]
export const FOLLOW_UP_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]
export const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC (Coordinated Universal Time)' },
  { value: 'America/New_York', label: 'US Eastern (ET)' },
  { value: 'America/Chicago', label: 'US Central (CT)' },
  { value: 'America/Denver', label: 'US Mountain (MT)' },
  { value: 'America/Los_Angeles', label: 'US Pacific (PT)' },
  { value: 'America/Anchorage', label: 'US Alaska (AKT)' },
  { value: 'Pacific/Honolulu', label: 'US Hawaii (HST)' },
  { value: 'Europe/London', label: 'UK (GMT/BST)' },
  { value: 'Europe/Berlin', label: 'Central Europe (CET)' },
  { value: 'Europe/Paris', label: 'France (CET)' },
  { value: 'Europe/Helsinki', label: 'Eastern Europe (EET)' },
  { value: 'Asia/Dubai', label: 'UAE (GST)' },
  { value: 'Asia/Kolkata', label: 'India (IST)' },
  { value: 'Asia/Bangkok', label: 'Thailand (ICT)' },
  { value: 'Asia/Singapore', label: 'Singapore (SGT)' },
  { value: 'Asia/Shanghai', label: 'China (CST)' },
  { value: 'Asia/Tokyo', label: 'Japan (JST)' },
  { value: 'Australia/Sydney', label: 'Australia Eastern (AEST)' },
  { value: 'Pacific/Auckland', label: 'New Zealand (NZST)' },
]
