/**
 * Centralized user-facing labels — single vocabulary across the app.
 * API paths may still use legacy names (templates, voice, etc.).
 */

export const NAV = {
  home: 'Home',
  requisitions: 'Requisitions',
  roles: 'Roles',
  analyze: 'Analyze',
  screenResumes: 'Screen',
  candidates: 'Candidates',
  activity: 'Activity',
  interviews: 'Interviews',
  compare: 'Compare',
  pipeline: 'Pipeline',
  projects: 'Projects',
  analytics: 'Analytics',
  team: 'Team',
  settings: 'Settings',
  interviewReview: 'Interview Review',
  hmDashboard: 'My Openings',
}

export const ANALYZE = {
  pageTitle: 'New Analysis',
  pageTitleRequisition: 'Screen candidates',
  subtitle: 'Score resumes against a role in three steps — job description, upload, then ranked results.',
  subtitleRequisition: 'Choose an opening, confirm skills, upload resumes, and send results to the pipeline.',
  step1Title: 'Step 1: Job Description & Skill Review',
  step1TitleRequisition: 'Step 1: Opening & skills',
  screeningFor: 'Screening for',
  changeRequisition: 'Change opening',
  openRequisition: 'Open requisition',
  selectOpeningTitle: 'Select an opening to start',
  selectOpeningHint: 'Every screen run should tie to a requisition so intake, criteria, and pipeline stay in sync.',
  searchRequisitions: 'Search requisitions…',
  noRequisitionsTitle: 'No requisitions yet',
  noRequisitionsHint: 'Create an opening first, then return here to screen candidates into its pipeline.',
  createRequisitionCta: 'Create requisition',
  quickScreenLink: 'Quick screen without a requisition',
  backToRequisitions: 'Back to requisition selection',
  jdReferenceLabel: 'Job description',
  viewFullJd: (n) => `View full JD (${n.toLocaleString()} word${n === 1 ? '' : 's'})`,
  selectRequisitionError: 'Select a requisition before screening.',
  adHocDisabledHint: 'Your workspace requires screening through a requisition. Create or select an opening first.',
  skillsFromCalibrated: 'Skills loaded from calibrated criteria',
  skillsFromRequisition: 'Skills loaded from requisition',
}

