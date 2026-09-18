"""
Single source of truth for all scoring constants, thresholds, and domain configuration.
All services MUST import from this module instead of defining local copies.
"""

from typing import Dict, List, Tuple


# --- Recommendation Thresholds ---
RECOMMENDATION_THRESHOLDS = {
    "shortlist": 72,
    "consider": 45,
    "reject": 0,
}

# Voice / consolidated interview uses a different vocabulary than resume screening.
SCORE_RULERS = {
    "resume": {
        "shortlist": 72,
        "consider": 45,
        "labels": ("Shortlist", "Consider", "Reject"),
    },
    "voice_interview": {
        "advance_hm": 80,
        "advance": 65,
        "hold": 45,
        "labels": ("Advance to HM", "Advance", "Hold", "Reject"),
    },
}


def recommendation_from_score(score) -> str:
    """Map a 0–100 displayed fit score to Shortlist / Consider / Reject."""
    try:
        value = int(round(float(score)))
    except (TypeError, ValueError):
        return "Consider"
    if value >= RECOMMENDATION_THRESHOLDS["shortlist"]:
        return "Shortlist"
    if value >= RECOMMENDATION_THRESHOLDS["consider"]:
        return "Consider"
    return "Reject"


# --- Seniority Ranges ---
# (min_years_inclusive, max_years_exclusive)
SENIORITY_RANGES = {
    "intern": (0, 1),
    "junior": (0, 2),
    "mid": (2, 5),
    "senior": (5, 10),
    "lead": (7, 15),
    "principal": (10, 25),
    "staff": (8, 20),
    "architect": (10, 25),
    "director": (12, 30),
}


# --- Default Scoring Weights ---
# Used by hybrid_pipeline.py and agent_pipeline.py
DEFAULT_WEIGHTS = {
    "skills":       0.30,
    "experience":   0.20,
    "architecture": 0.15,
    "education":    0.10,
    "timeline":     0.10,
    "domain":       0.10,
    "risk":         0.15,
}


# --- Industry-Specific Scoring Weights ---
# Override default weights based on detected industry for more accurate scoring
# Keys map to domain values from DOMAIN_KEYWORDS
INDUSTRY_WEIGHTS: Dict[str, Dict[str, float]] = {
    # Healthcare: certifications and clinical experience matter more
    "healthcare": {
        "skills":       0.20,
        "experience":   0.25,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.15,
    },
    # Finance: certifications (CPA, CFA) are critical
    "finance": {
        "skills":       0.15,
        "experience":   0.20,
        "education":    0.25,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.15,
    },
    # Sales: achievements and track record over formal credentials
    "sales": {
        "skills":       0.15,
        "experience":   0.25,
        "education":    0.10,
        "timeline":     0.15,
        "domain":       0.20,
        "risk":         0.15,
    },
    # HR: experience and certifications (SHRM, PHR)
    "hr": {
        "skills":       0.15,
        "experience":   0.30,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.15,
    },
    # Legal: bar admission and experience
    "legal": {
        "skills":       0.10,
        "experience":   0.35,
        "education":    0.20,
        "timeline":     0.10,
        "domain":       0.10,
        "risk":         0.15,
    },
    # Manufacturing: practical skills and experience
    "manufacturing": {
        "skills":       0.25,
        "experience":   0.25,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.10,
    },
    # Operations/Admin: experience and domain knowledge
    "operations_admin": {
        "skills":       0.15,
        "experience":   0.30,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.20,
        "risk":         0.10,
    },
    # Marketing: creative skills and results
    "marketing": {
        "skills":       0.20,
        "experience":   0.25,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.20,
        "risk":         0.10,
    },
    # Education: certifications and teaching experience
    "education": {
        "skills":       0.15,
        "experience":   0.30,
        "education":    0.20,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.10,
    },
    # Consulting: experience and education
    "consulting": {
        "skills":       0.15,
        "experience":   0.30,
        "education":    0.20,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.10,
    },
    # Logistics: operations experience
    "logistics": {
        "skills":       0.20,
        "experience":   0.30,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.10,
    },
    # Construction: practical experience
    "construction": {
        "skills":       0.20,
        "experience":   0.30,
        "education":    0.15,
        "timeline":     0.10,
        "domain":       0.15,
        "risk":         0.10,
    },
}


def get_industry_weights(domain: str) -> Dict[str, float]:
    """Get scoring weights for a given domain/industry.

    Args:
        domain: The detected domain (e.g., 'healthcare', 'finance', 'sales')

    Returns:
        Industry-specific weights if available, otherwise DEFAULT_WEIGHTS
    """
    # Direct match
    if domain in INDUSTRY_WEIGHTS:
        return INDUSTRY_WEIGHTS[domain]

    # Map related domains
    domain_mapping = {
        "hr": "hr",
        "sales": "sales",
        "marketing": "marketing",
        "legal": "legal",
        "manufacturing": "manufacturing",
        "operations_admin": "operations_admin",
        "logistics": "logistics",
        "construction": "construction",
        "consulting": "consulting",
        "education": "education",
    }

    mapped = domain_mapping.get(domain.lower())
    if mapped and mapped in INDUSTRY_WEIGHTS:
        return INDUSTRY_WEIGHTS[mapped]

    # Default to standard weights
    return DEFAULT_WEIGHTS


