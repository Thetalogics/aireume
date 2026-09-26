import { ArrowLeft, ShieldCheck } from 'lucide-react'
import { Link, Navigate, useParams } from 'react-router-dom'

const DOCUMENTS = {
  terms: {
    title: 'Terms of Service',
    intro: 'These terms govern access to and use of ARIA by ThetaLogics.',
    sections: [
      ['Authorized use', 'You may use ARIA only for lawful recruiting and workforce workflows, with appropriate authority to process the information you submit. You are responsible for your users, credentials, configured integrations, and source data.'],
      ['Human decision-making', 'ARIA provides decision support. Employers remain responsible for job requirements, candidate communications, accommodations, notices, and final employment decisions. Automated output must not be the sole basis for a legally significant decision.'],
      ['Acceptable use', 'Do not use ARIA to discriminate unlawfully, bypass consent requirements, probe other tenants, upload malicious content, infringe third-party rights, or attempt to disrupt or reverse engineer the service.'],
      ['Service operation', 'Availability may be affected by maintenance, customer configuration, third-party providers, and events outside reasonable control. Enterprise service levels and support commitments are governed by the applicable order form or agreement.'],
      ['Data and confidentiality', 'Each party retains ownership of its pre-existing materials. Customer data remains customer-controlled and is handled according to the Privacy Notice, configured retention rules, and any applicable data-processing agreement.'],
      ['Suspension and termination', 'Access may be suspended for security threats, unlawful use, non-payment, or material breach. On termination, data export and deletion follow the applicable agreement and configured retention obligations.'],
    ],
  },
  privacy: {
    title: 'Privacy Notice',
    intro: 'This notice explains how ARIA handles account, candidate, and operational data.',
    sections: [
      ['Data processed', 'ARIA may process account details, job descriptions, resumes, interview content, recruiter decisions, usage records, security events, and support communications provided by customers and authorized users.'],
      ['Purposes', 'Data is processed to provide screening and recruiting workflows, secure tenant access, operate integrations, prevent abuse, support customers, meet legal obligations, and improve configured tenant services.'],
      ['AI processing', 'Configured AI providers receive only the information required for the requested function after the application privacy boundary applies supported redaction. Provider selection and subprocessors depend on tenant configuration.'],
      ['Retention and deletion', 'Tenant-specific retention policies govern candidate data. Authorized administrators can request export, anonymization, or erasure. Object-storage deletion failures are retained in a retryable compliance workflow until completed or escalated.'],
      ['Security and isolation', 'ARIA uses tenant-scoped authorization, audit logging, encrypted transport, restricted administrative access, and operational monitoring. Customers must protect credentials and promptly report suspected compromise.'],
      ['Your requests', 'Privacy, access, correction, export, objection, and deletion requests can be submitted through your organization or to the support address below. Identity and authority may be verified before fulfillment.'],
    ],
  },
  subprocessors: {
    title: 'Subprocessors',
    intro: 'The providers below may process customer data when the corresponding capability is enabled.',
    sections: [
      ['Infrastructure and storage', 'The production hosting, database, cache, email, monitoring, and object-storage providers are selected by ThetaLogics for the contracted deployment region and documented in the applicable customer agreement.'],
      ['AI providers', 'Ollama Cloud and Google Gemini may be used for configured language-model functions. Local Ollama deployments can be used when enabled by the customer environment.'],
      ['Voice services', 'LiveKit may process call signaling, audio, transcripts, and delivery metadata when voice screening is enabled. Telephony and speech providers depend on the customer deployment configuration.'],
      ['Changes', 'Material subprocessor changes are communicated through the notice mechanism specified in the applicable agreement. Customers may contact support for the current deployment-specific list and processing locations.'],
    ],
  },
}

export default function LegalPage() {
  const { document } = useParams()
  const content = DOCUMENTS[document]

  if (!content) return <Navigate to="/legal/privacy" replace />

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-5 py-4">
          <Link to="/login" className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-brand-700">
            <ArrowLeft className="h-4 w-4" /> Back to ARIA
          </Link>
          <div className="inline-flex items-center gap-2 text-sm font-bold text-slate-800">
            <ShieldCheck className="h-5 w-5 text-brand-600" /> ThetaLogics
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-5 py-12 sm:py-16">
        <p className="text-sm font-semibold text-brand-700">ARIA trust center</p>
        <h1 className="mt-2 text-3xl font-bold text-slate-950 sm:text-4xl">{content.title}</h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">{content.intro}</p>
        <p className="mt-2 text-sm text-slate-500">Effective September 26, 2026</p>

        <div className="mt-10 divide-y divide-slate-200 border-y border-slate-200">
          {content.sections.map(([heading, body]) => (
            <section key={heading} className="py-6">
              <h2 className="text-lg font-bold text-slate-900">{heading}</h2>
              <p className="mt-2 text-sm leading-7 text-slate-600">{body}</p>
            </section>
          ))}
        </div>

        <div className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-sm font-semibold">
          <Link to="/legal/terms" className="text-brand-700 hover:text-brand-800">Terms</Link>
          <Link to="/legal/privacy" className="text-brand-700 hover:text-brand-800">Privacy</Link>
          <Link to="/legal/subprocessors" className="text-brand-700 hover:text-brand-800">Subprocessors</Link>
          <a href="mailto:support@thetalogics.com" className="text-brand-700 hover:text-brand-800">support@thetalogics.com</a>
        </div>
      </div>
    </main>
  )
}