export const REQUISITIONS = {
  pageTitle: 'Requisitions',
  pageSubtitle: 'Calibrated openings — HM intake, criteria, and pipeline in one place',
  createCta: 'New Requisition',
  emptyHint: 'Create a requisition to run intake, calibrate must-haves, and source candidates.',
  hmPageTitle: 'My Openings',
  hmPageSubtitle: 'Approve intake, review submissions, and track pipeline for your roles',
  intakeTab: 'Intake',
  intakeSaved: 'Intake saved',
  intakeSaveHint: 'Add screen-focus topics or must-haves. Screen after save; HM approval locks criteria v1.',
  intakeSuggestCta: 'Suggest from job description',
  intakeSuggestDone: 'Fields filled from JD — review and save',
  intakeStepIntake: '1. HM intake',
  intakeStepScreen: '2. Screen',
  intakeStepRefine: '3. Criteria locked',
  intakeStepRefineDraft: '3. Awaiting HM approval',
  intakeHmSaveApprove: 'Approve & lock criteria',
  intakeChangesRequested: 'HM requested changes — update intake and resubmit',
  assignRecruiter: 'Assign recruiter',
  assignRecruiterHint: 'TA lead assigns the recruiter who owns sourcing and screening.',
  openedOnBehalfOf: 'Opened on behalf of HM',
  searchBriefTab: 'Sourcing brief',
  searchBriefHint: 'Strategy updated from HM feedback and recruiter notes.',
  hmRejectFeedbackTitle: 'HM feedback — update sourcing',
  applyFeedbackCta: 'Apply to sourcing brief',
  submitNotesLabel: 'Recruiter note for HM (optional)',
  requestOpeningCta: 'Request new opening',
  routingPolicyLabel: 'Screening routing thresholds',
  suggestedActionSubmit: 'Suggested: submit to HM',
  suggestedActionAi: 'Suggested: schedule AI interview',
  sourcingSaveCta: 'Save sourcing plan',
  sourcingTabTitle: 'Sourcing plan',
  sourcingTabHint: 'Track channels and strategy — upload or pool candidates, then screen against this requisition.',
  sourcingChannelsLabel: 'Active sourcing channels',
  sourcingLinkedInHint: 'No API integration — source on LinkedIn manually, then upload resumes or add from pool.',
  sourcingTab: 'Sourcing',
  openRequestsTitle: 'Opening requests',
  openRequestsSubtitle: 'HM-requested roles awaiting TA assignment',
  openRequestsEmpty: 'No opening requests yet.',
  openRequestsNav: 'Opening requests',
  intakeUnsaved: 'Unsaved changes',
  intakeAutoSaveHint: 'Changes save automatically while you edit.',
  intakeSaving: 'Saving…',
  intakeLeaveUnsavedTitle: 'Could not save intake',
  intakeLeaveUnsavedMessage: 'Your latest edits were not saved. Discard them and leave, or stay on Intake to retry.',
  intakeDiscardLeave: 'Discard & leave',
  intakeStay: 'Stay on intake',
  hmReviewPackCta: 'HM review pack',
  hmReviewPackHint: 'Share shortlisted candidates with HM (after screening)',
  criteriaTab: 'Criteria',
  pipelineTab: 'Pipeline',
  overviewTab: 'Overview',
  calibrateCta: 'Calibrate criteria',
  approveIntakeCta: 'Approve intake',
  requestChangesCta: 'Request changes',
  submitToHmCta: 'Submit to HM',
  statusDraft: 'Draft',
  hmAssignHint: 'Assign a primary hiring manager for HM intake approval and pipeline ownership.',
  hmAssignCta: 'Save hiring manager',
  hmAssignSelfCta: 'Use me as HM (admin)',
  hmAssignOverview: 'Hiring manager',
  hmInviteCta: 'Invite HM',
  hmInviteTitle: 'Invite hiring manager',
  hmInviteHint: 'Creates a hiring manager account and assigns them to this requisition (admin only).',
  hmInviteSuccess: 'Hiring manager invited and assigned',
  hmRequestCta: 'Request HM',
  hmRequestTitle: 'Request hiring manager access',
  hmRequestHint: 'Submit the HM email for tenant admin approval. No account is created until approved.',
  hmRequestSuccess: 'HM access requested — waiting for admin approval',
  hmRequestPending: 'HM access requested',
  hmRequestApproveCta: 'Approve & assign',
  hmRequestRejectCta: 'Reject request',
  notCalibratedWarning: 'Save intake and assign a hiring manager before screening. Calibrate when HM feedback changes the bar.',
  jdPreviewLabel: 'JD preview',
  jdReferenceLabel: 'Job description (reference)',
  jdReferenceHint: 'Read-only source text used for intake suggestions and screening.',
  jdViewFullCta: 'View full job description',
  jdWordCount: (n) => `${n.toLocaleString()} word${n === 1 ? '' : 's'}`,
  jdShowMore: 'Show more',
  jdShowLess: 'Show less',
  jdModalTitle: 'Full job description',
  jdEmpty: 'No job description on this requisition.',
  nextActionAssignHm: 'Assign a hiring manager',
  nextActionCompleteIntake: 'Complete HM intake',
  nextActionHmApproval: 'HM approval required',
  nextActionReadyScreen: 'Ready to screen',
  nextActionReviewIntake: 'Review and approve intake',
  nextActionGoIntake: 'Go to intake',
  nextActionScreenPending: (n) => `Screen ${n} pending candidate${n === 1 ? '' : 's'}`,
  screenCandidateBlocked: 'Complete the steps above before screening',
  screeningModeLabel: 'Screening entry point',
  screeningModeHint: 'Require recruiters to select a requisition before screening, or allow ad-hoc paste/upload flows.',
  screeningModeRequired: 'Requisition required — select an opening on Screen',
  screeningModeAdHoc: 'Allow ad-hoc screening — paste JD without a requisition',
}

export const ROLES = {
  pageTitle: 'Roles',
  pageSubtitle: 'Job descriptions, skills, and screening weights for each opening',
  createCta: 'New Role',
  emptyHint: 'Create a role to start screening resumes against it.',
}