# --- Domain Keywords ---
DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "backend":      ["fastapi", "django", "flask", "spring", "rest api", "grpc",
                     "postgresql", "mysql", "redis", "microservices", "golang", "node.js",
                     "express", "api development", "backend", "server side", "database design",
                     "sqlalchemy", "orm", "celery", "message queue"],
    "frontend":     ["react", "vue", "angular", "next.js", "nuxt", "tailwind", "html", "css",
                     "webpack", "vite", "typescript", "ui development", "frontend", "responsive",
                     "web components", "ssr", "spa", "pwa", "accessibility", "figma"],
    "fullstack":    ["full stack", "fullstack", "full-stack", "frontend and backend",
                     "end-to-end", "mern", "mean", "lamp", "jamstack"],
    "data_science": ["pandas", "numpy", "sklearn", "scikit", "jupyter", "data analysis",
                     "statistics", "tableau", "power bi", "data pipeline", "etl",
                     "feature engineering", "regression", "classification", "clustering",
                     "time series", "sql", "bigquery", "snowflake", "dbt"],
    "ml_ai":        ["machine learning", "deep learning", "neural network", "nlp", "pytorch",
                     "tensorflow", "transformers", "llm", "computer vision", "reinforcement learning",
                     "mlops", "model training", "fine-tuning", "hugging face", "langchain",
                     "rag", "vector database", "embeddings", "generative ai"],
    "devops":       ["kubernetes", "docker", "terraform", "ansible", "jenkins", "ci/cd", "helm",
                     "argo", "prometheus", "grafana", "infrastructure as code", "sre",
                     "linux", "bash", "monitoring", "observability", "cloud", "gitops",
                     "deployment", "devops"],
    "embedded":     ["embedded", "firmware", "rtos", "microcontroller", "fpga", "uart",
                     "can bus", "i2c", "spi", "arm cortex", "device driver", "bsp",
                     "real-time", "baremetal", "freertos", "yocto", "iso 26262", "misra",
                     "embedded linux", "bootloader"],
    "mobile":       ["ios", "android", "react native", "flutter", "swift", "kotlin",
                     "xcode", "mobile app", "jetpack compose", "swiftui", "app store",
                     "mobile development", "push notification"],
    "management":   ["product manager", "engineering manager", "team lead", "delivery manager",
                     "scrum master", "agile coach", "roadmap", "stakeholder", "okr",
                     "program manager", "pmo", "budget", "hiring"],
    # ── Non-tech domains ──────────────────────────────────────────────────────
    "healthcare":   ["patient care", "clinical", "nursing", "medical", "healthcare", "hospital",
                     "electronic health records", "ehr", "emr", "hipaa", "patient safety",
                     "pharmacy", "radiology", "laboratory", "diagnostic", "treatment plan",
                     "patient outcomes", "medical terminology", "telemedicine", "epic",
                     "cerner", "medical coding", "icd-10", "cpt", "health informatics",
                     "public health", "epidemiology", "infection control", "patient discharge",
                     "care coordination", "medical device", "pharmaceutical", "clinical trial"],
    "finance":      ["financial analysis", "accounting", "financial modeling", "budgeting",
                     "forecasting", "gaap", "ifrs", "audit", "tax", "treasury",
                     "financial reporting", "balance sheet", "cash flow", "p&l", "profit loss",
                     "reconciliation", "accounts payable", "accounts receivable", "general ledger",
                     "quickbooks", "sap fi", "oracle financials", "financial planning",
                     "risk assessment", "compliance", "internal controls", "sox",
                     "valuation", "dcf", "npv", "irr", "financial statements",
                     "cost accounting", "variance analysis", "capital budgeting"],
    "legal":        ["law", "legal", "attorney", "lawyer", "paralegal", "litigation",
                     "contract", "compliance", "regulatory", "corporate law", "ip law",
                     "patent", "trademark", "copyright", "legal research", "legal writing",
                     "discovery", "deposition", "brief", "motion", "pleading",
                     "due diligence", "mergers acquisitions", "nda", "non-disclosure",
                     "employment law", "labor law", "real estate law", "criminal law",
                     "civil litigation", "arbitration", "mediation", "bar admission",
                     "westlaw", "lexisnexis", "legal hold", "evidence", "statute"],
    "sales":        ["sales strategy", "crm", "salesforce", "pipeline management", "negotiation",
                     "lead generation", "account management", "b2b sales", "closing deals",
                     "sales forecasting", "prospecting", "relationship building",
                     "cold calling", "sales pitch", "quota", "revenue target",
                     "territory management", "sales cycle", "b2c sales", "inside sales",
                     "field sales", "channel sales", "partner management", "upselling",
                     "cross-selling", "customer acquisition", "sales operations",
                     "hubspot", "pipedrive", "outreach", "sales loft", "gong"],
    "marketing":    ["digital marketing", "content marketing", "seo", "sem", "social media",
                     "email marketing", "marketing automation", "google analytics",
                     "campaign management", "brand management", "marketing strategy",
                     "lead generation", "conversion optimization", "ppc", "google ads",
                     "facebook ads", "marketing analytics", "customer segmentation",
                     "marketing research", "competitive analysis", "go-to-market",
                     "content strategy", "copywriting", "marketing funnel",
                     "hubspot", "marketo", "pardot", "mailchimp", "hootsuite",
                     "buffer", "canva", "adobe creative suite", "marketing roi"],
    "hr":           ["human resources", "recruitment", "talent acquisition", "onboarding",
                     "employee relations", "performance management", "hris", "workday",
                     "successfactors", "bamboo hr", "payroll", "benefits administration",
                     "compensation", "job analysis", "employee engagement",
                     "diversity inclusion", "training development", "l&d",
                     "labor law compliance", "grievance handling", "exit interviews",
                     "workforce planning", "succession planning", "hr analytics",
                     "applicant tracking system", "ats", "background verification",
                     "policy development", "organizational development"],
    "education":    ["curriculum development", "lesson planning", "pedagogy", "assessment",
                     "classroom management", "educational technology", "e-learning",
                     "instructional design", "teaching", "tutoring", "student engagement",
                     "differentiated instruction", "special education", "iep",
                     "stem education", "early childhood education", "higher education",
                     "academic research", "grant writing", "rubric", "formative assessment",
                     "summative assessment", "learning management system", "lms",
                     "canvas", "blackboard", "moodle", "google classroom",
                     "schoology", "edtech", "blended learning", "distance learning"],
    "manufacturing": ["lean manufacturing", "six sigma", "quality control", "iso 9001",
                     "production planning", "supply chain", "inventory management",
                     "erp", "sap", "oracle manufacturing", "mrp", "capacity planning",
                     "continuous improvement", "kaizen", "5s", "poka-yoke",
                     "statistical process control", "spc", "root cause analysis",
                     "safety compliance", "osha", "forklift", "cnc", "cad",
                     "cam", "autocad", "solidworks", "manufacturing execution system",
                     "mes", "total productive maintenance", "tpm", "value stream mapping"],
    "consulting":   ["strategy consulting", "management consulting", "business analysis",
                     "stakeholder management", "change management", "process improvement",
                     "project management", "pmo", "business transformation",
                     "digital transformation", "operational excellence", "mckinsey",
                     "bcg", "bain", "deloitte", "accenture", "pwc", "ey", "kpmg",
                     "case study", "executive presentation", "workshop facilitation",
                     "requirements gathering", "gap analysis", "business case",
                     "roi analysis", "transformation roadmap", "process mapping",
                     "lean six sigma", "agile transformation", "organizational design"],
    "design":       ["ui design", "ux design", "user research", "wireframing", "prototyping",
                     "figma", "sketch", "adobe xd", "design system", "component library",
                     "accessibility", "wcag", "responsive design", "interaction design",
                     "information architecture", "usability testing", "heuristic evaluation",
                     "user journey", "persona", "design thinking", "service design",
                     "visual design", "typography", "color theory", "branding",
                     "adobe creative suite", "photoshop", "illustrator", "after effects",
                     "motion design", "3d modeling", "blender", "cinema 4d"],
    "logistics":    ["supply chain management", "logistics", "warehousing", "distribution",
                     "freight", "shipping", "inventory control", "demand forecasting",
                     "procurement", "vendor management", "3pl", "4pl",
                     "transportation management", "wms", "tms", "erp",
                     "customs clearance", "import export", "incoterms", "hs code",
                     "freight forwarding", "last mile delivery", "route optimization",
                     "fleet management", "cold chain", "reverse logistics",
                     "supply chain optimization", "s&op", "demand planning"],
    "construction": ["construction management", "project management", "site supervision",
                     "building codes", "autocad", "revit", "bim", "primavera",
                     "ms project", "cost estimation", "quantity surveying",
                     "blueprint reading", "safety compliance", "osha",
                     "contract administration", "change order", "rfi",
                     "submittal", "leed certification", "sustainable construction",
                     "structural engineering", "civil engineering", " MEP",
                     "hvac", "electrical systems", "plumbing", "concrete",
                     "steel structure", "foundation", "demolition", "renovation"],
}


