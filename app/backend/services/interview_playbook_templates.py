"""
Domain-agnostic recruiter screen playbook templates.

Thread copy is parameterized by role family, skills, and candidate context.
Used by interview_kit_generator to produce conversation arcs (not keyword lists).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# Role families supported across industries
ROLE_FAMILIES = ("general", "talent_acquisition", "sap", "finance", "engineering", "healthcare", "sales")

OPEN_TEMPLATES: Dict[str, str] = {
    "general": (
        "Hi {name}, thanks for your time. I've reviewed your background as a {role_context}. "
        "I'd like to understand what you've owned recently, where you're strongest, and any areas "
        "we should clarify for this {role_title} role. This is a conversation, not a test."
    ),
    "talent_acquisition": (
        "Hi {name}, thanks for joining. Your recruiting background stood out — I'd like to hear "
        "how you've run searches lately: full-cycle ownership, hiring manager partnership, and "
        "where you've had to push back. Goal is to see fit for this {role_title} opening."
    ),
    "sap": (
        "Hi {name}, thanks for making time. Your MM/ERP background is relevant here. I'd like to "
        "understand your last engagement — phase, modules, what you owned through go-live — and where "
        "integrations fit. Conversational screen for our {role_title} need."
    ),
    "finance": (
        "Hi {name}, appreciate you speaking with me. I want to understand the analyses and "
        "reporting you've owned, how you handle tight deadlines, and fit for this {role_title} role."
    ),
    "engineering": (
        "Hi {name}, thanks for your time. I'd like to dig into systems you've built or maintained, "
        "how you handle production issues, and whether your experience matches this {role_title} role."
    ),
    "healthcare": (
        "Hi {name}, thanks for speaking today. I'd like to understand your clinical or operational "
        "scope, compliance-aware work, and fit for this {role_title} position."
    ),
    "sales": (
        "Hi {name}, thanks for your time. I want to hear how you've carried quota, managed pipeline, "
        "and navigated tough deals — to see alignment with this {role_title} opportunity."
    ),
}

CLOSE_TEMPLATES: Dict[str, str] = {
    "general": (
        "What are you looking for in your next move — type of work, team, and start timing? "
        "I'll share next steps if we're aligned."
    ),
    "talent_acquisition": (
        "What kind of reqs and hiring volume are you targeting next? Notice period and "
        "onsite/hybrid preference?"
    ),
    "sap": (
        "What engagement length and client interaction are you looking for? Travel or onsite "
        "expectations and availability?"
    ),
    "finance": (
        "What reporting cadence and tools do you want in your next role? Notice period and start date?"
    ),
    "engineering": (
        "What stack and team shape are you targeting? Remote/onsite preference and notice period?"
    ),
    "healthcare": (
        "What setting and schedule work for you next? Licenses, certifications, and start timing?"
    ),
    "sales": (
        "What territory, product, and quota band are you targeting? Travel and start date?"
    ),
}

CLOSE_LOGISTICS = [
    "Notice period / availability",
    "Location, travel, or onsite expectations",
    "Contract vs permanent (if applicable)",
]


def _ctx(
    *,
    name: str,
    role_title: str,
    role_context: str,
    company: str,
    title: str,
    skill: str,
    context: str,
    years: float,
) -> Dict[str, str]:
    return {
        "name": name or "there",
        "role_title": role_title or "role",
        "role_context": role_context or "professional",
        "company": company or "your last employer",
        "title": title or "your role",
        "skill": skill or "this area",
        "context": context or "this role",
        "years": str(int(years)) if years else "several",
    }


def ownership_thread_steps(family: str, ctx: Dict[str, str]) -> List[Dict[str, Any]]:
    """Thread 1 — core role ownership (domain-specific arc)."""
    c = ctx
    if family == "talent_acquisition":
        return [
            {
                "text": f"Walk me through your most recent search you owned end to end — role, "
                f"timeline, and your part from intake to offer.",
                "what_to_listen_for": ["Full-cycle ownership", "Stakeholders", "Outcome"],
                "follow_ups": [
                    "What made that search difficult?",
                    f"If vague: What did you personally do versus the team?",
                ],
            },
            {
                "text": "How do you partner with hiring managers when reqs shift mid-search?",
                "what_to_listen_for": ["Pushback", "Expectation setting", "Communication"],
                "follow_ups": ["Give me one example where you had to reset scope."],
            },
        ]
    if family == "sap":
        return [
            {
                "text": f"Talk me through your most recent MM/ERP engagement — client context, "
                f"phase, and which modules you personally owned.",
                "what_to_listen_for": ["Implementation phase", "Modules", "Personal deliverables"],
                "follow_ups": [
                    "What did you sign off before go-live?",
                    f"If vague: What did you configure or design yourself?",
                ],
            },
            {
                "text": "How much was S/4 versus ECC — and where did MM change most for that client?",
                "what_to_listen_for": ["S/4 specifics", "Migration context", "Depth beyond keyword"],
                "follow_ups": ["One config or process decision that stuck after go-live."],
            },
        ]
    if family == "engineering":
        return [
            {
                "text": f"Pick a recent system or feature you owned — scope, stack, and what you "
                f"shipped or operated in production.",
                "what_to_listen_for": ["Ownership", "Stack depth", "Production impact"],
                "follow_ups": [
                    "What broke in prod and how did you respond?",
                    f"If vague: What did you write or design personally?",
                ],
            },
        ]
    if family == "finance":
        return [
            {
                "text": f"Describe an analysis or reporting cycle you owned at {c['company']} — "
                f"tools, stakeholders, and deliverable.",
                "what_to_listen_for": ["End-to-end ownership", "Tools", "Business impact"],
                "follow_ups": ["What decision did your work inform?"],
            },
        ]
    if family == "sales":
        return [
            {
                "text": f"Walk me through a deal you carried from pipeline to close — size, "
                f"stakeholders, and what you personally drove.",
                "what_to_listen_for": ["Quota impact", "Deal craft", "Personal contribution"],
                "follow_ups": ["What almost killed the deal?"],
            },
        ]
    if family == "healthcare":
        return [
            {
                "text": f"At {c['company']}, what patient or operational scope did you own as "
                f"{c['title']}?",
                "what_to_listen_for": ["Scope", "Compliance awareness", "Outcomes"],
                "follow_ups": ["How did you handle a high-pressure clinical or ops situation?"],
            },
        ]
    # general
    return [
        {
            "text": f"Walk me through your most recent role — what you owned day to day at "
            f"{c['company']} and your biggest deliverable.",
            "what_to_listen_for": ["Scope owned", "Tools/skills used", "Measurable outcome"],
            "follow_ups": [
                f"If vague: What did you personally deliver versus the team?",
                "What would your manager say you were accountable for?",
            ],
        },
        {
            "text": f"Which project best shows your strength in {c['skill']}?",
            "what_to_listen_for": ["Project context", "Personal contribution", "Outcome"],
            "follow_ups": ["What was the hardest part of that work?"],
        },
    ]


def risk_gap_thread_steps(family: str, skill: str, ctx: Dict[str, str]) -> List[Dict[str, Any]]:
    """Thread 2 — weighted gap / risk probe."""
    c = ctx
    skill = skill or "this requirement"
    if family == "sap":
        return [
            {
                "text": f"Integrations matter for this role — especially {skill}. On your last "
                f"program, where did that sit and what was your hands-on part?",
                "what_to_listen_for": [
                    f"Practical {skill} exposure",
                    "Functional vs integration team boundary",
                    "Incident examples",
                ],
                "follow_ups": [
                    f"Describe a time {skill} failure impacted MM or logistics — what did you check first?",
                    f"If weak: How comfortable are you owning {skill} errors tomorrow?",
                ],
            },
        ]
    if family == "talent_acquisition":
        return [
            {
                "text": f"How have you used {skill} on a search you owned, and what did you do personally?",
                "what_to_listen_for": [f"{skill} in practice", "Examples", "Outcome"],
                "follow_ups": [f"If light: Any adjacent experience with {skill}?"],
            },
        ]
    if family == "engineering":
        return [
            {
                "text": f"Tell me about a time you used {skill} under pressure — your decisions and the outcome.",
                "what_to_listen_for": [f"Real {skill} usage", "Trade-offs", "Debugging"],
                "follow_ups": [f"What would you do differently next time with {skill}?"],
            },
        ]
    return [
        {
            "text": f"Where has {skill} shown up in your work, and what was your hands-on contribution?",
            "what_to_listen_for": [
                f"Practical {skill} examples",
                "Personal vs team contribution",
                "Honest depth",
            ],
            "follow_ups": [
                f"Pick one example — what did you personally do with {skill}?",
                f"If weak: Any adjacent experience that transfers to {skill}?",
            ],
        },
    ]


def ways_of_working_step(family: str, ctx: Dict[str, str]) -> Dict[str, Any]:
    """Thread 3 — single stakeholder / judgment probe."""
    if family == "talent_acquisition":
        text = "Tell me about a hiring manager who kept moving the goalposts — how did you handle it?"
    elif family == "sap":
        text = "Describe a go-live or hypercare issue you handled — your role and what you did under pressure."
    elif family == "engineering":
        text = "Tell me about a production incident you led or contributed to — timeline and resolution."
    elif family == "finance":
        text = "Tell me about a deadline-driven analysis where the data didn't cooperate — what did you do?"
    elif family == "sales":
        text = "Describe a deal where the champion went quiet — how did you re-engage or qualify out?"
    elif family == "healthcare":
        text = "Describe a situation where policy or compliance conflicted with operational urgency — your call."
    else:
        text = (
            f"Tell me about a time you had to push back on a stakeholder request in your "
            f"{ctx.get('role_context', 'work')} — what was at stake?"
        )
    return {
        "text": text,
        "what_to_listen_for": ["Ownership", "Stakeholder handling", "Outcome"],
        "follow_ups": ["What would you do differently next time?"],
        "scoring_criteria": {
            "strong": "Clear situation, personal actions, and result",
            "adequate": "Relevant story but light on personal role",
            "weak": "Vague, theoretical, or cannot give a concrete example",
        },
    }


def detect_role_family(jd_analysis: Dict[str, Any]) -> str:
    """Map JD title/domain to a playbook family."""
    blob = " ".join(
        str(jd_analysis.get(k) or "")
        for k in ("role_title", "title", "domain", "job_title")
    ).lower()
    if any(k in blob for k in ("talent acquisition", "recruiter", "recruiting", " hr", "hiring", "staffing")):
        return "talent_acquisition"
    if any(k in blob for k in ("sap", "erp", "s/4", "s4", " mm", "fico", "idoc")):
        return "sap"
    if any(k in blob for k in ("finance", "accounting", "fp&a", "financial analyst", "treasury")):
        return "finance"
    if any(k in blob for k in ("engineer", "developer", "software", "devops", "data scien", "architect")):
        return "engineering"
    if any(k in blob for k in ("nurse", "clinical", "physician", "healthcare", "patient", "medical")):
        return "healthcare"
    if any(k in blob for k in ("sales", "account executive", "business development", "quota", "revenue")):
        return "sales"
    return "general"


def build_open_script(family: str, ctx: Dict[str, str]) -> str:
    template = OPEN_TEMPLATES.get(family, OPEN_TEMPLATES["general"])
    return template.format_map({**ctx, "name": ctx.get("name", "there")})


def build_close_script(family: str, ctx: Dict[str, str]) -> str:
    template = CLOSE_TEMPLATES.get(family, CLOSE_TEMPLATES["general"])
    return template.format_map(ctx)


def get_playbook_registry() -> dict[str, Any]:
    """Phase 5 — domain playbook registry for UI and kit routing."""
    return {
        "families": list(ROLE_FAMILIES),
        "open_templates": list(OPEN_TEMPLATES.keys()),
        "thread_categories": ["technical", "behavioral", "domain", "ownership", "collaboration"],
        "version": "v2",
    }
