import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion, useMotionValueEvent, useReducedMotion, useScroll } from 'framer-motion'
import PaperDrift from '../components/PaperDrift'
import { TRUST } from '../lib/uxLabels'

const BENEFITS = [
  ['One bar for the opening', 'Recruiter and hiring manager work from the same job text and the same must-haves.'],
  ['A written result', 'The screen names skills that matched, skills that did not, experience signals, gaps, and risks. A narrative is added when the model step succeeds.'],
  ['A handoff that stays', 'Compare people, update status, and send the hiring manager a pack from the same opening.'],
  ['Access that matches the job', 'Admin, TA lead, recruiter, hiring manager, and viewer. Viewers can look. They do not advance a candidate.'],
  ['The next conversation, when you turn it on', 'Interview questions, voice screens, and transcript review are available on plans that include them, and only where those services are configured.'],
]

const STEPS = [
  ['Create the opening', 'Create a workspace and add an opening with the job text. Screening starts there, not in a loose upload.'],
  ['Confirm the criteria', 'Where the plan includes it, the hiring manager reviews the criteria. Approval locks that version of the bar.'],
  ['Screen the resumes', 'Upload resumes on the screen page. The run stays tied to the requisition. Batch size follows the plan’s limits.'],
  ['Read the result', 'Open the report. Advance, hold, or compare. The fit score is there so the team can discuss it. Your team still decides.'],
  ['Hand off the shortlist', 'Share the pack and keep status on the pipeline, so the reason travels with the person.'],
]

const PEOPLE = [
  ['Recruiter', 'Start from the opening, not from a blank chat. Screen a professional role when there are more applicants than you can read line by line.'],
  ['Hiring manager', 'Read a pack that uses the criteria you approved. Advance someone only when the gaps are visible.'],
  ['TA lead', 'Assign the opening and see the same candidates your recruiter sees.'],
  ['Admin', 'Resumes stay in a tenant you can permission. Export and erasure are workflows. Audit activity is recorded.'],
]

const WORKSPACE = [
  ['Candidates', 'Everyone screened for the opening, with the result kept next to the person.'],
  ['Report', 'Score, matched skills, missing skills, gaps, and risks. A narrative when the model step completes.'],
  ['Compare', 'A few results side by side, against the same must-haves. Included when the plan allows it.'],
  ['Handoff', 'The people you submit, bundled for the hiring manager. A link can be shared for that pack.'],
  ['Pipeline', 'Status for one opening or across the workspace, when the plan includes it.'],
  ['Interviews', 'Questions from the screen. Voice and transcript review only on plans and deployments where those services are on.'],
  ['Team', 'Invite admins, TA leads, recruiters, hiring managers, and viewers as themselves.'],
]

const FAQ = [
  ['Does ARIA hire people automatically?', 'No. It prepares a screen and a record. Your team decides who advances.'],
  ['Does it replace our ATS?', 'No. It can hold candidates and a pipeline. Posting jobs to boards is not part of the product. Syncing status with another system is not a recruiter setup screen today.'],
  ['What files can we screen?', 'The screen page accepts PDF and DOCX resumes.'],
  ['Is the score always written by a language model?', 'The score is calculated in the product. A language model can add a narrative when that step succeeds. If the model is unavailable, the narrative is not promised.'],
  ['Can we use single sign-on?', 'SAML single sign-on can be configured per workspace when the plan includes it. It needs an identity-provider certificate.'],
  ['Where is candidate data processed?', 'In your ARIA tenant and by the model provider configured for that deployment. The current list is on the subprocessors page. Ask ThetaLogics for the retention terms on your workspace.'],
  ['How much does it cost?', 'A price is not published on this page. Create a workspace to see the plan attached to it, or ask ThetaLogics for the current price sheet.'],
]

const EASE = [0.22, 1, 0.36, 1]
const CARD = 'bg-white rounded-3xl p-6 shadow-[0_12px_32px_rgba(31,78,121,0.06)]'