# --- Degree Scores ---
DEGREE_SCORES: Dict[str, int] = {
    "phd": 100, "ph.d": 100, "doctorate": 100, "doctor of": 100,
    "master": 85, "msc": 85, "m.sc": 85, "mba": 82, "mca": 85,
    "mtech": 85, "m.tech": 85, "m.e.": 85, "ms ": 85, "m.s.": 85,
    "me ": 85, "post graduate": 82, "postgraduate": 82, "pg diploma": 75,
    "bachelor": 70, "bsc": 70, "b.sc": 70, "be ": 70, "b.e.": 70,
    "btech": 70, "b.tech": 70, "bs ": 70, "b.s.": 70, "ba ": 65,
    "b.a.": 65, "bca": 65, "bba": 62,
    "diploma": 50, "associate": 48, "hnd": 45,
    "certif": 38, "bootcamp": 35, "course": 30,
}


# --- Field Relevance ---
FIELD_RELEVANCE: Dict[str, List[str]] = {
    "backend":      ["computer science", "computer engineering", "software engineering",
                     "information technology", "cse", "it", "bca", "mca", "electronics"],
    "frontend":     ["computer science", "software engineering", "web", "human computer interaction",
                     "hci", "interaction design", "information technology"],
    "fullstack":    ["computer science", "software engineering", "information technology",
                     "cse", "mca", "bca"],
    "data_science": ["data science", "statistics", "mathematics", "computer science",
                     "analytics", "information systems", "operations research", "economics"],
    "ml_ai":        ["artificial intelligence", "machine learning", "computer science",
                     "computational", "data science", "statistics", "mathematics"],
    "devops":       ["computer science", "computer engineering", "information technology",
                     "systems", "networking", "telecommunications"],
    "embedded":     ["electronics", "electrical", "computer engineering", "embedded",
                     "vlsi", "instrumentation", "mechatronics", "robotics", "avionics"],
    "mobile":       ["computer science", "software engineering", "information technology",
                     "mobile computing"],
    "management":   ["business", "management", "mba", "project management", "administration",
                     "operations", "strategy"],
    "healthcare":   ["medicine", "nursing", "public health", "health science", "pharmacy",
                     "biomedical", "healthcare administration", "health informatics",
                     "radiology", "medical technology", "clinical research", "epidemiology",
                     "biochemistry", "microbiology", "anatomy", "physiology"],
    "finance":      ["finance", "accounting", "economics", "business", "commerce",
                     "financial management", "banking", "actuarial science",
                     "taxation", "auditing", "corporate finance", "investment",
                     "mathematics", "statistics"],
    "legal":        ["law", "juris doctor", "llb", "ll.m", "legal studies",
                     "paralegal studies", "criminal justice", "political science",
                     "public policy", "international law", "corporate law"],
    "sales":        ["business", "marketing", "sales", "communication", "management",
                     "business administration", "psychology", "economics"],
    "marketing":    ["marketing", "business", "communication", "advertising",
                     "public relations", "journalism", "digital media",
                     "market research", "consumer behavior", "brand management"],
    "hr":           ["human resources", "psychology", "industrial relations",
                     "organizational behavior", "business administration",
                     "labor studies", "sociology", "education", "management"],
    "education":    ["education", "educational leadership", "curriculum instruction",
                     "pedagogy", "teaching", "educational technology",
                     "special education", "early childhood education",
                     "higher education administration", "subject specific"],
    "manufacturing": ["industrial engineering", "mechanical engineering",
                     "manufacturing engineering", "production engineering",
                     "quality engineering", "operations management",
                     "supply chain management", "industrial management"],
    "consulting":   ["business", "management", "mba", "economics", "finance",
                     "strategy", "operations research", "industrial engineering",
                     "organizational psychology", "public policy"],
    "design":       ["design", "graphic design", "interaction design", "visual communication",
                     "industrial design", "ux design", "ui design", "human computer interaction",
                     "fine arts", "applied arts", "multimedia design"],
    "logistics":    ["supply chain management", "logistics", "operations management",
                     "business", "industrial engineering", "transportation",
                     "international business", "commerce"],
    "construction": ["civil engineering", "construction management", "architecture",
                     "structural engineering", "building science", "quantity surveying",
                     "mechanical engineering", "electrical engineering"],
    "other":        ["computer", "information", "engineering", "technology", "science",
                     "mathematics", "systems", "business", "arts", "social science"],
}