export const INTERVIEW = {
  /** AI bot calls the candidate */
  aiScreenCall: 'AI Screen Call',
  aiScreenCallHint: 'ARIA calls the candidate — Quick, Standard, or Deep',
  newScreenCall: 'New AI Screen Call',
  newScreenCallSubtitle: 'Pick a candidate and role, then choose call depth',
  rescheduleCall: 'Reschedule call',
  /** Recruiter calls with kit */
  liveScreenKit: 'Live Screen Kit',
  liveScreenKitHint: 'You call the candidate using ARIA\'s interview questions and scorecard',
  hubTitle: 'Interviews',
  hubSubtitle: 'AI phone screens — scheduled, in progress, and completed',
  quick: 'Quick Screen',
  standard: 'Standard Interview',
  deep: 'Deep Assessment',
  viewBySession: 'By session',
  viewByCandidate: 'By candidate',
  needsAttention: 'Needs attention',
  upcoming: 'Upcoming',
  recent: 'Recent',
  settingsLink: 'Interview settings',
  voiceUnavailable:
    'AI phone screening is temporarily unavailable. Please try again later.',
}

/** User-safe message for failed voice sessions (hides raw dispatch errors). */
export function formatVoiceSessionError(errorLog) {
  const text = String(errorLog || '').trim()
  if (!text) return INTERVIEW.voiceUnavailable
  const lowered = text.toLowerCase()
  if (
    lowered.includes('livekit')
    || lowered.includes('dispatch')
    || lowered.includes('unreachable')
    || lowered.includes('connect')
    || lowered.includes('sip')
    || lowered.includes('agent')
    || lowered.includes('temporarily unavailable')
  ) {
    return INTERVIEW.voiceUnavailable
  }
  return text
}

export const LIVE_SCREEN = {
  readinessLoading: 'Interview kit is still generating',
  readinessLoadingHint: 'Targeted questions are being prepared from the screening analysis. Start the live call once the kit is ready.',
  readinessEmpty: 'No interview questions available yet',
  readinessEmptyHint: 'The AI kit did not generate questions for this report. You can use standard probe questions based on skill gaps.',
  useFallbackCta: 'Start with standard questions',
  waitCta: 'Wait for kit',
  fallbackBadge: 'Standard questions',
  teleprompter: 'Guided',
  checklist: 'All questions',
  endCall: 'End call & debrief',
  debriefTitle: 'Post-call debrief',
  debriefHint: 'Capture your recommendation while the conversation is fresh.',
  roleMismatchTitle: 'Low pre-screen fit',
  roleMismatchHint: 'Resume and role may not align — confirm the candidate applied to the correct opening before proceeding.',
  prescreenNote: 'Pre-screen signal only — not your interview decision',
  resumePanel: 'Resume',
  briefing: 'Candidate briefing',
  listenFor: 'What to listen for',
  followUps: 'Follow-up questions',
  questionProgress: (current, total) => `Question ${current} of ${total}`,
  markedAsked: 'Mark as asked',
}

export const REPORT = {
  title: 'Screening Report',
  exportMenu: 'Export & share',
}

export const PIPELINE = {
  global: 'Pipeline',
  globalHint: 'All candidates by status',
  project: 'Project pipeline',
  role: 'Role pipeline',
}

/** Trust & compliance copy — cloud SaaS positioning */
export const TRUST = {
  authFooter: 'Tenant-isolated workspaces · Encrypted in transit · GDPR deletion and export controls',
  aiProcessingTitle: 'AI & data processing',
  aiProcessingBody:
    'Resume, job description, and interview content are processed by secure AI providers to generate screening scores, narratives, and interview plans. Data is stored in your tenant workspace and never used to train public models.',
  aiProcessingAck:
    'I understand that candidate and job data are processed by AI providers to deliver screening analysis.',
  aiSubprocessors: ['Ollama Cloud', 'Google Gemini (when configured)', 'LiveKit (voice screening)'],
  legalLinks: [
    { label: 'Terms', href: '/legal/terms' },
    { label: 'Privacy', href: '/legal/privacy' },
    { label: 'Subprocessors', href: '/legal/subprocessors' },
    { label: 'Support', href: 'mailto:support@thetalogics.com' },
  ],
}