function Rise({ children, className = '', delay = 0 }) {
  const reduced = useReducedMotion()
  if (reduced) return <div className={className}>{children}</div>
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 32 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-8% 0px' }}
      transition={{ duration: 0.7, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

export default function LandingPage() {
  const reduced = useReducedMotion()
  const progressRef = useRef(0)
  const { scrollYProgress } = useScroll()
  useMotionValueEvent(scrollYProgress, 'change', (value) => {
    progressRef.current = value
  })
  const hero = reduced
    ? {}
    : {
        initial: { opacity: 0, y: 24 },
        animate: { opacity: 1, y: 0 },
        transition: { duration: 0.8, ease: EASE },
      }

  return (
    <div className="landing-page relative min-h-screen text-[#0F2744]">
      <PaperDrift reduced={reduced === true} progressRef={progressRef} />
      <motion.div className="fixed top-0 left-0 right-0 h-px bg-[#3D6E96] origin-left z-30" style={{ scaleX: scrollYProgress }} />
      <header className="sticky top-0 z-20 h-16 px-5 sm:px-8 flex items-center justify-between bg-[#F7F9FC]/85">
        <Link to="/" className="text-lg font-bold tracking-tight">ARIA</Link>
        <nav className="flex items-center gap-5 text-sm font-semibold">
          <a href="#how" className="hidden md:inline text-[#0F2744]">How it works</a>
          <a href="#who" className="hidden md:inline text-[#0F2744]">Who it’s for</a>
          <a href="#questions" className="hidden lg:inline text-[#0F2744]">Questions</a>
          <Link to="/login" className="text-[#1A3148]">Sign in</Link>
          <Link to="/register" className="inline-flex items-center min-h-11 px-4 rounded-full bg-[#1F4E79] text-white">Create workspace</Link>
        </nav>
      </header>

      <main className="relative z-10">
        <section className="relative min-h-[calc(100svh-4rem)] flex items-center px-5 sm:px-10">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-[#F7F9FC]/90 via-[#F7F9FC]/45 to-transparent" />
          <div className="relative max-w-xl py-16">
          <motion.p {...hero} className="text-sm font-bold tracking-wide text-[#1F4E79]">Resume screening workspace</motion.p>
          <motion.h1 {...hero} className="mt-3 text-4xl sm:text-6xl font-semibold tracking-tight leading-[1.05] max-w-[16ch] text-[#0F2744]">
            Know who fits before you read the pile.
          </motion.h1>
          <motion.p
            {...hero}
            transition={{ duration: 0.8, delay: 0.12, ease: EASE }}
            className="mt-5 text-xl leading-relaxed max-w-[46ch] text-[#16324F]"
          >
            ARIA gives a recruiting team one place to screen resumes against an opening, explain the decision, and move the right people toward interview.
          </motion.p>
          <motion.div {...hero} transition={{ duration: 0.8, delay: 0.22, ease: EASE }} className="mt-7 flex flex-wrap gap-3">
            <Link to="/register" className="inline-flex items-center min-h-11 px-5 rounded-full bg-[#1F4E79] text-white">Create workspace</Link>
            <Link to="/login" className="inline-flex items-center min-h-11 px-5 rounded-full bg-white text-[#1F4E79]">Sign in</Link>
          </motion.div>
          </div>
        </section>

        <div className="bg-[#F7F9FC]">
        <section id="problem" className="scroll-mt-20 px-5 sm:px-10 pt-4 pb-16 max-w-6xl mx-auto">
          <Rise>
            <p className="text-2xl sm:text-3xl font-semibold tracking-tight leading-snug max-w-[28ch]">
              A job description is easy to agree in a meeting and hard to apply to the fortieth resume.
            </p>
            <p className="mt-4 text-lg leading-relaxed max-w-[62ch] text-[#16324F]">
              Recruiters build private shortlists. Hiring managers ask why those names advanced. The reason lives in someone’s memory. ARIA starts with the opening, screens the pile against that job, and keeps the reason with the person.
            </p>
          </Rise>
        </section>

        <section className="px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight max-w-[18ch]">What your team can show</h2>
          <p className="mt-4 text-lg leading-relaxed max-w-[62ch] text-[#16324F]">
            Put the opening in first. Screen every file against that opening. Show what matched and what is missing before you ask for interview time.
          </p>
          <div className="mt-8 grid md:grid-cols-2 gap-4">
            {BENEFITS.map(([title, body], index) => (
              <Rise key={title} delay={index * 0.06} className={CARD}>
                <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
                <p className="mt-2 text-[#16324F] leading-relaxed">{body}</p>
              </Rise>
            ))}
          </div>
        </section>

        <section id="how" className="scroll-mt-20 px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <Rise>
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight max-w-[18ch]">How a screen run works</h2>
          </Rise>
          <div className="mt-8 grid sm:grid-cols-2 gap-4">
            {STEPS.map(([title, body], index) => (
              <Rise key={title} delay={index * 0.06}>
                <motion.div
                  className={`${CARD} h-full`}
                  whileHover={reduced ? undefined : { y: -6 }}
                  transition={{ duration: 0.35, ease: EASE }}
                >
                  <p className="text-sm font-bold text-[#1F4E79]">0{index + 1}</p>
                  <h3 className="mt-2 text-xl font-semibold tracking-tight">{title}</h3>
                  <p className="mt-2 text-[#16324F] leading-relaxed">{body}</p>
                </motion.div>
              </Rise>
            ))}
          </div>
        </section>

        <section id="who" className="scroll-mt-20 px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <Rise>
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">Who it is for</h2>
            <p className="mt-4 text-lg leading-relaxed text-[#16324F] max-w-[48ch]">In-house talent teams that screen professional resumes and involve a hiring manager before interviews.</p>
          </Rise>
          <div className="mt-8 grid sm:grid-cols-2 gap-4">
            {PEOPLE.map(([title, body], index) => (
              <Rise key={title} delay={index * 0.06} className={CARD}>
                <h3 className="text-xl font-semibold tracking-tight text-[#1F4E79]">{title}</h3>
                <p className="mt-2 text-[#16324F] leading-relaxed">{body}</p>
              </Rise>
            ))}
          </div>
        </section>

        <section id="workspace" className="px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <div className="grid lg:grid-cols-[0.8fr_1.2fr] gap-8">
            <Rise>
              <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">After the screen</h2>
              <p className="mt-4 text-lg leading-relaxed text-[#16324F] max-w-[36ch]">The shortlist stays attached to the opening that defined the bar. The team does not export a score and start again in a new thread.</p>
            </Rise>
            <dl className={`${CARD} sm:p-8`}>
              {WORKSPACE.map(([title, body], index) => (
                <Rise key={title} delay={index * 0.04}>
                  <div className="grid sm:grid-cols-[9rem_1fr] gap-1 sm:gap-6 py-4 border-t border-[#E4EEF6] first:border-t-0 first:pt-0">
                    <dt className="font-semibold text-[#1F4E79]">{title}</dt>
                    <dd className="text-[#16324F] leading-relaxed">{body}</dd>
                  </div>
                </Rise>
              ))}
            </dl>
          </div>
        </section>

        <section className="px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <div className="grid lg:grid-cols-2 gap-4">
            <Rise className={CARD}>
              <h2 className="text-2xl font-semibold tracking-tight">What stays with your team</h2>
              <ul className="mt-4 space-y-3 text-[#16324F] leading-relaxed">
                <li>ARIA does not hire anyone. It records the screen.</li>
                <li>It is not a careers site and not a job board.</li>
                <li>It does not replace the system where you already post jobs.</li>
                <li>It does not claim a SOC 2, ISO, or HIPAA certification.</li>
              </ul>
            </Rise>
            <Rise delay={0.08} className={CARD}>
              <h2 className="text-2xl font-semibold tracking-tight">Where the files live</h2>
              <p className="mt-4 text-[#16324F] leading-relaxed">
                Screens are stored in your tenant. Export and erasure are workflows an operator can run. Candidate content is processed in that tenant and by the model provider configured for the deployment.
              </p>
              <p className="mt-4 text-[#16324F] leading-relaxed">{TRUST.authFooter}</p>
              <a href="/legal/subprocessors" className="inline-flex items-center min-h-11 mt-3 text-[#1F4E79] font-semibold">Subprocessors</a>
            </Rise>
          </div>
        </section>

        <section id="questions" className="scroll-mt-20 px-5 sm:px-10 pb-16 max-w-3xl mx-auto">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">Questions buyers ask</h2>
          <div className="mt-6">
            {FAQ.map(([question, answer]) => (
              <details key={question} className="border-t border-[#E4EEF6] group">
                <summary className="cursor-pointer list-none flex items-center justify-between gap-4 py-4 min-h-11 text-lg font-semibold">
                  {question}
                  <span aria-hidden="true" className="text-[#1F4E79] group-open:rotate-45 transition-transform">+</span>
                </summary>
                <p className="pb-4 text-[#16324F] leading-relaxed">{answer}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="px-5 sm:px-10 pb-16 max-w-6xl mx-auto">
          <Rise>
            <div className="rounded-3xl bg-[#1F4E79] text-white p-6 sm:p-10 grid lg:grid-cols-[1.2fr_auto] gap-6 items-center">
              <div>
                <h2 className="text-3xl font-semibold tracking-tight">Start with one opening</h2>
                <p className="mt-3 max-w-[48ch] text-[#F7FBFF] leading-relaxed text-lg">Create a workspace, add the requisition, and screen the first pile against criteria a hiring manager can approve.</p>
              </div>
              <Link to="/register" className="inline-flex items-center justify-center min-h-11 px-5 rounded-full bg-white text-[#1F4E79]">Create workspace</Link>
            </div>
          </Rise>
        </section>
        </div>
      </main>

      <footer className="relative z-10 bg-[#F7F9FC] px-5 sm:px-8 py-10 text-base text-[#16324F]">
        <p className="text-2xl font-semibold tracking-tight text-[#1A3148]">ARIA</p>
        <p className="mt-2 max-w-[42ch]">Resume screening by ThetaLogics. A workspace for the screen, not another inbox.</p>
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2">
          {TRUST.legalLinks.map((link) => (
            <a key={link.label} href={link.href} className="inline-flex items-center min-h-6 text-[#1F4E79]">{link.label}</a>
          ))}
        </div>
      </footer>
    </div>
  )
}