# --- Risk Severity Penalties ---
RISK_SEVERITY_PENALTIES = {
    "high": 20,
    "medium": 10,
    "low": 4,
}


# --- Weight Mapper Schemas ---
# Legacy 4-weight schema (for backward compatibility)
LEGACY_WEIGHTS = {
    "skills": 0.40,
    "experience": 0.35,
    "stability": 0.15,
    "education": 0.10,
}

# New universal-adaptive weight schema
NEW_DEFAULT_WEIGHTS = {
    "core_competencies": 0.30,
    "experience": 0.20,
    "domain_fit": 0.20,
    "education": 0.10,
    "career_trajectory": 0.10,
    "role_excellence": 0.10,
    "risk": 0.10,
}

# Old backend schema (7 tech-centric weights)
OLD_BACKEND_WEIGHTS = {
    "skills": 0.30,
    "experience": 0.20,
    "architecture": 0.15,
    "education": 0.10,
    "timeline": 0.10,
    "domain": 0.10,
    "risk": 0.15,
}


# ============================================================================
# ENTERPRISE-GRADE JOB FUNCTION TAXONOMY (Phase 1 Enhancement)
# ============================================================================

# Job Function Skill Taxonomy: Maps job functions to core, adjacent, and irrelevant skills
# This enables context-aware skill validation during matching
JOB_FUNCTION_SKILL_TAXONOMY: Dict[str, Dict[str, List[str]]] = {
    "account_based_marketing": {
        "core_skills": [
            "account-based marketing", "abm", "demand generation", "lead generation",
            "marketing automation", "salesforce", "marketing analytics", "segment",
            "target account identification", "personalization", "multi-channel campaigns"
        ],
        "adjacent_skills": [
            "content marketing", "seo", "sem", "social media marketing",
            "email marketing", "crm", "customer journey mapping", "attribution modeling",
            "marketing strategy", "b2b marketing", "data analysis", "communication"
        ],
        "irrelevant_skills": [
            "python", "java", "kubernetes", "react", "sql", "machine learning",
            "embedded systems", "mobile development", "devops"
        ],
        "core_responsibilities": [
            "develop abm strategies", "identify target accounts", "create personalized campaigns",
            "collaborate with sales", "analyze campaign performance", "manage marketing automation",
            "track account engagement", "optimize marketing funnel"
        ]
    },
    "backend_engineering": {
        "core_skills": [
            "python", "java", "go", "node.js", "fastapi", "django", "spring boot",
            "express", "postgresql", "mysql", "redis", "mongodb", "api development",
            "rest api", "graphql", "microservices", "system design"
        ],
        "adjacent_skills": [
            "docker", "kubernetes", "ci/cd", "aws", "gcp", "azure", "terraform",
            "graphql", "grpc", "message queues", "caching", "database optimization",
            "testing", "agile", "communication", "collaboration"
        ],
        "irrelevant_skills": [
            "photoshop", "illustrator", "abm", "marketing automation", "salesforce",
            "financial modeling", "hr management", "graphic design"
        ],
        "core_responsibilities": [
            "design and implement apis", "build scalable backend systems", "optimize database queries",
            "ensure system reliability", "write clean maintainable code", "collaborate with frontend",
            "implement security best practices", "monitor system performance"
        ]
    },
    "frontend_engineering": {
        "core_skills": [
            "react", "vue", "angular", "typescript", "javascript", "html", "css",
            "next.js", "responsive design", "web accessibility", "state management",
            "webpack", "vite", "tailwind", "sass"
        ],
        "adjacent_skills": [
            "figma", "ui/ux design", "testing", "performance optimization", "seo",
            "progressive web apps", "git", "agile", "communication", "collaboration"
        ],
        "irrelevant_skills": [
            "python backend", "database administration", "network security",
            "financial analysis", "hr policies", "marketing strategy"
        ],
        "core_responsibilities": [
            "build responsive user interfaces", "implement pixel-perfect designs",
            "optimize frontend performance", "ensure cross-browser compatibility",
            "write reusable components", "collaborate with designers", "implement accessibility standards"
        ]
    },
    "data_science": {
        "core_skills": [
            "python", "sql", "pandas", "numpy", "scikit-learn", "data analysis",
            "statistics", "machine learning", "data visualization", "tableau",
            "power bi", "feature engineering", "hypothesis testing"
        ],
        "adjacent_skills": [
            "big data", "spark", "hadoop", "cloud platforms", "deep learning",
            "nlp", "etl", "data engineering", "communication", "storytelling",
            "business acumen", "a/b testing"
        ],
        "irrelevant_skills": [
            "react", "angular", "mobile development", "graphic design",
            "marketing automation", "sales management", "hr policies"
        ],
        "core_responsibilities": [
            "analyze complex datasets", "build predictive models", "extract actionable insights",
            "create data visualizations", "present findings to stakeholders", "design experiments",
            "collaborate with engineering", "ensure data quality"
        ]
    },
    "devops_engineering": {
        "core_skills": [
            "docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd",
            "aws", "gcp", "azure", "linux", "bash", "monitoring", "prometheus",
            "grafana", "infrastructure as code"
        ],
        "adjacent_skills": [
            "python", "go", "git", "networking", "security", "database administration",
            "scripting", "automation", "agile", "collaboration", "incident response"
        ],
        "irrelevant_skills": [
            "react", "marketing automation", "financial modeling", "graphic design",
            "content writing", "sales strategy", "hr management"
        ],
        "core_responsibilities": [
            "manage cloud infrastructure", "implement ci/cd pipelines", "ensure system reliability",
            "automate deployment processes", "monitor system performance", "respond to incidents",
            "optimize infrastructure costs", "implement security best practices"
        ]
    },
    "product_management": {
        "core_skills": [
            "product strategy", "roadmap planning", "user research", "agile", "scrum",
            "stakeholder management", "data analysis", "a/b testing", "user stories",
            "prioritization", "market research", "competitive analysis"
        ],
        "adjacent_skills": [
            "sql", "analytics tools", "wireframing", "communication", "leadership",
            "project management", "technical understanding", "design thinking",
            "customer development", "go-to-market strategy"
        ],
        "irrelevant_skills": [
            "python programming", "database administration", "network configuration",
            "graphic design tools", "video editing", "accounting"
        ],
        "core_responsibilities": [
            "define product vision", "prioritize features", "gather user requirements",
            "collaborate with engineering", "analyze product metrics", "conduct user research",
            "manage stakeholder expectations", "drive product launches"
        ]
    },
    "sales": {
        "core_skills": [
            "sales strategy", "crm", "salesforce", "pipeline management", "negotiation",
            "lead generation", "account management", "b2b sales", "closing deals",
            "sales forecasting", "prospecting", "relationship building"
        ],
        "adjacent_skills": [
            "marketing", "communication", "presentation skills", "data analysis",
            "social selling", "email outreach", "linkedin", "customer success",
            "contract negotiation", "territory management"
        ],
        "irrelevant_skills": [
            "python", "react", "kubernetes", "database design", "machine learning",
            "graphic design", "video production", "accounting"
        ],
        "core_responsibilities": [
            "drive revenue growth", "build client relationships", "manage sales pipeline",
            "conduct product demos", "negotiate contracts", "exceed sales targets",
            "collaborate with marketing", "provide market feedback"
        ]
    },
    "healthcare_clinical": {
        "core_skills": [
            "patient care", "clinical assessment", "medical terminology", "patient safety",
            "electronic health records", "ehr", "emr", "epic", "cerner",
            "treatment planning", "medication administration", "vital signs",
            "patient discharge", "care coordination", "infection control", "hipaa"
        ],
        "adjacent_skills": [
            "communication", "empathy", "critical thinking", "teamwork",
            "time management", "documentation", "quality assurance",
            "patient education", "telemedicine", "data entry"
        ],
        "irrelevant_skills": [
            "python", "react", "kubernetes", "digital marketing", "salesforce",
            "graphic design", "content strategy"
        ],
        "core_responsibilities": [
            "provide direct patient care", "maintain accurate medical records",
            "administer medications", "monitor patient progress",
            "collaborate with healthcare team", "ensure regulatory compliance",
            "educate patients and families", "respond to emergencies"
        ]
    },
    "finance_accounting": {
        "core_skills": [
            "financial analysis", "accounting", "gaap", "ifrs", "financial reporting",
            "reconciliation", "general ledger", "accounts payable", "accounts receivable",
            "quickbooks", "excel", "financial modeling", "budgeting", "forecasting"
        ],
        "adjacent_skills": [
            "audit", "tax", "sox compliance", "internal controls", "sap",
            "oracle financials", "data analysis", "communication", "attention to detail",
            "problem solving", "process improvement"
        ],
        "irrelevant_skills": [
            "react", "kubernetes", "graphic design", "content marketing",
            "patient care", "machine learning"
        ],
        "core_responsibilities": [
            "prepare financial statements", "conduct financial analysis",
            "manage accounts payable receivable", "ensure regulatory compliance",
            "support audit processes", "maintain general ledger",
            "assist with budgeting forecasting", "reconcile accounts"
        ]
    },
    "legal": {
        "core_skills": [
            "legal research", "legal writing", "contract review", "litigation",
            "regulatory compliance", "due diligence", "corporate law",
            "westlaw", "lexisnexis", "contract drafting", "legal analysis"
        ],
        "adjacent_skills": [
            "negotiation", "communication", "attention to detail", "analytical thinking",
            "project management", "stakeholder management", "risk assessment",
            "policy development", "ethics"
        ],
        "irrelevant_skills": [
            "python", "react", "kubernetes", "digital marketing", "patient care",
            "graphic design", "machine learning"
        ],
        "core_responsibilities": [
            "conduct legal research", "draft and review contracts",
            "manage litigation proceedings", "ensure regulatory compliance",
            "provide legal counsel", "support mergers acquisitions",
            "maintain corporate records", "advise on risk mitigation"
        ]
    },
    "marketing": {
        "core_skills": [
            "digital marketing", "seo", "sem", "content marketing", "social media",
            "email marketing", "google analytics", "marketing automation",
            "campaign management", "ppc", "google ads", "hubspot"
        ],
        "adjacent_skills": [
            "communication", "copywriting", "data analysis", "a/b testing",
            "brand management", "market research", "competitive analysis",
            "figma", "canva", "adobe creative suite", "project management"
        ],
        "irrelevant_skills": [
            "python", "kubernetes", "patient care", "financial modeling",
            "legal research", "machine learning"
        ],
        "core_responsibilities": [
            "develop marketing campaigns", "manage social media presence",
            "optimize seo sem performance", "create content strategy",
            "analyze campaign metrics", "manage marketing budget",
            "collaborate with sales team", "drive brand awareness"
        ]
    },
    "human_resources": {
        "core_skills": [
            "recruitment", "talent acquisition", "employee relations",
            "performance management", "onboarding", "hris", "workday",
            "successfactors", "compensation benefits", "labor law compliance"
        ],
        "adjacent_skills": [
            "communication", "conflict resolution", "negotiation", "empathy",
            "data analysis", "project management", "training development",
            "organizational development", "diversity inclusion"
        ],
        "irrelevant_skills": [
            "python", "react", "kubernetes", "patient care", "digital marketing",
            "financial modeling", "machine learning"
        ],
        "core_responsibilities": [
            "manage full cycle recruitment", "facilitate onboarding",
            "handle employee relations", "administer performance reviews",
            "ensure labor law compliance", "manage benefits programs",
            "support organizational development", "maintain hris data"
        ]
    },
    "education_teaching": {
        "core_skills": [
            "curriculum development", "lesson planning", "classroom management",
            "assessment", "instructional design", "educational technology",
            "differentiated instruction", "student engagement", "lms"
        ],
        "adjacent_skills": [
            "communication", "patience", "creativity", "collaboration",
            "public speaking", "google classroom", "canvas", "moodle",
            "special education", "mentoring"
        ],
        "irrelevant_skills": [
            "python", "kubernetes", "patient care", "financial modeling",
            "digital marketing", "salesforce"
        ],
        "core_responsibilities": [
            "develop and deliver lessons", "assess student progress",
            "manage classroom behavior", "adapt instruction for diverse learners",
            "collaborate with parents staff", "maintain accurate records",
            "integrate technology in learning", "support student wellbeing"
        ]
    },
    "manufacturing_operations": {
        "core_skills": [
            "lean manufacturing", "six sigma", "quality control", "iso 9001",
            "production planning", "supply chain", "erp", "sap",
            "statistical process control", "root cause analysis", "5s", "kaizen"
        ],
        "adjacent_skills": [
            "autocad", "solidworks", "cnc", "cad cam", "safety compliance",
            "osha", "continuous improvement", "data analysis", "project management",
            "communication", "teamwork"
        ],
        "irrelevant_skills": [
            "react", "digital marketing", "patient care", "legal research",
            "content marketing", "graphic design"
        ],
        "core_responsibilities": [
            "manage production schedules", "ensure quality standards",
            "optimize manufacturing processes", "implement safety protocols",
            "manage inventory levels", "drive continuous improvement",
            "coordinate with supply chain", "lead operational teams"
        ]
    },
    "design_creative": {
        "core_skills": [
            "ui design", "ux design", "figma", "sketch", "adobe xd",
            "user research", "wireframing", "prototyping", "design system",
            "accessibility", "responsive design", "interaction design"
        ],
        "adjacent_skills": [
            "html", "css", "javascript", "communication", "collaboration",
            "photoshop", "illustrator", "after effects", "design thinking",
            "usability testing", "information architecture"
        ],
        "irrelevant_skills": [
            "kubernetes", "patient care", "financial modeling", "legal research",
            "supply chain management", "manufacturing"
        ],
        "core_responsibilities": [
            "create user centered designs", "conduct user research",
            "develop wireframes prototypes", "maintain design systems",
            "ensure accessibility compliance", "collaborate with engineering",
            "present design rationale", "iterate based on feedback"
        ]
    },
    "logistics_supply_chain": {
        "core_skills": [
            "supply chain management", "logistics", "warehousing", "inventory management",
            "procurement", "vendor management", "freight", "shipping",
            "wms", "tms", "erp", "demand forecasting"
        ],
        "adjacent_skills": [
            "data analysis", "excel", "communication", "negotiation",
            "project management", "process improvement", "customs",
            "import export", "safety compliance", "teamwork"
        ],
        "irrelevant_skills": [
            "react", "machine learning", "patient care", "graphic design",
            "content marketing", "legal research"
        ],
        "core_responsibilities": [
            "manage warehouse operations", "coordinate shipments",
            "optimize inventory levels", "manage vendor relationships",
            "ensure customs compliance", "track delivery performance",
            "optimize routes", "manage logistics budget"
        ]
    },
    "construction_engineering": {
        "core_skills": [
            "construction management", "project management", "autocad", "revit",
            "bim", "cost estimation", "quantity surveying", "building codes",
            "safety compliance", "osha", "blueprint reading"
        ],
        "adjacent_skills": [
            "ms project", "primavera", "structural engineering", "civil engineering",
            "communication", "leadership", "problem solving", "contract administration",
            "quality assurance", "teamwork"
        ],
        "irrelevant_skills": [
            "react", "digital marketing", "patient care", "machine learning",
            "content marketing", "graphic design"
        ],
        "core_responsibilities": [
            "manage construction projects", "ensure safety compliance",
            "coordinate with subcontractors", "monitor project progress",
            "control project costs", "review blueprints",
            "conduct site inspections", "manage quality standards"
        ]
    },
}

