import { REQUISITIONS } from '../../lib/uxLabels'

export function countJdWords(text) {
  if (!text?.trim()) return 0
  return text.trim().split(/\s+/).filter(Boolean).length
}

export function resolveRequisitionNextAction({ req, intakeGate, canWrite, isHiringManager, isAdmin, pendingCount }) {
  if (!req) return null

  if (isHiringManager && req.intake_status === 'pending_hm') {
    return {
      stepLabel: 'Your action',
      title: REQUISITIONS.nextActionReviewIntake,
      description: 'Recruiter submitted intake for this role. Approve to lock criteria v1 or request changes.',
      cta: REQUISITIONS.approveIntakeCta,
      action: 'intake',
      tone: 'brand',
    }
  }

  if (canWrite && intakeGate && !intakeGate.hm_assigned) {
    if (req.hm_request_status === 'pending' && req.hm_request_email) {
      return {
        stepLabel: 'Step 1 of 3',
        title: 'Waiting for HM approval',
        description: `Access requested for ${req.hm_request_email}. Approve the request or assign another hiring manager.`,
        cta: 'Review HM request',
        action: 'hm',
        tone: 'amber',
      }
    }
    const adminNote = isAdmin && intakeGate.intake_has_minimum_content
      ? ' Admin override allows screening once intake is saved.'
      : ''
    return {
      stepLabel: 'Step 1 of 3',
      title: REQUISITIONS.nextActionAssignHm,
      description: `${REQUISITIONS.hmAssignHint}${adminNote}`,
      cta: REQUISITIONS.hmAssignCta,
      action: 'hm',
      tone: 'amber',
    }
  }

  if (canWrite && intakeGate && !intakeGate.intake_has_minimum_content) {
    return {
      stepLabel: 'Step 2 of 3',
      title: REQUISITIONS.nextActionCompleteIntake,
      description: 'Add screen-focus topics or must-haves on the Intake tab, or use “Suggest from job description”.',
      cta: REQUISITIONS.nextActionGoIntake,
      action: 'intake',
      tone: 'brand',
    }
  }

  if (intakeGate?.blocks && intakeGate?.requires_hm_approval && req.intake_status !== 'approved') {
    return {
      stepLabel: 'Waiting on HM',
      title: REQUISITIONS.nextActionHmApproval,
      description: intakeGate.warning || 'Save intake and request HM approval per tenant policy.',
      cta: REQUISITIONS.nextActionGoIntake,
      action: 'intake',
      tone: 'amber',
    }
  }

  if (canWrite && intakeGate?.intake_screening_ready && !intakeGate?.blocks) {
    const hmNote = req.intake_status === 'pending_hm'
      ? ' HM approval is optional for screening but locks criteria when approved.'
      : ''
    return {
      stepLabel: 'Ready',
      title: REQUISITIONS.nextActionReadyScreen,
      description: (pendingCount > 0
        ? `${pendingCount} candidate${pendingCount === 1 ? '' : 's'} in pipeline — open Analyze with this requisition loaded.`
        : 'Intake is set — open Analyze to screen resumes against this role.') + hmNote,
      cta: pendingCount > 0
        ? REQUISITIONS.nextActionScreenPending(pendingCount)
        : 'Screen candidates',
      action: 'screen',
      tone: 'emerald',
    }
  }

  return null
}

export function parseScoringWeights(sw) {
  if (!sw) return { resume: 40, interview: 60 }
  try {
    const obj = typeof sw === 'string' ? JSON.parse(sw) : sw
    return {
      resume: Math.round((obj.resume_weight ?? 0.4) * 100),
      interview: Math.round((obj.interview_weight ?? 0.6) * 100),
    }
  } catch {
    return { resume: 40, interview: 60 }
  }
}