export const ANALYTICS = {
  pageTitle: 'Analytics',
  pageSubtitle: 'Command center, deep-dive explore, custom reports, and metric documentation',
  lastUpdated: 'Last updated',
  periodLabel: 'Analytics period',
  presetLabel: 'Preset',
  customRangeLabel: 'Custom range',
  compareActiveHint: 'vs prior period',
  startDateLabel: 'Custom range start',
  endDateLabel: 'Custom range end',
  compareLabel: 'Compare to prior period',
  refreshLabel: 'Refresh all analytics',
  viewLabel: 'Analytics view',
  tabsLabel: 'Analytics hub sections',
  sectionNavLabel: 'Analytics sections',
  navOverview: 'Overview',
  navExplore: 'Explore',
  navReports: 'Reports',
  navDocs: 'Metrics & BI',
  overviewHint: 'What needs attention right now — click a card to drill into Explore.',
  exploreCta: 'Open Explore',
  saveViewLabel: 'Save this view',
  pinViewLabel: 'Pin current filters',
  savedViewsLabel: 'Saved views',
  miniTrendTitle: 'Screening volume (last 14 days)',
  emptyTrend: 'No screening activity in this period yet.',
  attentionStale: 'Stale pipeline',
  attentionStaleHint: 'Candidates stuck in pipeline — review in Funnel',
  attentionEmptyReqs: 'Empty pipelines',
  attentionEmptyReqsHint: 'Open requisitions with zero candidates',
  viewerScopeHint: 'Some metrics are aggregated; candidate emails are masked for your role.',
  reportsSubtitle: 'Build a custom report: pick a data source, choose columns, optionally group by, then export or schedule.',
  reportSteps: { source: 'Source', columns: 'Columns', filters: 'Filters & name', run: 'Run & save' },
  reportPickSource: 'Choose a data source',
  reportPickColumns: 'Choose columns to include',
  reportGroupByLabel: 'Group by (optional)',
  reportFiltersHint: 'Date range comes from the time control above.',
  reportNamePlaceholder: 'Report name (required to save)',
  reportNameRequired: 'Enter a report name before saving.',
  reportRunTitle: 'Preview or export',
  reportTemplatesLabel: 'Quick templates',
  previewLabel: 'Preview table',
  saveReportLabel: 'Save report',
  savedReportsLabel: 'Saved reports',
  emptySavedReports: 'No saved reports yet. Build one above and save it.',
  sharedLabel: 'Shared with team',
  scheduledReportsLabel: 'Scheduled delivery',
  pickReportLabel: 'Select saved report',
  recipientsPlaceholder: 'email@company.com, teammate@company.com',
  scheduleLabel: 'Schedule',
  docsSubtitle: 'Plain-English definitions for every KPI, plus BI export endpoints.',
  glossaryTitle: 'Metric glossary',
  nextLabel: 'Next',
  backLabel: 'Back',
  sliceScreening: 'Screening',
  sliceFunnel: 'Pipeline',
  sliceInterviews: 'Interviews',
  sliceTeam: 'Team',
  sliceHm: 'Hiring manager',
  sliceExecutive: 'Executive',
  sliceAts: 'ATS',
  sliceReports: 'Reports',
  attentionHm: 'Hiring manager review',
  attentionHmHint: 'Submissions awaiting hiring manager outcome — click to view',
  filterAppliesHint: 'Requisition filter applies to this view.',
  filterRecruiterHint: 'Recruiter filter applies to team activity.',
  filterReqInterviewsHint: 'Requisition filter scopes interviews to candidates on that req.',
  errorTitle: 'Analytics unavailable',
  errorGeneric: 'Could not load analytics data. Check your connection and try again.',
  retryLabel: 'Retry',
  biManifestTitle: 'BI export endpoints',
  biManifestHint: 'Use these API endpoints for warehouse / BI tooling.',
  emptyScreening: 'No screenings in this period. Run an analysis to populate this view.',
  emptyInterviews: 'No completed interviews with both resume and call scores yet.',
  emptyHm: 'No submissions waiting on hiring manager review.',
  emptyAts: 'No ATS sync failures in this period.',
  emptyFunnel: 'No pipeline movement in this period.',
  emptyTeam: 'No recruiter activity logged in this period.',
  emptyLeadership: 'No executive risk flags right now.',
}

/** Plan features that are deprecated or not offered — hidden in UI */
export const DEPRECATED_PLAN_FEATURES = [
  'On-premise deployment option',
]

export function sanitizePlanFeatures(features) {
  if (!Array.isArray(features)) return []
  return features.filter((f) => !DEPRECATED_PLAN_FEATURES.includes(f))
}