# Job Function Detection Keywords (maps JD keywords to job functions)
JOB_FUNCTION_KEYWORDS: Dict[str, List[str]] = {
    "account_based_marketing": [
        "abm", "account-based marketing", "demand generation marketer",
        "b2b marketer", "growth marketer", "field marketer"
    ],
    "backend_engineering": [
        "backend engineer", "backend developer", "server-side developer",
        "api developer", "software engineer backend", "platform engineer"
    ],
    "frontend_engineering": [
        "frontend engineer", "frontend developer", "ui engineer",
        "ui developer", "web developer", "javascript developer"
    ],
    "data_science": [
        "data scientist", "data analyst", "machine learning engineer",
        "analytics engineer", "business intelligence", "bi analyst"
    ],
    "devops_engineering": [
        "devops engineer", "site reliability engineer", "sre",
        "platform engineer", "infrastructure engineer", "cloud engineer"
    ],
    "product_management": [
        "product manager", "senior product manager", "director of product",
        "vp product", "head of product", "technical product manager"
    ],
    "sales": [
        "account executive", "sales representative", "sales manager",
        "business development", "ae", "sdr", "bdr", "sales director"
    ],
    "healthcare_clinical": [
        "registered nurse", "rn", "nurse practitioner", "physician",
        "doctor", "medical officer", "clinical specialist", "pharmacist",
        "radiologist", "lab technician", "medical technologist",
        "physical therapist", "occupational therapist", "nursing manager"
    ],
    "finance_accounting": [
        "financial analyst", "accountant", "controller", "cfo",
        "finance manager", "audit manager", "tax specialist",
        "treasury analyst", "fp&a analyst", "bookkeeper",
        "accounts payable specialist", "accounts receivable specialist"
    ],
    "legal": [
        "attorney", "lawyer", "paralegal", "legal counsel",
        "compliance officer", "legal advisor", "contract specialist",
        "litigation associate", "corporate counsel", "legal consultant"
    ],
    "marketing": [
        "marketing manager", "digital marketing specialist", "seo specialist",
        "content marketing manager", "social media manager", "brand manager",
        "growth marketing manager", "marketing analyst", "demand generation"
    ],
    "human_resources": [
        "hr manager", "recruiter", "talent acquisition specialist",
        "hr business partner", "people operations", "l&d specialist",
        "compensation analyst", "hr coordinator", "people partner"
    ],
    "education_teaching": [
        "teacher", "professor", "instructor", "curriculum developer",
        "instructional designer", "education coordinator", "academic advisor",
        "school administrator", "education specialist", "tutor"
    ],
    "manufacturing_operations": [
        "production manager", "quality engineer", "manufacturing engineer",
        "plant manager", "operations manager", "supply chain analyst",
        "process engineer", "industrial engineer", "qa qc inspector"
    ],
    "design_creative": [
        "ux designer", "ui designer", "product designer", "graphic designer",
        "visual designer", "interaction designer", "brand designer",
        "motion designer", "design researcher", "creative director"
    ],
    "logistics_supply_chain": [
        "logistics coordinator", "supply chain manager", "warehouse manager",
        "procurement specialist", "distribution manager", "freight coordinator",
        "inventory analyst", "shipping manager", "transportation planner"
    ],
    "construction_engineering": [
        "project engineer", "construction manager", "site engineer",
        "civil engineer", "structural engineer", "quantity surveyor",
        "architect", "bim coordinator", "safety officer", "cost estimator"
    ],
}

# Generic Soft Skills (should NOT be in must-have unless explicitly emphasized)
GENERIC_SOFT_SKILLS = {
    "communication", "collaboration", "teamwork", "leadership", "problem solving",
    "analytical thinking", "creativity", "adaptability", "time management",
    "attention to detail", "critical thinking", "interpersonal skills",
    "organizational skills", "multitasking", "work ethic", "initiative"
}

# Linguistic Cues for Skill Importance
MUST_HAVE_CUES = [
    "must have", "must-have", "required", "essential", "mandatory",
    "non-negotiable", "critical", "core requirement", "key requirement",
    "qualifications:", "requirements:", "must possess", "should have"
]

NICE_TO_HAVE_CUES = [
    "nice to have", "nice-to-have", "preferred", "bonus", "plus",
    "good to have", "desirable", "ideal candidate", "would be great",
    "beneficial", "advantageous", "a plus"
]

# Enterprise Skill Classification Settings
MAX_REQUIRED_SKILLS = 12
MAX_NICE_TO_HAVE_SKILLS = 8
MIN_REQUIRED_SKILLS = 3
SOFT_SKILL_THRESHOLD = 0.30  # Max 30% of required skills can be soft skills

# Confidence Thresholds
HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.70
LOW_CONFIDENCE_THRESHOLD = 0.70

# --- Skill Ratio Combination Weights ---
REQUIRED_SKILL_WEIGHT = 0.70
NICE_TO_HAVE_SKILL_WEIGHT = 0.30


def combine_skill_ratios(required_ratio: float, nice_to_have_ratio: float) -> int:
    """Combine required and nice-to-have skill match ratios using standard weights."""
    return round((required_ratio * REQUIRED_SKILL_WEIGHT) + (nice_to_have_ratio * NICE_TO_HAVE_SKILL_WEIGHT))


# --- Skill Synonyms (variant/abbreviation → canonical form) ---
SKILL_SYNONYMS = {
    # Language abbreviations
    "js": "javascript", "ts": "typescript", "py": "python",
    "rb": "ruby", "cs": "csharp", "c#": "csharp", "c++": "cpp",
    "cpp": "cpp", "go": "golang", "rs": "rust",

    # Framework variants
    "react.js": "react", "reactjs": "react", "react native": "react native",
    "vue.js": "vue", "vuejs": "vue",
    "angular.js": "angularjs", "angularjs": "angularjs",
    "node.js": "nodejs", "node": "nodejs",
    "next.js": "nextjs", "nuxt.js": "nuxtjs",
    "express.js": "express", "expressjs": "express",
    "nest.js": "nestjs", "nestjs": "nestjs",

    # Database variants
    "postgres": "postgresql", "pg": "postgresql",
    "mongo": "mongodb", "mongodb": "mongodb",
    "ms sql": "mssql", "sql server": "mssql",
    "mysql": "mysql", "maria": "mariadb", "mariadb": "mariadb",
    "dynamodb": "dynamodb", "dynamo": "dynamodb",
    "redis": "redis", "memcache": "memcached",

    # Cloud/DevOps
    "k8s": "kubernetes", "kube": "kubernetes",
    "aws": "aws", "amazon web services": "aws",
    "gcp": "google cloud platform", "google cloud": "google cloud platform",
    "azure": "microsoft azure", "ms azure": "microsoft azure",
    "tf": "terraform", "gh actions": "github actions",
    "ci/cd": "cicd", "ci cd": "cicd",

    # Tools
    "git": "git", "github": "github", "gitlab": "gitlab",
    "vscode": "visual studio code", "vs code": "visual studio code",
    "docker compose": "docker-compose",
    "rabbitmq": "rabbitmq", "rabbit": "rabbitmq",
    "kafka": "apache kafka",
    "elasticsearch": "elasticsearch", "elastic": "elasticsearch", "es": "elasticsearch",

    # Data/ML
    "ml": "machine learning", "ai": "artificial intelligence",
    "dl": "deep learning", "nlp": "natural language processing",
    "cv": "computer vision",
    "pytorch": "pytorch", "torch": "pytorch",
    "sklearn": "scikit-learn", "scikit learn": "scikit-learn",
    "pandas": "pandas", "numpy": "numpy",

    # Mobile
    "rn": "react native", "ios": "ios", "android": "android",
    "flutter": "flutter", "swift": "swift", "kotlin": "kotlin",
    "objective-c": "objective-c", "objc": "objective-c",

    # Testing
    "jest": "jest", "mocha": "mocha", "pytest": "pytest",
    "junit": "junit", "cypress": "cypress", "selenium": "selenium",

    # Messaging/API
    "rest": "rest api", "restful": "rest api", "rest api": "rest api",
    "graphql": "graphql", "grpc": "grpc",
    "websocket": "websockets", "ws": "websockets",

    # Methodologies
    "agile": "agile", "scrum": "scrum", "kanban": "kanban",
    "tdd": "test driven development", "bdd": "behavior driven development",
    "oop": "object oriented programming",

    # Frontend
    "html5": "html", "css3": "css",
    "sass": "sass", "scss": "sass", "less": "less",
    "tailwind": "tailwindcss", "tailwind css": "tailwindcss",
    "bootstrap": "bootstrap", "material ui": "material-ui", "mui": "material-ui",

    # Salesforce
    "apexcode": "apex",
    "apex code": "apex",
    "sfdc apex": "apex",
    "webservices": "soap",
    "web services": "soap",
    "soap api": "soap",
    "vf": "visualforce",
    "visual force": "visualforce",
    "lwc": "lightning",
    "lightning web component": "lightning",
    "sfdc": "salesforce",
    "sf.com": "salesforce",
    "salesforce.com": "salesforce",

    # Others
    "linux": "linux", "unix": "unix", "bash": "bash", "shell": "shell scripting",
    "powershell": "powershell", "ps": "powershell",
    "nginx": "nginx", "apache": "apache http server",
}


# --- Skill Hierarchy (child skill → parent + category) ---
SKILL_HIERARCHY = {
    # Frontend Frameworks → Language
    "react": {"parent": "javascript", "category": "frontend_framework"},
    "vue": {"parent": "javascript", "category": "frontend_framework"},
    "angular": {"parent": "typescript", "category": "frontend_framework"},
    "svelte": {"parent": "javascript", "category": "frontend_framework"},
    "nextjs": {"parent": "react", "category": "fullstack_framework"},
    "nuxtjs": {"parent": "vue", "category": "fullstack_framework"},

    # Backend Frameworks → Language
    "django": {"parent": "python", "category": "backend_framework"},
    "fastapi": {"parent": "python", "category": "backend_framework"},
    "flask": {"parent": "python", "category": "backend_framework"},
    "spring": {"parent": "java", "category": "backend_framework"},
    "spring boot": {"parent": "java", "category": "backend_framework"},
    "express": {"parent": "nodejs", "category": "backend_framework"},
    "nestjs": {"parent": "typescript", "category": "backend_framework"},
    "rails": {"parent": "ruby", "category": "backend_framework"},
    "laravel": {"parent": "php", "category": "backend_framework"},
    "asp.net": {"parent": "csharp", "category": "backend_framework"},
    "gin": {"parent": "golang", "category": "backend_framework"},
    "actix": {"parent": "rust", "category": "backend_framework"},

    # Container/Orchestration
    "kubernetes": {"parent": "docker", "category": "container_orchestration"},
    "docker-compose": {"parent": "docker", "category": "container_orchestration"},
    "helm": {"parent": "kubernetes", "category": "package_management"},

    # Cloud Services → Platform
    "lambda": {"parent": "aws", "category": "serverless"},
    "ec2": {"parent": "aws", "category": "compute"},
    "s3": {"parent": "aws", "category": "storage"},
    "rds": {"parent": "aws", "category": "database"},
    "cloud functions": {"parent": "google cloud platform", "category": "serverless"},
    "azure functions": {"parent": "microsoft azure", "category": "serverless"},

    # Data/ML → Foundation
    "tensorflow": {"parent": "python", "category": "ml_framework"},
    "pytorch": {"parent": "python", "category": "ml_framework"},
    "scikit-learn": {"parent": "python", "category": "ml_library"},
    "pandas": {"parent": "python", "category": "data_library"},
    "numpy": {"parent": "python", "category": "data_library"},
    "spark": {"parent": "python", "category": "big_data"},

    # Testing → Language
    "jest": {"parent": "javascript", "category": "testing_framework"},
    "pytest": {"parent": "python", "category": "testing_framework"},
    "junit": {"parent": "java", "category": "testing_framework"},
    "cypress": {"parent": "javascript", "category": "e2e_testing"},

    # Mobile
    "react native": {"parent": "react", "category": "mobile_framework"},
    "flutter": {"parent": "dart", "category": "mobile_framework"},
    "swiftui": {"parent": "swift", "category": "ui_framework"},
    "jetpack compose": {"parent": "kotlin", "category": "ui_framework"},

    # IaC
    "terraform": {"parent": "infrastructure as code", "category": "iac"},
    "cloudformation": {"parent": "aws", "category": "iac"},
    "ansible": {"parent": "infrastructure as code", "category": "configuration_management"},

    # Databases → Category
    "postgresql": {"parent": "sql", "category": "relational_database"},
    "mysql": {"parent": "sql", "category": "relational_database"},
    "mssql": {"parent": "sql", "category": "relational_database"},
    "mongodb": {"parent": "nosql", "category": "document_database"},
    "redis": {"parent": "nosql", "category": "key_value_store"},
    "elasticsearch": {"parent": "nosql", "category": "search_engine"},

    # Salesforce
    "apex": {"parent": "salesforce", "category": "programming"},
    "visualforce": {"parent": "salesforce", "category": "ui"},
    "lightning": {"parent": "salesforce", "category": "ui"},
    "soql": {"parent": "salesforce", "category": "query"},
    "lwc": {"parent": "salesforce", "category": "ui"},
    "soap": {"parent": "web_services", "category": "protocol"},

    # AWS
    "lambda": {"parent": "aws", "category": "compute"},
    "s3": {"parent": "aws", "category": "storage"},
    "dynamodb": {"parent": "aws", "category": "database"},
    "ec2": {"parent": "aws", "category": "compute"},

    # SAP
    "abap": {"parent": "sap", "category": "programming"},
}


# --- API Limits and Timeouts ---
# Centralized constants for API request limits, timeouts, and sizes

# File Upload Limits
MAX_RESUME_FILE_SIZE_MB = 10
MAX_JD_SIZE_BYTES = 50 * 1024  # 50KB
MAX_SCORING_WEIGHTS_SIZE_BYTES = 4 * 1024  # 4KB
MAX_PDF_PAGES = 500
ALLOWED_RESUME_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.rtf', '.odt')

# Batch Processing
MAX_BATCH_SIZE = 50
DEFAULT_BATCH_CONCURRENT = 30

# Timeouts (seconds)
DEFAULT_PARSE_TIMEOUT_SECONDS = 30
DEFAULT_LLM_NARRATIVE_TIMEOUT = 500
DEFAULT_OLLAMA_WARMUP_TIMEOUT = 300
DEFAULT_OLLAMA_WAIT_TIMEOUT = 120

# Pagination
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Rate Limiting
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
DEFAULT_RATE_LIMIT_BURST = 10

# Cache TTL (seconds)
DEFAULT_JD_CACHE_TTL = 3600  # 1 hour
SKILL_CACHE_TTL = 86400  # 24 hours

# Queue Settings
QUEUE_STALE_JOB_TIMEOUT_SECONDS = 3600  # 1 hour
QUEUE_MAX_RETRIES = 3
QUEUE_RETRY_DELAY_SECONDS = 60

# Session Settings
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS = 30
IDLE_TIMEOUT_MINUTES = 30
