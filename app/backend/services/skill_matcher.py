"""
Shared skill matcher — single source of truth for skill matching.
"""

import logging
import re
from functools import lru_cache
from typing import Dict, List, Any, Pattern, Tuple

from app.backend.services.constants import SKILL_SYNONYMS, SKILL_HIERARCHY

# Extend imported hierarchy with additional cloud, platform, and framework entries
SKILL_HIERARCHY = {**SKILL_HIERARCHY, **{
    # GCP
    "bigquery": {"parent": "gcp", "category": "analytics"},
    "cloud functions": {"parent": "gcp", "category": "compute"},
    "pubsub": {"parent": "gcp", "category": "messaging"},
    "compute engine": {"parent": "gcp", "category": "compute"},

    # Azure
    "azure functions": {"parent": "azure", "category": "compute"},
    "cosmos db": {"parent": "azure", "category": "database"},
    "azure devops": {"parent": "azure", "category": "devops"},
    "app service": {"parent": "azure", "category": "compute"},

    # Docker/K8s ecosystem
    "helm": {"parent": "kubernetes", "category": "packaging"},
    "kubectl": {"parent": "kubernetes", "category": "cli"},
    "docker compose": {"parent": "docker", "category": "orchestration"},

    # Spring ecosystem
    "spring security": {"parent": "spring boot", "category": "security"},
    "spring data": {"parent": "spring boot", "category": "data"},

    # React ecosystem
    "redux": {"parent": "react", "category": "state"},
    "next.js": {"parent": "react", "category": "framework"},
}}

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# O*NET OCCUPATION-AWARE VALIDATION (OPTIONAL)
# ═══════════════════════════════════════════════════════════════════════════════

_onet_validator = None


def _get_onet_validator():
    """Lazy singleton for ONETValidator."""
    global _onet_validator
    if _onet_validator is None:
        try:
            from app.backend.services.onet import ONETValidator
            _onet_validator = ONETValidator()
        except Exception as e:
            logger.warning("ONETValidator init failed: %s", e)
            _onet_validator = None
    return _onet_validator


def match_skills_with_onet(
    candidate_skills, jd_skills, jd_text="", jd_nice_to_have=None, job_title=None,
    structured_skills=None, text_scanned_skills=None
):
    """Enhanced skill matching with O*NET occupation context.

    Falls back to standard :func:`match_skills` if O*NET data is unavailable
    or no *job_title* is provided.

    .. deprecated:: jd_text
        Kept for backward compatibility (agent_pipeline.py). Ignored internally.
        Use *text_scanned_skills* instead.
    """
    result = match_skills(candidate_skills, jd_skills, jd_nice_to_have,
                          structured_skills=structured_skills,
                          text_scanned_skills=text_scanned_skills)

    validator = _get_onet_validator()
    if validator is not None and validator.available and job_title:
        try:
            onet_result = validator.validate_skills_batch(
                [m for m in result.get("matched_skills", [])],
                job_title,
            )
            result["onet_validation"] = onet_result

            # Filter: remove high-collision skills that O*NET says are NOT valid for this occupation
            if onet_result.get("skill_validations"):
                invalid_high_collision = set()
                for skill_name, validation in onet_result["skill_validations"].items():
                    norm = _normalize_skill(skill_name)
                    if norm in HIGH_COLLISION_SKILLS and not validation.get("valid", True):
                        invalid_high_collision.add(skill_name)
                if invalid_high_collision:
                    result["matched_skills"] = [s for s in result["matched_skills"]
                                                if s not in invalid_high_collision]
                    result["onet_filtered"] = list(invalid_high_collision)
        except Exception as e:
            logger.warning("O*NET validation failed (non-fatal): %s", e)

    return result

# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SKILLS LIST
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_SKILLS: List[str] = [
    # ── Programming languages ──────────────────────────────────────────────────
    "python", "java", "javascript", "typescript", "c++", "c#", "c", "golang", "go",
    "rust", "scala", "kotlin", "swift", "ruby", "php", "r", "matlab", "perl",
    "haskell", "erlang", "elixir", "clojure", "f#", "lua", "dart", "zig", "ada",
    "assembly", "bash", "powershell", "groovy", "cobol", "fortran", "vba",
    "nim", "crystal", "julia", "ocaml", "reasonml", "purescript", "elm",
    "solidity", "vyper", "move", "cairo",
    # ── Web frameworks ─────────────────────────────────────────────────────────
    "react", "vue.js", "vue", "angular", "next.js", "nuxt.js", "svelte", "astro",
    "remix", "gatsby", "ember.js", "backbone.js", "jquery", "bootstrap", "tailwind",
    "material ui", "chakra ui", "ant design", "storybook",
    "node.js", "express.js", "fastapi", "django", "flask", "tornado", "aiohttp",
    "starlette", "litestar", "spring boot", "spring", "quarkus", "micronaut",
    "nestjs", "koa", "hapi", "feathers", "strapi", "rails", "sinatra", "laravel",
    "symfony", "codeigniter", "gin", "fiber", "echo", "chi", "actix", "axum", "rocket",
    "htmx", "alpine.js", "lit", "stencil", "solid.js", "qwik", "preact",
    "blazor", "asp.net", "asp.net core", "dotnet", ".net", "wpf", "winforms",
    # ── Databases ──────────────────────────────────────────────────────────────
    "postgresql", "mysql", "sqlite", "mariadb", "oracle", "microsoft sql server",
    "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb", "couchdb",
    "neo4j", "influxdb", "timescaledb", "cockroachdb", "planetscale",
    "snowflake", "bigquery", "redshift", "databricks", "clickhouse",
    "supabase", "firebase", "firestore", "realm", "fauna", "deno kv",
    "sqlalchemy", "hibernate", "prisma", "typeorm", "sequelize", "drizzle",
    "mongoose", "redis om", "entity framework", "dapper",
    "db2", "informix", "sybase", "teradata", "greenplum",
    # ── Cloud platforms ────────────────────────────────────────────────────────
    "amazon web services", "aws", "google cloud platform", "gcp", "microsoft azure",
    "azure", "digital ocean", "linode", "vultr", "hetzner", "oracle cloud",
    "ibm cloud", "alibaba cloud", "cloudflare", "vercel", "netlify", "heroku",
    "fly.io", "render", "railway", "supabase", "firebase", "appwrite", "pocketbase",
    "cloudfoundry", "openshift", "rancher", "nomad", "hashicorp",
    # ── AWS services ───────────────────────────────────────────────────────────
    "ec2", "s3", "lambda", "ecs", "eks", "rds", "aurora", "elasticache",
    "api gateway", "cloudfront", "route53", "iam", "cloudwatch", "sns", "sqs",
    "kinesis", "glue", "athena", "emr", "sagemaker", "bedrock",
    "dynamodb", "step functions", "eventbridge", "sqs", "ses", "sns",
    "vpc", "alb", "nlb", "autoscaling", "cloudformation", "elastic beanstalk",
    # ── DevOps & infrastructure ────────────────────────────────────────────────
    "docker", "kubernetes", "helm", "terraform", "ansible", "puppet", "chef",
    "vagrant", "packer", "jenkins", "github actions", "gitlab ci", "circleci",
    "travis ci", "bitbucket pipelines", "argo cd", "flux", "spinnaker", "tekton",
    "prometheus", "grafana", "datadog", "new relic", "dynatrace", "splunk",
    "elk stack", "loki", "jaeger", "zipkin", "opentelemetry",
    "nginx", "apache", "traefik", "envoy", "istio", "linkerd", "consul",
    "vault", "linux", "unix", "ubuntu", "centos", "rhel", "debian",
    "ci/cd", "devops", "sre", "infrastructure as code", "gitops",
    "pulumi", "crossplane", "backstage", "kustomize", "skaffold", "tilt",
    "bazel", "buck", "nix", "guix", "chocolatey", "homebrew",
    # ── AI / ML ────────────────────────────────────────────────────────────────
    "machine learning", "deep learning", "neural networks", "natural language processing",
    "nlp", "computer vision", "reinforcement learning", "generative ai",
    "large language models", "llm", "transformers", "bert", "gpt",
    "pytorch", "tensorflow", "keras", "jax", "mxnet", "caffe",
    "scikit-learn", "xgboost", "lightgbm", "catboost", "statsmodels",
    "hugging face", "langchain", "llamaindex", "ollama", "openai",
    "anthropic", "cohere", "vector database", "rag", "fine-tuning",
    "mlflow", "wandb", "optuna", "ray", "kubeflow", "vertex ai",
    "opencv", "pillow", "albumentations", "detectron", "yolo",
    "stable diffusion", "midjourney", "comfyui", "diffusers",
    "spacy", "nltk", "gensim", "fasttext", "word2vec", "doc2vec",
    "tensorflow lite", "onnx", "tensorrt", "openvino", "coreml",
    "automl", "feature store", "feast", "tecton",
    # ── Data science & analytics ───────────────────────────────────────────────
    "pandas", "numpy", "scipy", "matplotlib", "seaborn", "plotly", "bokeh",
    "jupyter", "jupyter notebook", "colab", "dask", "polars", "vaex",
    "apache spark", "pyspark", "hadoop", "hive", "pig", "flink",
    "apache kafka", "rabbitmq", "celery", "airflow", "prefect", "dagster",
    "dbt", "fivetran", "airbyte", "stitch", "etl", "data pipeline",
    "tableau", "power bi", "looker", "metabase", "superset", "grafana",
    "excel", "google sheets", "data analysis", "statistics", "a/b testing",
    "data warehousing", "data lake", "data mesh", "data governance",
    "feature engineering", "data mining", "predictive modeling", "time series analysis",
    "sas", "spss", "minitab", "stata",
    # ── Embedded / systems ─────────────────────────────────────────────────────
    "embedded", "rtos", "freertos", "zephyr", "vxworks", "qnx", "embedded linux",
    "microcontroller", "fpga", "arm", "arm cortex", "avr", "pic",
    "uart", "spi", "i2c", "can bus", "modbus", "ethernet",
    "tcp/ip", "udp", "mqtt", "coap", "ble", "zigbee", "lorawan",
    "device driver", "bsp", "bootloader", "firmware", "real-time",
    "ipc", "multithreading", "multiprocessing", "posix",
    "cmake", "makefile", "openocd", "jtag", "gdb",
    "iso 26262", "misra", "do-178", "sil4", "functional safety",
    "plc", "ladder logic", "scada", "industrial automation", "hart",
    # ── Mobile ─────────────────────────────────────────────────────────────────
    "ios", "android", "react native", "flutter", "xamarin", "ionic", "capacitor",
    "swift", "swiftui", "objective-c", "kotlin", "jetpack compose",
    "xcode", "android studio", "expo", "firebase", "appcenter",
    "cordova", "phonegap", "titanium", "unity mobile",
    # ── Testing ────────────────────────────────────────────────────────────────
    "unit testing", "integration testing", "e2e testing", "tdd", "bdd",
    "pytest", "unittest", "jest", "vitest", "mocha", "jasmine", "cypress",
    "playwright", "selenium", "appium", "postman", "insomnia",
    "k6", "locust", "jmeter", "gatling",
    "sonarqube", "codecov", "coveralls", "code review",
    "mutation testing", "property based testing", "fuzz testing",
    # ── Architecture & design ──────────────────────────────────────────────────
    "microservices", "monolith", "event-driven", "cqrs", "event sourcing",
    "domain driven design", "ddd", "hexagonal architecture", "clean architecture",
    "rest api", "graphql", "grpc", "websocket", "mqtt", "openapi", "swagger",
    "system design", "distributed systems", "high availability", "scalability",
    "design patterns", "solid", "ooad", "uml", "soa",
    "api gateway", "service mesh", "sidecar", "circuit breaker", "bulkhead",
    "saga pattern", "outbox pattern", "inbox pattern", "strangler fig",
    "twelve-factor app", "cap theorem", "acid", "base",
    # ── Security ───────────────────────────────────────────────────────────────
    "oauth2", "jwt", "saml", "ldap", "iam", "rbac",
    "tls", "ssl", "cryptography", "penetration testing", "owasp",
    "soc2", "gdpr", "hipaa", "pci dss",
    "zero trust", "siem", "soar", "edr", "xdr", "mdr",
    "vulnerability scanning", "threat modeling", "bug bounty",
    "pkcs", "x.509", "aes", "rsa", "ecc", "sha", "md5",
    # ── Project management ─────────────────────────────────────────────────────
    "agile", "scrum", "kanban", "safe", "lean",
    "jira", "confluence", "linear", "asana", "trello", "notion",
    "git", "github", "gitlab", "bitbucket", "svn",
    "project management", "product management", "technical lead",
    "team lead", "engineering manager",
    "program management", "portfolio management", "release management",
    "change management", "itil", "cobit", "ppm",
    # ── Design & UX ───────────────────────────────────────────────────────────
    "figma", "sketch", "adobe xd", "invision", "zeplin",
    "ui/ux", "user research", "wireframing", "prototyping",
    "accessibility", "wcag", "responsive design",
    "design system", "component library", "storybook",
    "adobe creative suite", "photoshop", "illustrator", "after effects",
    "blender", "cinema 4d", "maya", "3ds max",
    "motion design", "interaction design", "information architecture",
    "usability testing", "heuristic evaluation", "card sorting",
    # ── Blockchain ─────────────────────────────────────────────────────────────
    "blockchain", "solidity", "ethereum", "web3", "smart contracts",
    "defi", "nft", "hyperledger", "polkadot",
    "bitcoin", "cosmos", "tendermint", "avalanche", "solana", "cardano",
    "chainlink", "the graph", "ipfs", "filecoin",
    "zero knowledge", "zk-snarks", "zk-starks", "rollups", "layer 2",
    # ── Gaming / AR / VR ───────────────────────────────────────────────────────
    "unity", "unreal engine", "godot", "cryengine", "game maker",
    "cocos2d", "phaser", "babylon.js", "three.js", "webgl", "webgpu",
    "openxr", "arkit", "arcore", "vuforia", "metaverse",
    "shader programming", "hlsl", "glsl", "compute shaders",
    # ── Networking ─────────────────────────────────────────────────────────────
    "networking", "tcp/ip", "udp", "http", "http/2", "http/3", "quic",
    "dns", "dhcp", "vpn", "ipsec", "wireguard", "openvpn",
    "load balancing", "cdn", "reverse proxy", "waf", "ddos protection",
    "bgp", "ospf", "mpls", "sdn", "nfv", "vlan", "vxlan",
    "firewall", "ids", "ips", "network security", "packet analysis",
    # ── Operating systems ──────────────────────────────────────────────────────
    "windows", "windows server", "linux", "macos", "unix",
    "freebsd", "openbsd", "netbsd", "solaris", "aix",
    "embedded linux", "android os", "ios", "chrome os",
    "active directory", "group policy", "ldap", "kerberos",
    # ── CRM / ERP / Business ───────────────────────────────────────────────────
    "salesforce", "hubspot", "zoho", "pipedrive", "freshsales",
    "sap", "sap mm", "sap s/4hana", "sap s4hana", "sap s/4 hana",
    "sap mm configuration", "sap sd", "sap pp", "sap fi", "sap wm",
    "sap ariba", "sap srm", "sap ewm", "solution manager",
    "procure-to-pay", "p2p", "purchase requisition", "purchase order",
    "goods receipt", "inventory management", "invoice verification",
    "material master data", "vendor master data", "idoc",
    "subcontracting", "consignment", "stock transfer order",
    "release strategy", "request for quotation", "batch management",
    "oracle erp", "dynamics 365", "netsuite", "workday",
    "servicenow", "jira service management", "freshdesk", "zendesk",
    "sharepoint", "power platform", "power apps", "power automate",
    "outsystems", "mendix", "appian", "apex", "soap", "visualforce", "soql", "lightning", "lwc",
    "business analysis", "business intelligence", "data modeling",
    "process modeling", "bpmn", "uml", "enterprise architecture",
    "togaf", "archimate", "zachman",
    # ── Soft skills & non-technical ────────────────────────────────────────────
    "communication", "leadership", "mentoring", "documentation",
    "technical writing", "code review", "pair programming",
    "problem solving", "critical thinking", "analytical thinking",
    "creativity", "innovation", "adaptability", "flexibility",
    "time management", "prioritization", "organization",
    "collaboration", "teamwork", "cross-functional collaboration",
    "stakeholder management", "client management", "customer success",
    "conflict resolution", "negotiation", "persuasion",
    "public speaking", "presentation skills", "storytelling",
    "emotional intelligence", "empathy", "cultural awareness",
    "decision making", "strategic thinking", "vision",
    "coaching", "feedback", "performance management",
    "hiring", "recruiting", "talent acquisition", "onboarding",
    "budget management", "financial planning", "roi analysis",
    "vendor management", "contract negotiation", "procurement",
    "compliance", "risk management", "business continuity",
    "change management", "organizational development",
    "quality assurance", "process improvement", "six sigma", "lean manufacturing",
    "root cause analysis", "fishbone diagram", "5 whys",
    "technical support", "customer support", "help desk", "it support",
    "training", "knowledge management", "wiki", "confluence",
    "event planning", "workshop facilitation", "sprint planning",
    "daily standups", "retrospectives", "demos", "backlog grooming",
    "user stories", "acceptance criteria", "definition of done",
    "market research", "competitive analysis", "product strategy",
    "go-to-market", "pricing strategy", "revenue modeling",
    "sales engineering", "solution architecture", "presales",
    "account management", "relationship management", "partnerships",
    "content strategy", "content marketing", "seo", "sem",
    "social media marketing", "email marketing", "marketing automation",
    "brand management", "public relations", "copywriting",
    "video editing", "audio editing", "podcasting", "streaming",
    "data entry", "transcription", "translation", "localization",
    # ── Misc ──────────────────────────────────────────────────────────────────
    "webscraping", "beautifulsoup", "scrapy", "selenium",
    "celery", "redis queue", "bull", "sidekiq",
    "protobuf", "avro", "json schema", "thrift", "grpc",
    "regex", "xml", "yaml", "toml", "json", "csv",
    "rest", "soap", "graphql", "webhook", "api design",
    "seo", "analytics", "google analytics", "segment", "mixpanel",
    "amplitude", "hotjar", "fullstory", "crazy egg", "optimizely",
    "ab testing", "multivariate testing", "conversion rate optimization",
    "latex", "markdown", "asciidoc", "restructuredtext",
    "vim", "emacs", "vscode", "intellij", "pycharm", "webstorm",
    "eclipse", "netbeans", "visual studio", "rider", "clion",
    "postman", "insomnia", "swagger ui", "hoppscotch",
    "git", "gitflow", "trunk based development", "monorepo", "turborepo", "nx",
    "dependabot", "snyk", "black duck", "whitesource", " FOSSA",
    "swagger", "openapi", "postman", "graphql playground",
    "diagrams", "mermaid", "plantuml", "draw.io", "lucidchart",
    "chatgpt", "claude", "copilot", "cursor", "codeium", "tabnine",
    "notion", "obsidian", "roam research", "logseq", "evernote",
    "slack", "microsoft teams", "discord", "zoom", "webex", "google meet",
    "loom", "gitpitch", "canva", "gamma",
    # ── Healthcare & Medical ───────────────────────────────────────────────────
    "patient care", "clinical assessment", "medical terminology", "patient safety",
    "electronic health records", "ehr", "emr", "epic", "cerner", "medical coding",
    "icd-10", "cpt", "health informatics", "public health", "epidemiology",
    "infection control", "care coordination", "medical device", "pharmaceutical",
    "clinical trial", "telemedicine", "nursing", "pharmacy", "radiology",
    "laboratory", "diagnostic", "treatment planning", "medication administration",
    "vital signs", "patient discharge", "hipaa", "medical research",
    "patient education", "wound care", "iv therapy", "patient advocacy",
    "medical imaging", "ultrasound", "mri", "ct scan", "x-ray",
    "pathology", "histology", "hematology", "microbiology",
    "anatomy", "physiology", "pharmacology", "biochemistry",
    "occupational therapy", "physical therapy", "speech therapy",
    "mental health", "psychiatric nursing", "geriatric care",
    "pediatric care", "emergency medicine", "critical care",
    "anesthesia", "surgical technology", "sterile technique",
    "meditech", "eclinicalworks", "allscripts", "athenahealth", "nextgen",
    "medicare", "medicaid", "hipaa compliance", "joint commission",
    # ── Finance & Accounting ───────────────────────────────────────────────────
    "financial analysis", "accounting", "gaap", "ifrs", "financial reporting",
    "reconciliation", "general ledger", "accounts payable", "accounts receivable",
    "quickbooks", "xero", "sage", "netsuite", "financial modeling", "budgeting", "forecasting",
    "audit", "tax", "treasury", "sox", "internal controls",
    "valuation", "dcf", "npv", "irr", "financial statements",
    "cost accounting", "variance analysis", "capital budgeting",
    "balance sheet", "cash flow", "p&l", "profit loss",
    "sap fi", "oracle financials", "financial planning",
    "risk assessment", "compliance", "payroll", "bookkeeping",
    "bank reconciliation", "invoice processing", "expense management",
    "tax preparation", "corporate tax", "indirect tax", "vat", "gst",
    "transfer pricing", "customs duty", "financial audit", "internal audit",
    "forensic accounting", "fp&a", "financial planning analysis",
    # ── Legal ──────────────────────────────────────────────────────────────────
    "legal research", "legal writing", "contract review", "litigation",
    "regulatory compliance", "due diligence", "corporate law", "ip law",
    "patent law", "trademark", "copyright", "legal analysis",
    "westlaw", "lexisnexis", "contract drafting", "legal counsel",
    "employment law", "labor law", "real estate law", "criminal law",
    "civil litigation", "arbitration", "mediation", "discovery",
    "deposition", "pleading", "motion practice", "evidence",
    "nda", "non-disclosure", "mergers acquisitions", "legal hold",
    "bar admission", "legal compliance", "privacy law", "data protection",
    "competition law", "antitrust", "securities law", "bankruptcy",
    "immigration law", "family law", "environmental law",
    # ── Marketing ──────────────────────────────────────────────────────────────
    "digital marketing", "content marketing", "seo", "sem", "social media marketing",
    "email marketing", "marketing automation", "google analytics", "google ads",
    "facebook ads", "ppc", "campaign management", "brand management",
    "marketing strategy", "conversion optimization", "customer segmentation",
    "market research", "competitive analysis", "go-to-market",
    "content strategy", "copywriting", "marketing funnel",
    "marketo", "pardot", "mailchimp", "hootsuite", "buffer",
    "marketing roi", "ab testing", "conversion rate optimization",
    "influencer marketing", "affiliate marketing", "marketing analytics",
    "demand generation", "lead nurturing", "marketing qualified leads",
    "sales qualified leads", "customer acquisition cost", "lifetime value",
    # ── HR ─────────────────────────────────────────────────────────────────────
    "recruitment", "talent acquisition", "onboarding", "employee relations",
    "performance management", "hris", "workday", "successfactors",
    "bamboo hr", "payroll", "benefits administration", "compensation",
    "job analysis", "employee engagement", "diversity inclusion",
    "training development", "l&d", "labor law compliance",
    "grievance handling", "exit interviews", "workforce planning",
    "succession planning", "hr analytics", "applicant tracking system",
    "ats", "background verification", "policy development",
    "organizational development", "change management",
    "competency mapping", "360 feedback", "performance appraisal",
    # ── Education ──────────────────────────────────────────────────────────────
    "curriculum development", "lesson planning", "pedagogy", "assessment",
    "classroom management", "educational technology", "e-learning",
    "instructional design", "teaching", "tutoring", "student engagement",
    "differentiated instruction", "special education", "iep",
    "stem education", "early childhood education", "higher education",
    "academic research", "grant writing", "formative assessment",
    "summative assessment", "learning management system", "lms",
    "canvas", "blackboard", "moodle", "google classroom",
    "schoology", "edtech", "blended learning", "distance learning",
    "rubric design", "student assessment", "curriculum mapping",
    # ── Manufacturing & Operations ─────────────────────────────────────────────
    "lean manufacturing", "six sigma", "quality control", "iso 9001",
    "production planning", "supply chain", "inventory management",
    "mrp", "capacity planning", "continuous improvement",
    "kaizen", "5s", "poka-yoke", "statistical process control", "spc",
    "root cause analysis", "safety compliance", "osha",
    "cnc", "cad", "cam", "autocad", "solidworks",
    "manufacturing execution system", "mes",
    "total productive maintenance", "tpm", "value stream mapping",
    "process optimization", "quality assurance", "lean six sigma",
    "green belt", "black belt", "iso 14001", "iso 45001",
    # ── Logistics & Supply Chain ───────────────────────────────────────────────
    "supply chain management", "logistics", "warehousing", "distribution",
    "freight", "shipping", "inventory control", "demand forecasting",
    "procurement", "vendor management", "3pl", "4pl",
    "transportation management", "wms", "tms",
    "customs clearance", "import export", "incoterms", "hs code",
    "freight forwarding", "last mile delivery", "route optimization",
    "fleet management", "cold chain", "reverse logistics",
    "supply chain optimization", "s&op", "demand planning",
    # ── Construction ───────────────────────────────────────────────────────────
    "construction management", "site supervision", "building codes",
    "revit", "bim", "primavera", "ms project",
    "cost estimation", "quantity surveying", "blueprint reading",
    "contract administration", "change order", "rfi",
    "submittal", "leed certification", "sustainable construction",
    "structural engineering", "civil engineering",
    "hvac", "electrical systems", "plumbing", "concrete",
    "steel structure", "foundation", "demolition", "renovation",
    # ── Consulting ─────────────────────────────────────────────────────────────
    "strategy consulting", "management consulting", "business analysis",
    "business transformation", "digital transformation",
    "operational excellence", "process improvement",
    "case study", "executive presentation", "workshop facilitation",
    "requirements gathering", "gap analysis", "business case",
    "transformation roadmap", "process mapping",
    "agile transformation", "organizational design",
    # ── Recruiting & HR Tools (everyday recruiter use cases) ───────────────────
    "greenhouse", "lever", "workday recruiting", "icims", "jobvite", "adp",
    "smartrecruiters", "taleo", "recruitee", "breezy hr", "jazzhr",
    "linkedin recruiter", "indeed", "monster", "naukri", "glassdoor",
    "ziprecruiter", "careerbuilder", "angelList", "wellfound", "hireez",
    "gem", "seekout", "hiretual", "entelo", "pymetrics", "paradox",
    "mya", "xor", "textio", "phenom", "eightfold", "beamery",
    "adp workforce now", "paylocity", "paychex", "gusto", "deel",
    "rippling", "bamboohr", "namely", "zenefits", "factorial",
    "hi bob", "lattice", "15five", "culture amp", "glint",
    "qualtrics", "survey monkey", "typeform", "officevibe",
    "linkedin learning", "udemy", "coursera", "pluralsight", "skillsoft",
    "cornerstone ondemand", "saba", "docebo", "lessonly", "thinkific",
    # ── Sales & Customer Success ───────────────────────────────────────────────
    "salesforce", "hubspot", "zoho", "pipedrive",
    "freshsales", "insightly", "copper crm", "nimble", "close crm",
    "salesloft", "outreach", "apollo.io", "zoominfo", "lusha",
    "seamless.ai", "clearbit", "linkedin sales navigator", "gong",
    "chorus.ai", "execvision", "clari", "revenue grid", "prolifiq",
    "customer success", "customer onboarding", "account management",
    "renewals management", "upsell", "cross-sell", "churn reduction",
    "net promoter score", "nps", "customer health scoring",
    "support ticket management", "help desk", "live chat", "chatbot",
    "intercom", "zendesk", "freshdesk", "kustomer", "crisp",
    # ── Operations & Administration ───────────────────────────────────────────
    "operations management", "business operations", "office management",
    "executive assistance", "virtual assistance", "administrative support",
    "calendar management", "travel coordination", "expense reporting",
    "data entry", "document management", "file management",
    "microsoft office", "ms office", "microsoft word", "microsoft excel",
    "microsoft powerpoint", "microsoft outlook", "microsoft sharepoint",
    "google workspace", "google docs", "google sheets", "google slides",
    "google forms", "google drive", "google calendar",
    "slack", "microsoft teams", "zoom", "webex", "gotomeeting",
    "asana", "monday.com", "clickup", "notion", "trello", "smartsheet",
    "wrike", "basecamp", "teamwork", "proofhub", "airtable",
    "confluence", "sharepoint", "oneDrive", "dropbox", "box",
    "docusign", "adobe sign", "hellosign", "pandadoc", "signnow",
    "quickbooks", "xero", "freshbooks", "wave accounting", "zoho books",
    "expensify", "concur", "tripactions", "navan", "brex", "ramp",
    "event planning", "vendor coordination", "facilities management",
    "procurement", "purchasing", "inventory control", "asset management",
    # ── Additional Industry & Domain Skills ────────────────────────────────────
    "clinical research", "regulatory affairs", "pharmacovigilance",
    "quality assurance", "qa", "gmp", "glp", "gcp", "fda",
    "underwriting", "claims processing", "risk management", "actuarial",
    "property management", "real estate", "lease administration",
    "hospitality management", "food and beverage", "restaurant management",
    "retail management", "merchandising", "visual merchandising",
    "supply chain operations", "warehouse operations", "distribution center",
    "logistics coordination", "transportation management", "fleet operations",
    "customer service", "call center", "helpdesk", "technical support",
    "field service", "service delivery", "installation", "maintenance",
    "repair", "troubleshooting", "quality inspection",
    "social work", "case management", "counseling", "behavioral health",
    "nonprofit management", "grant management", "fundraising", "donor relations",
    "public relations", "media relations", "press releases", "crisis communication",
    "content creation", "video production", "photography", "graphic design",
    "ux research", "user interviews", "usability testing", "prototyping",
    "instructional design", "e-learning development", "training delivery",
    "data analysis", "business reporting", "kpi tracking", "dashboard creation",
    "market analysis", "competitive intelligence", "pricing analysis",
    # ── Common Certifications & Credentials ────────────────────────────────────
    "pmp", "certified associate in project management", "capm",
    "six sigma green belt", "six sigma black belt", "lean six sigma",
    "certified public accountant", "cpa", "chartered financial analyst", "cfa",
    "certified internal auditor", "cia", "certified management accountant", "cma",
    "shrm certified professional", "shrm-cp", "shrm senior certified professional", "shrm-scp",
    "professional in human resources", "phr", "senior professional in human resources", "sphr",
    "certified compensation professional", "ccp", "global remuneration professional", "grp",
    "certified talent acquisition specialist", "ctas", "certified professional recruiter", "cpr",
    "aws certified solutions architect", "aws certified developer",
    "google professional data engineer", "microsoft certified azure administrator",
    "certified scrum master", "csm", "safe certified", "pmi acp",
    "itil foundation", "itil 4", "cobit", "iso 27001",
    "certified information systems auditor", "cisa",
    "certified information security manager", "cism",
    "certified ethical hacker", "ceh", "comptia security+", "comptia a+",
    "google ads certification", "hubspot inbound certification",
    "meta certified digital marketing associate",
    "salesforce certified administrator", "salesforce certified platform developer",
    "sap certified application associate", "oracle certified professional",
]

SKILL_ALIASES: Dict[str, List[str]] = {
    # Languages
    "javascript":              ["js", "ecmascript", "es6", "es2015", "es2020", "es2022"],
    "typescript":              ["ts"],
    "python":                  ["py", "python3", "python 3"],
    "golang":                  ["go", "go lang", "go language"],
    "c++":                     ["cpp", "c plus plus", "cplusplus"],
    "c#":                      ["csharp", "c sharp", ".net c#", "dotnet c#"],
    "kotlin":                  ["kotlin jvm"],
    "ruby":                    ["rb", "ruby on rails"],
    # Web frameworks
    "react":                   ["reactjs", "react.js", "react js"],
    "vue.js":                  ["vue", "vuejs", "vue 3", "vue3"],
    "angular":                 ["angularjs", "angular 2+", "angular js", "angular.js"],
    "next.js":                 ["nextjs", "next js", "nextjs 13", "nextjs 14"],
    "nuxt.js":                 ["nuxt", "nuxtjs"],
    "node.js":                 ["node", "nodejs", "express", "express.js"],
    "fastapi":                 ["fast api"],
    "django":                  ["django rest framework", "drf", "django 4"],
    "spring boot":             ["spring", "spring framework", "spring mvc", "spring cloud", "springboot", "spring-boot"],
    "tailwind":                ["tailwind css", "tailwindcss"],
    # Databases
    "postgresql":              ["postgres", "psql", "pg", "postgre"],
    "mongodb":                 ["mongo", "mongo db", "mongoose"],
    "elasticsearch":           ["elastic search", "elk", "opensearch", "elastic"],
    "microsoft sql server":    ["mssql", "sql server", "t-sql", "tsql"],
    "redis":                   ["redis cache", "redis queue"],
    "dynamodb":                ["dynamo", "aws dynamodb"],
    "bigquery":                ["google bigquery", "bq", "big query"],
    "snowflake":               ["snowflake db"],
    # Cloud
    "amazon web services":     ["aws", "amazon aws"],
    "google cloud platform":   ["gcp", "google cloud"],
    "microsoft azure":         ["azure", "az", "azure cloud"],
    # DevOps
    "kubernetes":              ["k8s", "kube", "k8"],
    "docker":                  ["dockerfile", "docker compose", "docker swarm", "container", "containerization", "docker engine"],
    "github actions":          ["gh actions", "github ci", "github workflows"],
    "terraform":               ["tf", "iac", "infrastructure as code", "hcl"],
    "ansible":                 ["ansible playbook"],
    "jenkins":                 ["jenkins ci", "jenkins pipeline", "jenkinsfile"],
    "continuous integration":  ["ci", "ci/cd", "cicd"],
    "nginx":                   ["nginx web server"],
    "prometheus":              ["prometheus monitoring"],
    "grafana":                 ["grafana dashboard"],
    "elk stack":               ["elk", "elastic stack", "elasticsearch kibana logstash"],
    # AI/ML
    "machine learning":        ["ml", "supervised learning", "unsupervised learning", "classification"],
    "deep learning":           ["dl", "neural networks", "neural network"],
    "natural language processing": ["nlp", "text processing", "language models", "text mining"],
    "pytorch":                 ["torch", "pytorch lightning"],
    "tensorflow":              ["tf", "keras", "tensorflow keras"],
    "scikit-learn":            ["sklearn", "scikit learn"],
    "computer vision":         ["cv", "image processing", "object detection"],
    "large language models":   ["llm", "llms", "foundation models"],
    "hugging face":            ["huggingface", "transformers library"],
    "langchain":               ["lang chain"],
    # Data
    "pandas":                  ["pd", "pandas dataframe"],
    "apache spark":            ["spark", "pyspark", "databricks spark"],
    "apache kafka":            ["kafka", "kafka streams"],
    "apache airflow":          ["airflow"],
    # Embedded
    "freertos":                ["free rtos", "free-rtos"],
    "embedded linux":          ["buildroot", "yocto"],
    "can bus":                 ["can", "canopen", "j1939"],
    "iso 26262":               ["iso26262", "functional safety automotive"],
    "firmware":                ["bare metal"],
    # Mobile
    "react native":            ["rn", "react-native"],
    "flutter":                 ["flutter sdk", "dart flutter"],
    "swiftui":                 ["swift ui"],
    "jetpack compose":         ["compose", "android compose"],
    # Testing
    "end to end testing":      ["e2e", "e2e testing"],
    "test driven development": ["tdd"],
    "behavior driven development": ["bdd"],
    # Architecture
    "rest api":                ["rest", "restful", "restful api", "http api", "rest service", "rest endpoint"],
    "graphql":                 ["graph ql"],
    "microservices":           ["microservice", "micro services"],
    "domain driven design":    ["ddd"],
    "event-driven":            ["event driven", "event driven architecture", "eda"],
    # Cloud services
    "ec2":                     ["aws ec2", "amazon ec2"],
    "s3":                      ["aws s3", "amazon s3"],
    "lambda":                  ["aws lambda", "serverless"],
    "sagemaker":               ["aws sagemaker"],
    # Security
    "oauth2":                  ["oauth", "oauth 2.0"],
    "jwt":                     ["json web token", "json web tokens"],
    # Tools
    "git":                     ["version control", "vcs"],
    "github":                  ["github.com"],
    "jira":                    ["atlassian jira"],
    "power bi":                ["powerbi", "ms power bi", "microsoft power bi"],
    "tableau":                 ["tableau desktop", "tableau server"],
    "figma":                   ["figma design"],
    "ci/cd":                   ["cicd", "continuous delivery", "continuous deployment", "continuous integration"],
    # Salesforce
    "apex":                    ["apexcode", "apex code", "salesforce apex", "sfdc apex", "apex trigger", "apex class"],
    "soap":                    ["webservices", "web services", "soap api", "wsdl", "soap web service"],
    "visualforce":             ["vf", "visual force", "vf page"],
    "salesforce":              ["sfdc", "sf.com", "salesforce.com", "salesforce crm"],
    "lightning":               ["lwc", "lightning web component", "aura", "aura component"],
    # Soft skills
    "mentoring":               ["mentor", "mentored", "coaching", "coached", "coach"],
    "communication":           ["communicate", "communicating", "communicator", "communication skills"],
    "stakeholder management":  ["managing stakeholders", "stakeholder engagement", "stakeholder relations"],
    "leadership":              ["lead teams", "led teams", "team lead", "people management", "team management"],

    # GCP
    "gcp":                     ["google cloud", "google cloud platform"],
    "cloud functions":         ["gcf", "google cloud functions"],
    "pubsub":                  ["pub/sub", "google pub/sub"],

    # Azure
    "azure":                   ["microsoft azure", "ms azure"],
    "azure functions":         ["az functions"],
    "cosmos db":               ["cosmosdb", "azure cosmos"],
    "azure devops":            ["ado", "azure devops services"],

    # ServiceNow
    "servicenow":              ["snow", "service now", "service-now"],

    # SAP
    "sap":                     ["systems applications and products"],
    "sap mm":                  ["sap materials management", "materials management"],
    "sap s/4hana":             ["sap s4hana", "sap s/4 hana", "s4hana", "s/4hana"],
    "sap mm configuration":    ["mm configuration", "sap mm customizing"],
    "sap sd":                  ["sales and distribution", "sap sales and distribution", "sd integration", "sd"],
    "sap pp":                  ["production planning", "sap production planning", "pp integration"],
    "sap fi":                  ["financial accounting", "sap financial accounting", "fi integration", "cross-module integration (fi"],
    "sap wm":                  ["warehouse management", "sap warehouse management", "wm integration"],
    "procure-to-pay":          ["p2p", "procure to pay", "purchase to pay", "purchase-to-pay"],
    "purchase requisition":    ["pr", "purchase req"],
    "purchase order":          ["po", "purchase orders"],
    "goods receipt":           ["gr", "goods receipts"],
    "inventory management":    ["inventory control", "inventory tracking"],
    "invoice verification":    ["vendor invoice verification", "invoice processing"],
    "material master data":    ["material master", "material masters"],
    "vendor master data":      ["vendor master"],
    "idoc":                    ["intermediate document"],
    "subcontracting":          ["subcontract"],
    "consignment":             ["consignment process"],
    "stock transfer order":    ["sto", "stock transfer"],
    "release strategy":        ["approval strategy", "release procedure"],
    "request for quotation":   ["rfq"],
    "batch management":        ["batches"],
    "solution manager":        ["sap solution manager"],

    # Recruiting & HR tools
    "greenhouse":              ["greenhouse ats", "greenhouse recruiting"],
    "lever":                   ["lever ats", "lever recruiting"],
    "icims":                   ["icims ats"],
    "jobvite":                 ["jobvite ats"],
    "smartrecruiters":         ["smartrecruiters ats"],
    "taleo":                   ["oracle taleo", "taleo ats"],
    "recruitee":               ["recruitee ats"],
    "jazzhr":                  ["jazz hr"],
    "bamboohr":                ["bamboo hr"],
    "hi bob":                  ["hibob", "hi bob"],
    "15five":                  ["15 five"],
    "culture amp":             ["cultureamp"],
    "survey monkey":           ["surveymonkey"],
    "qualtrics":               ["qualtrics employee experience"],
    "adp workforce now":       ["adp"],
    "workday recruiting":      ["workday ats"],
    "linkedin recruiter":      ["linkedin recruiting"],
    "naukri":                  ["naukri.com"],
    "wellfound":               ["angellist", "angel list"],
    "phenom":                  ["phenom people"],
    "eightfold":               ["eightfold ai"],
    "beamery":                 ["beamery crm"],

    # Sales & Customer Success
    "hubspot":                 ["hubspot crm"],
    "zoho":                    ["zoho crm"],
    "copper crm":              ["copper"],
    "close crm":               ["close"],
    "salesloft":               ["salesloft cadence"],
    "outreach":                ["outreach.io"],
    "apollo.io":               ["apollo"],
    "gong":                    ["gong.io"],
    "chorus.ai":               ["chorus"],
    "customer success":        ["customer success management", "csm"],
    "account management":      ["account mgmt"],
    "net promoter score":      ["nps"],
    "help desk":               ["helpdesk"],
    "zendesk":                 ["zendesk support"],
    "freshdesk":               ["freshdesk support"],

    # Operations & Admin
    "microsoft office":        ["ms office"],
    "microsoft word":          ["ms word", "word"],
    "microsoft excel":         ["ms excel", "excel"],
    "microsoft powerpoint":    ["ms powerpoint", "powerpoint"],
    "microsoft outlook":       ["outlook"],
    "google workspace":        ["g suite", "gsuite"],
    "google docs":             ["gdocs"],
    "google sheets":           ["gsheets"],
    "google slides":           ["gslides"],
    "monday.com":              ["monday"],
    "clickup":                 ["clickup project management"],
    "airtable":                ["airtable database"],
    "docusign":                ["docu sign"],
    "adobe sign":              ["adobesign"],
    "quickbooks":              ["quickbooks online"],
    "concur":                  ["sap concur"],
    "procurement":             ["sourcing", "purchasing"],
    "customer service":        ["customer support"],
    "call center":             ["contact center"],
    "technical support":       ["tech support"],

    # Certifications
    "pmp":                     ["project management professional"],
    "capm":                    ["certified associate in project management"],
    "cpa":                     ["certified public accountant"],
    "cfa":                     ["chartered financial analyst"],
    "csm":                     ["certified scrum master"],
    "phr":                     ["professional in human resources"],
    "sphr":                    ["senior professional in human resources"],
    "shrm-cp":                 ["shrm certified professional"],
    "shrm-scp":                ["shrm senior certified professional"],
    "cisa":                    ["certified information systems auditor"],
    "cism":                    ["certified information security manager"],
    "ceh":                     ["certified ethical hacker"],

    # Oracle
    "plsql":                   ["pl/sql", "pl sql", "oracle pl/sql"],
    "oracle":                  ["oracle db", "oracle database"],

    # Docker/K8s
    "helm":                    ["helm charts"],

    # Data Engineering
    "etl":                     ["extract transform load", "data pipeline", "data pipelines"],
    "spark":                   ["apache spark", "pyspark"],
    "kafka":                   ["apache kafka", "event streaming"],
    "airflow":                 ["apache airflow"],
    "hadoop":                  ["apache hadoop", "hdfs", "mapreduce"],

    # CI/CD
    "express":                 ["expressjs", "express.js"],

    # Frontend
    "vue":                     ["vuejs", "vue.js"],

    # Soft Skills
    "problem solving":         ["problem-solving", "troubleshooting", "analytical thinking"],
    "teamwork":                ["team player", "collaborative", "team collaboration"],
    "time management":         ["deadline management", "time-management"],
    "project management":      ["program management"],
    "agile":                   ["agile methodology", "scrum", "kanban"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# CERTIFICATION SKILL INFERENCE MAP
# ═══════════════════════════════════════════════════════════════════════════════

CERTIFICATION_SKILL_MAP = {
    # Salesforce
    "dev-401": ["apex", "visualforce", "soql"],
    "dev-501": ["apex", "visualforce", "soql"],
    "dev501": ["apex", "visualforce", "soql"],
    "adm-201": ["salesforce admin", "salesforce"],
    "adm201": ["salesforce admin", "salesforce"],
    "salesforce certified developer": ["apex", "visualforce", "soql", "salesforce"],
    "salesforce certified administrator": ["salesforce admin", "salesforce"],
    "salesforce certified sales cloud": ["salesforce", "sales cloud"],
    "salesforce certified service cloud": ["salesforce", "service cloud"],
    "salesforce certified platform builder": ["salesforce", "lightning", "apex"],
    "sfdc certified": ["salesforce", "apex"],
    # AWS
    "aws solutions architect": ["aws", "ec2", "s3", "vpc", "cloudformation", "lambda"],
    "aws developer associate": ["aws", "lambda", "dynamodb", "api gateway", "s3"],
    "aws sysops": ["aws", "ec2", "cloudwatch", "iam"],
    "aws devops engineer": ["aws", "codepipeline", "cloudformation", "ecs"],
    "aws certified": ["aws"],
    # Azure
    "az-900": ["azure"],
    "az-204": ["azure", "azure functions", "cosmos db", "app service"],
    "az-305": ["azure", "azure architecture"],
    "azure solutions architect": ["azure", "azure architecture"],
    "azure certified": ["azure"],
    # GCP
    "google cloud professional": ["gcp", "bigquery", "cloud functions"],
    "gcp associate cloud engineer": ["gcp", "compute engine", "kubernetes"],
    "google cloud certified": ["gcp"],
    # Project Management
    "pmp": ["project management"],
    "prince2": ["project management"],
    "csm": ["scrum", "agile"],
    "certified scrum master": ["scrum", "agile"],
    "safe agilist": ["agile", "safe", "scaled agile"],
    "pmi-acp": ["agile", "project management"],
    # Data
    "databricks certified": ["databricks", "spark", "data engineering"],
    "snowflake certified": ["snowflake", "sql", "data warehousing"],
    # Security
    "cissp": ["information security", "cybersecurity"],
    "ceh": ["ethical hacking", "penetration testing"],
    "comptia security+": ["cybersecurity", "network security"],
    # Java/Oracle
    "ocjp": ["java"],
    "oracle certified java": ["java"],
    "oracle certified professional": ["oracle", "plsql"],
    # Microsoft
    "mcse": ["windows server", "active directory"],
    "mcsa": ["windows server"],
    # Kubernetes/DevOps
    "cka": ["kubernetes"],
    "ckad": ["kubernetes"],
    "docker certified associate": ["docker", "containers"],
    # ITIL
    "itil": ["it service management", "itsm"],
    "itil certified": ["it service management", "itsm"],
    # Finance & Accounting
    "cpa": ["accounting", "audit", "financial reporting", "gaap"],
    "certified public accountant": ["accounting", "audit", "financial reporting", "gaap"],
    "cfa": ["financial analysis", "investment", "valuation", "portfolio management"],
    "chartered financial analyst": ["financial analysis", "investment", "valuation"],
    "frm": ["financial risk management", "risk assessment", "quantitative analysis"],
    "financial risk manager": ["financial risk management", "risk assessment"],
    "acca": ["accounting", "ifrs", "financial reporting", "audit"],
    "chartered accountant": ["accounting", "audit", "financial reporting", "tax"],
    "ca ": ["accounting", "audit", "financial reporting", "tax"],
    "cima": ["management accounting", "cost accounting", "financial planning"],
    "cma": ["management accounting", "cost accounting", "financial planning"],
    "certified management accountant": ["management accounting", "cost accounting"],
    "ea ": ["tax", "tax preparation", "irs"],
    "enrolled agent": ["tax", "tax preparation"],
    "series 7": ["securities", "investment", "financial services"],
    "series 63": ["securities", "investment"],
    "series 65": ["investment advisory", "financial planning"],
    # Healthcare & Medical
    "usmle": ["medical", "clinical assessment", "patient care"],
    "mrcp": ["medical", "clinical assessment", "patient care"],
    "nclex": ["nursing", "patient care", "medication administration"],
    "nclex-rn": ["nursing", "patient care", "medication administration"],
    "rn license": ["nursing", "patient care"],
    "registered nurse": ["nursing", "patient care"],
    "bls": ["basic life support", "patient care", "emergency medicine"],
    "acls": ["advanced cardiac life support", "emergency medicine", "critical care"],
    "pals": ["pediatric advanced life support", "pediatric care", "emergency medicine"],
    "cpr certified": ["cpr", "patient safety", "emergency medicine"],
    "ccrn": ["critical care nursing", "critical care", "patient care"],
    "cen": ["emergency nursing", "emergency medicine"],
    "phr": ["hr", "human resources", "employee relations"],
    "sphr": ["hr", "human resources", "strategic hr"],
    "shrm-cp": ["human resources", "hr management"],
    "shrm-scp": ["human resources", "strategic hr management"],
    # Legal
    "bar exam": ["law", "legal research", "litigation"],
    "bar admission": ["law", "legal research", "litigation"],
    "passed the bar": ["law", "legal research", "litigation"],
    "patent bar": ["patent law", "ip law"],
    "cle": ["continuing legal education", "law"],
    # Education
    "teaching license": ["teaching", "education", "classroom management"],
    "teaching certificate": ["teaching", "education"],
    "praxis": ["teaching", "education", "pedagogy"],
    "google certified educator": ["educational technology", "google classroom", "teaching"],
    "montessori certified": ["early childhood education", "montessori", "pedagogy"],
    # Manufacturing & Quality
    "six sigma green belt": ["six sigma", "quality control", "process improvement"],
    "six sigma black belt": ["six sigma", "quality control", "process improvement", "statistical process control"],
    "lean six sigma": ["lean manufacturing", "six sigma", "process improvement"],
    "iso 9001 lead auditor": ["iso 9001", "quality management", "audit"],
    "iso 14001 lead auditor": ["iso 14001", "environmental management", "audit"],
    "osh certified": ["osha", "safety compliance", "workplace safety"],
    # Construction
    "pmp": ["project management", "construction management"],
    "prince2": ["project management"],
    "lei certified": ["leed certification", "sustainable construction", "green building"],
    "leed ap": ["leed certification", "sustainable construction", "green building"],
    "pe license": ["professional engineer", "engineering", "civil engineering"],
    "professional engineer": ["engineering", "civil engineering", "structural engineering"],
    # Marketing
    "google ads certified": ["google ads", "ppc", "sem"],
    "google analytics certified": ["google analytics", "marketing analytics"],
    "hubspot certified": ["hubspot", "marketing automation", "crm"],
    "facebook blueprint": ["facebook ads", "social media marketing"],
    "omca": ["digital marketing", "seo", "sem"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# SHORT SKILL SPECIAL PATTERNS
# For high-collision single-letter skills (R, C, Go, C#, C++)
# ═══════════════════════════════════════════════════════════════════════════════

SHORT_SKILL_PATTERNS = {
    "r": r'(?i)\b(?:R\s+programming|R\s+language|R\s+studio|RStudio|(?:programming|develop(?:ment|er)|statistical\s+computing|data\s+analysis|machine\s+learning)\s+(?:in|with|using)\s+R\b|\bR\b(?=\s+(?:programming|language|package|library|script|studio|markdown|shiny)))',
    "c": r'(?i)\b(?:C\s+programming|C\s+language|ANSI\s+C|C/C\+\+|programming\s+in\s+C\b|(?:written|developed|coded)\s+in\s+C\b)',
    "c++": r'(?i)\bC\+\+\b',
    "c#": r'(?i)(?:\bC#\b|C\s*Sharp|\.NET\s+C#)',
    "go": r'(?i)\b(?:Golang|Go\s+programming|Go\s+language|Go\s+developer|written\s+in\s+Go|develop(?:ed|ment)\s+in\s+Go)\b',
    "rust": r'(?i)\bRust\b(?=\s+(?:programming|language|developer|engineer|code|project|application))',
    "swift": r'(?i)\bSwift\b(?=\s+(?:programming|language|developer|UI|iOS|app))',
    "dart": r'(?i)\bDart\b(?=\s+(?:programming|language|Flutter|developer))',
    "sas": r'(?i)\bSAS\b(?=\s+(?:programming|language|analytics|statistical|enterprise))',
}


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXTUAL SKILL INDICATORS
# Implied skills detected via surrounding context phrases
# ═══════════════════════════════════════════════════════════════════════════════

CONTEXTUAL_SKILL_INDICATORS = {
    "rest api": ["api integration", "api development", "api design", "restful service", "http endpoint", "api endpoint", "web api"],
    "sql": ["database queries", "stored procedures", "database design", "relational database", "query optimization"],
    "git": ["version control", "source control", "code repository", "pull request", "code review"],
    "ci/cd": ["continuous integration", "continuous deployment", "deployment pipeline", "automated deployment", "build pipeline"],
    "agile": ["sprint planning", "daily standup", "backlog grooming", "retrospective", "scrum ceremony", "user stories"],
    "microservices": ["service-oriented architecture", "soa", "distributed architecture", "service mesh", "api gateway"],
    "cloud computing": ["cloud infrastructure", "cloud migration", "cloud-native", "cloud architect"],
    "devops": ["infrastructure automation", "deployment automation", "site reliability", "sre"],
    "data engineering": ["data pipeline", "etl process", "data warehouse", "data lake", "data ingestion"],
    "machine learning": ["predictive model", "model training", "feature engineering", "ml pipeline", "neural network"],
    "project management": ["managed project", "project delivery", "project planning", "project lifecycle", "cross-functional project"],
    "leadership": ["led team", "managed team", "team of", "direct reports", "people management", "reporting to me"],
    "mentoring": ["mentored", "coached", "trained junior", "knowledge transfer", "onboarding new", "guided team"],
    "communication": ["presented to", "stakeholder presentation", "client-facing", "executive summary", "cross-functional collaboration"],
    "stakeholder management": ["stakeholder engagement", "client relationship", "business partner", "executive stakeholder"],
    "problem solving": ["troubleshot", "root cause analysis", "debug", "resolved issue", "diagnosed"],
    # SAP / ERP domain inference
    "procure-to-pay": ["purchase requisition", "purchase order", "goods receipt", "vendor invoice", "p2p cycle"],
    "p2p": ["purchase requisition", "purchase order", "goods receipt", "vendor invoice", "p2p cycle"],
    "inventory management": ["inventory", "goods receipt", "goods issue", "stock transfer", "warehouse"],
    "invoice verification": ["vendor invoice", "invoice verification", "invoice processing", "three-way match"],
    "material master data": ["material master", "material master data"],
    "vendor master data": ["vendor master", "vendor master data", "vendor invoices"],
    "sap sd": ["sd integration", "sales and distribution", "sap sd", "sd"],
    "sap fi": ["fi integration", "financial accounting", "cross-module integration (fi", "fi, sd"],
    "sap pp": ["production planning", "pp integration"],
    "sap wm": ["warehouse management", "wm integration"],
    "sap s/4hana": ["sap s/4hana", "sap s4hana", "s/4hana implementation", "s4hana"],
    "idoc": ["idoc", "intermediate document"],
    # Recruiting / sales / ops inference
    "customer success": ["customer onboarding", "churn reduction", "customer health", "renewals", "upsell", "cross-sell"],
    "account management": ["account mgmt", "renewals management", "client relationship", "key accounts"],
    "operations management": ["business operations", "process optimization", "operational excellence", "day-to-day operations"],
    "business analysis": ["requirements gathering", "gap analysis", "business requirements", "process mapping"],
    "project management": ["project delivery", "project planning", "project lifecycle", "project coordination"],
    "microsoft office": ["microsoft word", "microsoft excel", "microsoft powerpoint", "microsoft outlook", "ms office"],
    "google workspace": ["google docs", "google sheets", "google slides", "google drive", "g suite"],
    "procurement": ["purchase requisition", "purchase order", "vendor management", "sourcing"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN-CLUSTERED SKILL TAXONOMY
# SKILL_TAXONOMY is the authoritative source for skill domain classification.
# MASTER_SKILLS is kept for backward compatibility (FlashText processor, DB seeding).
# ═══════════════════════════════════════════════════════════════════════════════

SKILL_TAXONOMY = {
    "programming_languages": {
        "core_imperative": [
            "python", "java", "c++", "c#", "c", "golang", "go", "rust", "kotlin",
            "swift", "ruby", "php", "perl", "ada", "cobol", "fortran", "vba",
            "pascal", "assembly", "nim", "zig", "crystal", "dart"
        ],
        "functional": [
            "haskell", "erlang", "elixir", "clojure", "f#", "lisp", "scheme",
            "ocaml", "reasonml", "purescript", "elm", "scala"
        ],
        "scripting": [
            "bash", "powershell", "groovy", "lua", "perl"
        ],
        "specialized": [
            "r", "matlab", "julia", "sas", "spss", "minitab", "stata"
        ],
        "blockchain": [
            "solidity", "vyper", "move", "cairo"
        ]
    },
    "web_frontend": {
        "frameworks": [
            "react", "vue.js", "vue", "angular", "next.js", "nuxt.js", "svelte",
            "astro", "remix", "gatsby", "ember.js", "backbone.js", "qwik", "solid.js"
        ],
        "libraries": ["jquery", "lit", "stencil", "preact"],
        "css_frameworks": ["tailwind", "bootstrap", "material ui", "chakra ui", "ant design"],
        "tools": ["storybook", "webpack", "vite", "parcel", "rollup"],
        "markup": ["html", "xml", "htmx", "alpine.js"]
    },
    "web_backend": {
        "nodejs": ["node.js", "express.js", "nestjs", "koa", "hapi", "feathers", "strapi"],
        "python": ["fastapi", "django", "flask", "tornado", "aiohttp", "starlette", "litestar"],
        "java": ["spring boot", "spring", "quarkus", "micronaut"],
        "golang": ["gin", "fiber", "echo", "chi"],
        "rust": ["actix", "axum", "rocket", "warp"],
        "ruby": ["rails", "sinatra"],
        "elixir": ["phoenix"],
        "php": ["laravel", "symfony", "codeigniter"],
        "dotnet": ["asp.net", "asp.net core", "dotnet", ".net", "blazor", "wpf", "winforms"]
    },
    "databases": {
        "relational": [
            "postgresql", "mysql", "sqlite", "mariadb", "oracle", "microsoft sql server",
            "db2", "informix", "sybase", "teradata", "greenplum"
        ],
        "nosql_document": ["mongodb", "couchdb", "firebase", "firestore", "realm", "fauna"],
        "nosql_kv": ["redis", "memcached"],
        "search_timeseries": ["elasticsearch", "cassandra", "influxdb", "timescaledb", "clickhouse"],
        "graph_specialized": ["neo4j", "dynamodb"],
        "cloud_managed": ["snowflake", "bigquery", "redshift", "databricks", "supabase", "planetscale"],
        "orms": [
            "sqlalchemy", "hibernate", "prisma", "typeorm", "sequelize", "drizzle",
            "mongoose", "entity framework", "dapper"
        ]
    },
    "cloud_platforms": {
        "hyperscalers": ["amazon web services", "aws", "google cloud platform", "gcp", "microsoft azure", "azure"],
        "regional": ["digital ocean", "linode", "vultr", "hetzner", "oracle cloud", "ibm cloud", "alibaba cloud"],
        "deployment_platforms": ["vercel", "netlify", "heroku", "fly.io", "render", "railway"],
        "backend_as_service": ["supabase", "firebase", "appwrite", "pocketbase"],
        "orchestration": ["cloudfoundry", "openshift", "rancher", "nomad"]
    },
    "aws_services": {
        "compute": ["ec2", "lambda", "ecs", "eks", "elastic beanstalk"],
        "storage": ["s3", "ebs", "efs"],
        "database": ["rds", "aurora", "dynamodb"],
        "networking": ["vpc", "api gateway", "cloudfront", "route53"],
        "messaging": ["sqs", "sns", "kinesis", "eventbridge"],
        "analytics": ["glue", "athena", "emr", "sagemaker"],
        "management": ["cloudformation", "iam", "cloudwatch", "step functions"]
    },
    "devops_infrastructure": {
        "containers": ["docker", "docker swarm"],
        "orchestration": ["kubernetes", "k8s", "helm", "kustomize"],
        "iac": ["terraform", "ansible", "puppet", "chef", "vagrant", "packer", "pulumi"],
        "ci_cd": [
            "jenkins", "github actions", "gitlab ci", "circleci", "travis ci",
            "bitbucket pipelines", "argo cd", "flux", "spinnaker"
        ],
        "monitoring": [
            "prometheus", "grafana", "datadog", "new relic", "dynatrace", "splunk",
            "elk stack", "loki", "jaeger", "zipkin", "opentelemetry"
        ],
        "networking_proxy": ["nginx", "apache", "traefik", "envoy"],
        "secrets": ["vault", "consul"]
    },
    "embedded_systems": {
        "rtos_os": ["embedded", "rtos", "freertos", "zephyr", "vxworks", "qnx", "embedded linux", "baremetal"],
        "hardware": ["microcontroller", "fpga", "arm", "arm cortex", "avr", "pic"],
        "protocols": ["uart", "spi", "i2c", "can bus", "modbus", "mqtt", "coap", "ble", "zigbee", "lorawan"],
        "tools": ["openocd", "jtag", "gdb"],
        "standards": ["iso 26262", "misra", "do-178", "functional safety"],
        "firmware": ["firmware", "bsp", "bootloader", "device driver"],
        "industrial": ["plc", "ladder logic", "scada", "industrial automation"]
    },
    "mobile": {
        "ios": ["ios", "swift", "swiftui", "objective-c", "xcode"],
        "android": ["android", "kotlin", "jetpack compose", "android studio"],
        "crossplatform": ["react native", "flutter", "xamarin", "ionic", "capacitor", "cordova"]
    },
    "ai_ml": {
        "core": [
            "machine learning", "deep learning", "neural networks", "reinforcement learning",
            "generative ai", "computer vision", "natural language processing", "nlp"
        ],
        "frameworks": ["pytorch", "tensorflow", "keras", "jax", "scikit-learn", "xgboost", "lightgbm"],
        "llm": ["transformers", "hugging face", "langchain", "llamaindex", "rag", "fine-tuning", "ollama", "openai"],
        "tools": ["mlflow", "wandb", "kubeflow", "vertex ai"],
        "vision": ["opencv", "yolo", "stable diffusion"]
    },
    "data_engineering": {
        "processing": ["apache spark", "spark", "pyspark", "hadoop", "hive", "flink"],
        "messaging": ["apache kafka", "kafka", "rabbitmq"],
        "orchestration": ["apache airflow", "airflow", "prefect", "dagster"],
        "transformation": ["dbt", "fivetran", "airbyte"],
        "concepts": ["etl", "data pipeline", "data warehousing", "data lake", "data mesh", "data governance"]
    },
    "data_science_analytics": {
        "core_libraries": ["pandas", "numpy", "scipy", "polars"],
        "visualization": ["matplotlib", "seaborn", "plotly"],
        "bi": ["tableau", "power bi", "looker", "metabase"],
        "tools": ["jupyter", "excel", "google analytics"]
    },
    "architecture_design": {
        "patterns": [
            "microservices", "event-driven", "cqrs", "event sourcing", "saga pattern",
            "domain driven design", "ddd", "clean architecture"
        ],
        "api": ["rest api", "graphql", "grpc", "websocket", "openapi", "swagger"]
    },
    "security": {
        "auth": ["oauth2", "jwt", "saml", "ldap", "rbac"],
        "crypto": ["tls", "ssl", "cryptography"],
        "compliance": ["soc2", "gdpr", "hipaa", "pci dss"],
        "practices": ["penetration testing", "owasp", "zero trust"]
    },
    "testing": {
        "types": ["unit testing", "integration testing", "e2e testing", "tdd", "bdd"],
        "frameworks": ["pytest", "jest", "vitest", "mocha", "cypress", "playwright", "selenium"],
        "performance": ["k6", "locust", "jmeter"],
        "coverage": ["sonarqube", "codecov"]
    },
    "blockchain": {
        "platforms": ["ethereum", "bitcoin", "solana", "cardano", "polkadot"],
        "concepts": ["blockchain", "web3", "smart contracts", "defi", "nft"],
        "tools": ["hyperledger", "chainlink", "ipfs"]
    },
    "gaming_graphics": {
        "engines": ["unity", "unreal engine", "godot"],
        "web": ["three.js", "babylon.js", "webgl", "webgpu"]
    },
    "networking": {
        "protocols": ["tcp/ip", "udp", "http", "dns", "vpn"],
        "infrastructure": ["load balancing", "cdn", "reverse proxy", "firewall"]
    },
    "project_management": {
        "methodologies": ["agile", "scrum", "kanban", "safe", "lean"],
        "tools": ["jira", "confluence", "linear", "asana", "trello", "notion"],
        "vcs": ["git", "github", "gitlab", "bitbucket"]
    },
    "business_erp": {
        "crm": ["salesforce", "apex", "visualforce", "lightning", "soql", "soap", "lwc", "hubspot", "zoho", "dynamics 365", "pipedrive"],
        "erp": ["sap", "sap mm", "sap s/4hana", "sap sd", "sap pp", "sap fi", "sap wm",
                "sap ariba", "sap srm", "sap ewm", "solution manager",
                "oracle erp", "dynamics 365", "netsuite", "workday"],
        "procurement": ["procure-to-pay", "p2p", "purchase requisition", "purchase order",
                          "goods receipt", "invoice verification", "request for quotation",
                          "release strategy", "subcontracting", "consignment", "stock transfer order"],
        "master_data": ["material master data", "vendor master data", "batch management"],
        "integration": ["idoc", "cross-module integration"],
        "service": ["servicenow", "freshdesk", "zendesk"],
        "low_code": ["power platform", "power apps", "outsystems", "mendix"]
    },
    "design_ux": {
        "tools": ["figma", "sketch", "adobe xd"],
        "concepts": ["ui/ux", "wireframing", "prototyping", "accessibility", "responsive design"]
    },
    "soft_skills": {
        "communication": ["communication", "technical writing", "public speaking"],
        "leadership": ["leadership", "mentoring", "coaching"],
        "analytical": ["problem solving", "critical thinking", "analytical thinking"]
    },
    "healthcare_medical": {
        "clinical": ["patient care", "clinical assessment", "medical terminology", "treatment planning",
                     "medication administration", "vital signs", "patient safety", "care coordination"],
        "records": ["electronic health records", "ehr", "emr", "epic", "cerner", "medical coding",
                    "icd-10", "cpt", "health informatics"],
        "specialties": ["nursing", "pharmacy", "radiology", "laboratory", "diagnostic",
                        "pathology", "histology", "hematology", "microbiology",
                        "anesthesia", "emergency medicine", "critical care", "pediatric care",
                        "geriatric care", "mental health", "occupational therapy", "physical therapy"],
        "research": ["medical research", "clinical trial", "epidemiology", "public health",
                     "anatomy", "physiology", "pharmacology", "biochemistry"],
        "technology": ["telemedicine", "medical device", "medical imaging", "ultrasound",
                       "mri", "ct scan", "x-ray", "surgical technology"],
    },
    "finance_accounting": {
        "core": ["financial analysis", "accounting", "gaap", "ifrs", "financial reporting",
                 "reconciliation", "general ledger", "accounts payable", "accounts receivable",
                 "financial modeling", "budgeting", "forecasting"],
        "audit": ["audit", "internal audit", "financial audit", "sox", "internal controls",
                  "forensic accounting"],
        "tax": ["tax", "tax preparation", "corporate tax", "indirect tax", "vat", "gst",
                "transfer pricing", "customs duty"],
        "tools": ["quickbooks", "sap fi", "oracle financials", "excel"],
        "advanced": ["valuation", "dcf", "npv", "irr", "cost accounting", "variance analysis",
                     "capital budgeting", "fp&a", "financial planning analysis"],
    },
    "legal": {
        "core": ["legal research", "legal writing", "contract review", "contract drafting",
                 "legal analysis", "legal counsel", "litigation"],
        "specialties": ["corporate law", "ip law", "patent law", "employment law", "labor law",
                        "real estate law", "criminal law", "civil litigation", "privacy law",
                        "data protection", "competition law", "antitrust", "securities law",
                        "bankruptcy", "immigration law", "family law", "environmental law"],
        "tools": ["westlaw", "lexisnexis"],
        "concepts": ["due diligence", "regulatory compliance", "arbitration", "mediation",
                     "discovery", "deposition", "evidence", "nda", "legal hold",
                     "bar admission", "mergers acquisitions"],
    },
    "marketing_domain": {
        "digital": ["digital marketing", "seo", "sem", "ppc", "google ads", "facebook ads",
                    "social media marketing", "email marketing"],
        "content": ["content marketing", "content strategy", "copywriting", "blogging"],
        "strategy": ["marketing strategy", "brand management", "campaign management",
                     "go-to-market", "customer segmentation", "market research",
                     "competitive analysis", "marketing funnel", "demand generation"],
        "tools": ["google analytics", "hubspot", "marketo", "pardot", "mailchimp",
                  "hootsuite", "buffer"],
        "analytics": ["marketing analytics", "marketing roi", "conversion optimization",
                      "ab testing", "conversion rate optimization"],
    },
    "hr_domain": {
        "core": ["recruitment", "talent acquisition", "onboarding", "employee relations",
                 "performance management", "compensation", "benefits administration"],
        "development": ["training development", "l&d", "employee engagement",
                        "diversity inclusion", "organizational development"],
        "tools": ["hris", "workday", "successfactors", "bamboo hr", "applicant tracking system", "ats",
                  "greenhouse", "lever", "icims", "jobvite", "smartrecruiters", "taleo", "recruitee",
                  "jazzhr", "linkedin recruiter", "indeed", "monster", "naukri", "glassdoor",
                  "ziprecruiter", "careerbuilder", "wellfound", "hireez", "gem", "seekout",
                  "hiretual", "entelo", "pymetrics", "paradox", "mya", "xor", "textio", "phenom",
                  "eightfold", "beamery", "adp workforce now", "paylocity", "paychex", "gusto",
                  "deel", "rippling", "bamboohr", "namely", "zenefits", "factorial", "hi bob",
                  "lattice", "15five", "culture amp", "glint", "qualtrics", "survey monkey"],
        "compliance": ["labor law compliance", "policy development", "grievance handling"],
        "strategy": ["workforce planning", "succession planning", "hr analytics",
                     "competency mapping", "360 feedback", "performance appraisal"],
    },
    "sales_customer_success": {
        "crm": ["salesforce", "hubspot", "zoho", "pipedrive",
                 "freshsales", "insightly", "copper crm", "nimble", "close crm"],
        "engagement": ["salesloft", "outreach", "apollo.io", "zoominfo", "lusha",
                        "seamless.ai", "clearbit", "linkedin sales navigator"],
        "conversation_intelligence": ["gong", "chorus.ai", "execvision", "clari",
                                         "revenue grid", "prolifiq"],
        "cs": ["customer success", "customer onboarding", "account management",
                "renewals management", "upsell", "cross-sell", "churn reduction",
                "net promoter score", "nps", "customer health scoring"],
        "support": ["support ticket management", "help desk", "live chat", "chatbot",
                    "intercom", "zendesk", "freshdesk", "kustomer", "crisp"],
    },
    "operations_admin": {
        "office": ["operations management", "business operations", "office management",
                    "executive assistance", "virtual assistance", "administrative support",
                    "calendar management", "travel coordination", "expense reporting"],
        "productivity": ["microsoft office", "ms office", "microsoft word", "microsoft excel",
                         "microsoft powerpoint", "microsoft outlook", "microsoft sharepoint",
                         "google workspace", "google docs", "google sheets", "google slides",
                         "google forms", "google drive", "google calendar"],
        "collaboration": ["slack", "microsoft teams", "zoom", "webex", "gotomeeting"],
        "project_tools": ["asana", "monday.com", "clickup", "notion", "trello", "smartsheet",
                           "wrike", "basecamp", "teamwork", "proofhub", "airtable",
                           "confluence", "sharepoint"],
        "documents": ["onedrive", "dropbox", "box", "docusign", "adobe sign", "hellosign",
                       "pandadoc", "signnow"],
        "finance_ops": ["quickbooks", "xero", "freshbooks", "wave accounting", "zoho books",
                          "expensify", "concur", "tripactions", "navan", "brex", "ramp"],
        "procurement": ["procurement", "purchasing", "inventory control", "asset management",
                         "vendor coordination", "facilities management"],
    },
    "education_domain": {
        "core": ["curriculum development", "lesson planning", "pedagogy", "assessment",
                 "classroom management", "teaching", "tutoring", "student engagement"],
        "specialized": ["differentiated instruction", "special education", "iep",
                        "stem education", "early childhood education", "higher education"],
        "technology": ["educational technology", "e-learning", "instructional design",
                       "learning management system", "lms", "canvas", "blackboard",
                       "moodle", "google classroom", "schoology", "edtech"],
        "research": ["academic research", "grant writing", "rubric design",
                     "formative assessment", "summative assessment", "curriculum mapping"],
    },
    "manufacturing_operations": {
        "methodology": ["lean manufacturing", "six sigma", "lean six sigma", "kaizen", "5s",
                        "poka-yoke", "continuous improvement", "total productive maintenance"],
        "quality": ["quality control", "quality assurance", "iso 9001", "iso 14001",
                    "iso 45001", "statistical process control", "spc", "root cause analysis"],
        "production": ["production planning", "capacity planning", "mrp",
                       "manufacturing execution system", "mes", "value stream mapping"],
        "tools": ["cnc", "cad", "cam", "autocad", "solidworks"],
        "safety": ["safety compliance", "osha"],
        "certifications": ["green belt", "black belt"],
    },
    "logistics_supply_chain": {
        "core": ["supply chain management", "logistics", "warehousing", "distribution",
                 "inventory management", "inventory control", "procurement", "vendor management"],
        "transport": ["freight", "shipping", "freight forwarding", "transportation management",
                      "last mile delivery", "route optimization", "fleet management"],
        "international": ["customs clearance", "import export", "incoterms", "hs code",
                          "3pl", "4pl"],
        "systems": ["wms", "tms", "erp"],
        "planning": ["demand forecasting", "demand planning", "s&op",
                     "supply chain optimization"],
    },
    "construction_engineering": {
        "management": ["construction management", "site supervision", "project management",
                       "contract administration", "cost estimation", "quantity surveying"],
        "design": ["autocad", "revit", "bim", "blueprint reading", "primavera", "ms project"],
        "engineering": ["structural engineering", "civil engineering"],
        "trades": ["hvac", "electrical systems", "plumbing", "concrete", "steel structure",
                   "foundation", "demolition", "renovation"],
        "compliance": ["building codes", "safety compliance", "osha", "leed certification"],
    },
    "consulting": {
        "strategy": ["strategy consulting", "management consulting", "business transformation",
                     "digital transformation", "operational excellence"],
        "analysis": ["business analysis", "gap analysis", "business case", "roi analysis",
                     "process mapping", "process improvement"],
        "delivery": ["executive presentation", "workshop facilitation",
                     "requirements gathering", "transformation roadmap"],
    },
    "certifications": {
        "project": ["pmp", "capm", "certified scrum master", "csm", "safe certified", "pmi acp"],
        "quality": ["six sigma green belt", "six sigma black belt", "lean six sigma"],
        "accounting_finance": ["cpa", "certified public accountant", "cfa", "chartered financial analyst",
                                 "cia", "certified internal auditor", "cma", "certified management accountant"],
        "hr": ["phr", "professional in human resources", "sphr", "senior professional in human resources",
               "shrm-cp", "shrm-scp", "ccp", "global remuneration professional", "grp",
               "ctas", "certified professional recruiter"],
        "security": ["cisa", "cism", "ceh", "comptia security+", "comptia a+", "iso 27001"],
        "cloud_tech": ["aws certified solutions architect", "aws certified developer",
                       "google professional data engineer", "microsoft certified azure administrator"],
        "sales_marketing": ["google ads certification", "hubspot inbound certification",
                             "meta certified digital marketing associate",
                             "salesforce certified administrator", "salesforce certified platform developer"],
        "erp": ["sap certified application associate", "oracle certified professional"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_skill(s: str) -> str:
    s = s.strip()
    # Special-case skills that must not be normalized
    if s.lower() in ("c++", "c#"):
        return s.lower()
    s = s.lower()
    s = re.sub(r'[.\-/\\]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _is_token_skill_match(a: str, b: str) -> bool:
    """True if one skill is a whole token inside the other (not Java⊂JavaScript)."""
    if not a or not b or a == b:
        return a == b
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter not in longer:
        return False
    return re.search(rf'(^|[^a-z0-9]){re.escape(shorter)}([^a-z0-9]|$)', longer) is not None


def normalize_skill_name(skill: str) -> str:
    """Normalize a skill name using SKILL_ALIASES.

    Returns the canonical form if an alias exists, otherwise returns
    the original (stripped).  When the input already *is* the canonical
    form (case-insensitive), the original casing is preserved so that
    "Python" stays "Python" rather than becoming "python".

    This is the public API for parser_service and any other consumer
    that needs to collapse alias variants at extraction time.

    Examples
    --------
    >>> normalize_skill_name("nodejs")
    'node.js'
    >>> normalize_skill_name("react.js")
    'react'
    >>> normalize_skill_name("Python")
    'Python'
    """
    if not skill:
        return skill
    stripped = skill.strip()
    lower = stripped.lower()

    # Special-case: preserve c++ / c# casing from SKILL_ALIASES keys
    if lower in ("c++", "c#"):
        for canonical in SKILL_ALIASES:
            if canonical.lower() == lower:
                return canonical
        return stripped

    # Normalize punctuation for lookup (e.g. "react.js" -> "react js")
    norm = _normalize_skill(stripped)

    # Check if the normalised form matches a canonical key
    for canonical, aliases in SKILL_ALIASES.items():
        if norm == _normalize_skill(canonical):
            # Input IS the canonical form (possibly different casing/punctuation).
            # If only casing differs (lowered input == canonical key), preserve
            # original casing — e.g. "Python" stays "Python".
            # If punctuation/spacing differs (e.g. "node js" vs "node.js"),
            # return the canonical form.
            if lower == canonical.lower():
                return stripped
            return canonical
        for alias in aliases:
            if norm == _normalize_skill(alias):
                # Input is an alias — map to canonical form.
                return canonical

    # No alias match — return original stripped form
    return stripped


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-COLLISION SKILLS
# Skills that are common English words — require domain co-occurrence validation
# when extracted from raw text (not from structured skills sections)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_collision_skills():
    """Auto-detect skills appearing in 2+ domains plus known English-word homonyms."""
    multi_domain = set()
    # Find skills that exist in multiple top-level domains
    skill_domains = {}  # norm_skill -> set of domain names
    for domain_name, subcats in SKILL_TAXONOMY.items():
        for subcat_name, skill_list in subcats.items():
            for s in skill_list:
                norm = _normalize_skill(s)
                if norm not in skill_domains:
                    skill_domains[norm] = set()
                skill_domains[norm].add(domain_name)
    
    for norm, domains in skill_domains.items():
        if len(domains) >= 2:
            multi_domain.add(norm)
    
    # Known English-word homonyms that could appear in non-technical context
    ENGLISH_HOMONYMS = {
        "go", "r", "c", "swift", "ruby", "rust", "dart",
        "scala", "spark", "rocket", "phoenix", "julia",
        "nim", "elixir", "erlang", "embedded", "railway",
        "rtos", "communication", "assembly",
    }
    return multi_domain | ENGLISH_HOMONYMS

HIGH_COLLISION_SKILLS = _build_collision_skills()

# Aliases that must NEVER be registered in the flashtext processor for free-text
# scanning because they are common English words that cause massive false positives.
# These aliases are still valid in structured skills sections (regex splitting).
_BANNED_FREETEXT_ALIASES: set = {
    "go",      # "go-to", "going", "ago" → falsely matches golang
    "c",       # "because", "C-level", "cloud" → falsely matches C language
    "r",       # "for", "your", "senior" → falsely matches R language
    "js",      # "json", "jsx" → falsely matches JavaScript (use "javascript")
    "ts",      # "ts" inside words → falsely matches TypeScript
    "py",      # "py" inside words → falsely matches Python
    "rb",      # "ruby" abbreviation too short
    "tf",      # "terraform" abbreviation inside words
}


def _get_skill_domains(skill: str) -> set:
    """Return set of top-level domain names this skill belongs to in SKILL_TAXONOMY."""
    norm = _normalize_skill(skill)
    domains = set()
    for domain_name, subcategories in SKILL_TAXONOMY.items():
        for subcat_skills in subcategories.values():
            if norm in {_normalize_skill(s) for s in subcat_skills}:
                domains.add(domain_name)
    return domains


def _get_skill_subcategory_keys(skill: str) -> set:
    """Return set of (domain, subcategory) tuples for a skill in SKILL_TAXONOMY.

    More granular than _get_skill_domains — used for two-pass validation
    to avoid false positives where a skill shares a top-level domain but
    belongs to a completely different subcategory (e.g. 'aws' in
    cloud_platforms.hyperscalers should not validate 'railway' in
    cloud_platforms.deployment_platforms).
    """
    norm = _normalize_skill(skill)
    keys = set()
    for domain_name, subcategories in SKILL_TAXONOMY.items():
        for subcat_name, skill_list in subcategories.items():
            if norm in {_normalize_skill(s) for s in skill_list}:
                keys.add((domain_name, subcat_name))
    return keys


def _flatten_taxonomy() -> set:
    """Derive flat skill set from SKILL_TAXONOMY for backward compatibility."""
    skills = set()
    for subcategories in SKILL_TAXONOMY.values():
        for skill_list in subcategories.values():
            skills.update(_normalize_skill(s) for s in skill_list)
    return skills


def _expand_skill(skill: str) -> List[str]:
    """Return skill + all its aliases, all normalized."""
    n = _normalize_skill(skill)
    aliases = [_normalize_skill(a) for a in SKILL_ALIASES.get(n, [])]
    # Also check the original unnormalized form
    raw_aliases = SKILL_ALIASES.get(skill.lower(), [])
    aliases += [_normalize_skill(a) for a in raw_aliases]
    return list(dict.fromkeys([n] + aliases))


# ═══════════════════════════════════════════════════════════════════════════════
# SKILLS REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

def _build_domain_skill_map() -> Dict[str, str]:
    """Map each MASTER_SKILL to its primary domain for seeding."""
    from app.backend.services.constants import DOMAIN_KEYWORDS
    skill_to_domain: Dict[str, str] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            skill_to_domain[kw] = domain
    return skill_to_domain


class SkillsRegistry:
    """
    DB-backed skills registry with in-memory flashtext processor.
    Falls back to MASTER_SKILLS if DB is unavailable.
    """

    def __init__(self):
        self._processor = None
        self._skills: List[str] = []
        self._loaded = False

    def _build_processor(self, skills: List[str]):
        """Build flashtext KeywordProcessor from a skills list.

        Filters out _BANNED_FREETEXT_ALIASES to prevent common-English-word
        false positives (e.g. \"go\" in \"go-to\" matching golang).
        """
        try:
            from flashtext import KeywordProcessor
            kp = KeywordProcessor(case_sensitive=False)
            # Filter banned aliases from direct skill list (DB-loaded aliases
            # may include them even if SKILL_ALIASES has been cleaned)
            safe_skills = [s for s in skills if s.lower() not in _BANNED_FREETEXT_ALIASES]
            kp.add_keywords_from_list(safe_skills)
            # Also add all known aliases, except banned short aliases
            for canonical, aliases in SKILL_ALIASES.items():
                for alias in aliases:
                    if alias.lower() in _BANNED_FREETEXT_ALIASES:
                        continue
                    kp.add_keyword(alias, canonical)
            self._processor = kp
            self._skills = safe_skills
        except ImportError:
            self._processor = None
            self._skills = skills

    def seed_if_empty(self, db) -> None:
        """Upsert MASTER_SKILLS into the skills table — safe to call on every startup.

        Uses INSERT … ON CONFLICT (name) DO NOTHING so re-deploys never crash
        on duplicate keys, and new skills added to MASTER_SKILLS are picked up
        automatically without manual DB intervention.
        """
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from app.backend.models.db_models import Skill

            _domain_map = _build_domain_skill_map()
            rows = [
                {
                    "name":    skill,
                    "aliases": ",".join(SKILL_ALIASES.get(skill, [])),
                    "domain":  _domain_map.get(skill, "general"),
                    "status":  "active",
                    "source":  "seed",
                    "frequency": 0,
                }
                for skill in MASTER_SKILLS
            ]
            stmt = pg_insert(Skill).values(rows).on_conflict_do_nothing(index_elements=["name"])
            db.execute(stmt)
            db.commit()
            logger.info("Skills upsert complete (%d definitions)", len(rows))
        except Exception as e:
            db.rollback()
            logger.warning("Skills seed failed (non-fatal): %s", e)

    def load(self, db=None) -> None:
        """Load active skills from DB into the in-memory processor.

        Always uses a fresh query — never reuses a session that may have been
        poisoned by a previous exception.
        """
        skills = []
        if db is not None:
            try:
                # Explicitly begin a clean transaction in case the caller's
                # session was rolled back by seed_if_empty above.
                db.rollback()
                from app.backend.models.db_models import Skill
                rows = db.query(Skill).filter(Skill.status == "active").all()
                if rows:
                    for row in rows:
                        skills.append(row.name)
                        if row.aliases:
                            skills.extend(a.strip() for a in row.aliases.split(",") if a.strip())
            except Exception as e:
                logger.warning("Skills load from DB failed (using hardcoded): %s", e)
        # Always merge with MASTER_SKILLS so code updates to hardcoded skills are
        # reflected even when the DB was seeded earlier with an older version.
        # Aliases are registered separately via SKILL_ALIASES mapping in _build_processor.
        base = list(MASTER_SKILLS)
        if skills:
            merged = list(dict.fromkeys(skills + base))
        else:
            merged = base
        self._build_processor(merged)
        _compiled_skill_patterns.cache_clear()
        self._loaded = True
        logger.info("SkillsRegistry loaded %d skills", len(self._skills))

    def rebuild(self, db=None) -> None:
        """Hot-reload skills without restarting the app."""
        self._loaded = False
        self.load(db)

    def get_processor(self):
        """Return the flashtext processor, lazy-loading if needed."""
        if not self._loaded:
            self.load()
        return self._processor

    def get_all_skills(self) -> List[str]:
        if not self._loaded:
            self.load()
        return self._skills


# Bump this version whenever skill-extraction logic changes (aliases, boundaries,
# MASTER_SKILLS list, etc.).  Old JD-cache entries will be auto-invalidated.
JD_CACHE_VERSION: str = "4"

# Module-level singleton
skills_registry = SkillsRegistry()


@lru_cache(maxsize=16)
def _compiled_skill_patterns(skills_key: Tuple[str, ...]) -> Tuple[Tuple[Pattern[str], str], ...]:
    """Compile fallback skill regexes once per loaded skills registry state."""
    variant_map: Dict[str, str] = {}
    for canonical in skills_key:
        canonical_lower = canonical.lower()
        if canonical_lower not in _BANNED_FREETEXT_ALIASES:
            variant_map[canonical_lower] = canonical_lower
        for alias in SKILL_ALIASES.get(canonical_lower, []):
            alias_lower = alias.lower()
            if alias_lower not in _BANNED_FREETEXT_ALIASES:
                variant_map[alias_lower] = canonical_lower

    for canonical_lower, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower in variant_map and canonical_lower not in variant_map:
                variant_map[canonical_lower] = variant_map[alias_lower]

    compiled: list[Tuple[Pattern[str], str]] = []
    for variant, canonical_lower in variant_map.items():
        if variant in _BANNED_FREETEXT_ALIASES:
            continue
        pattern = r'(?:^|[^a-z0-9])' + re.escape(variant) + r'(?:$|[^a-z0-9-])'
        compiled.append((re.compile(pattern), canonical_lower))
    return tuple(compiled)


def add_user_skills_to_registry(
    skill_names: list,
    db=None,
    source: str = "manual",
    status: str = "active",
) -> list[str]:
    """Persist new skills from recruiter overrides into the global Skill DB registry.

    Normalizes input names via SKILL_ALIASES, skips existing canonicals (case-insensitive),
    inserts the remainder as active manual skills, and hot-reloads the in-memory registry
    so the skills are immediately available for subsequent extraction.

    Returns the list of newly added canonical skill names.
    """
    if not skill_names or not db:
        return []

    from app.backend.models.db_models import Skill
    from sqlalchemy.exc import IntegrityError

    added: list[str] = []
    seen: set[str] = set()

    try:
        existing = {row[0].lower() for row in db.query(Skill.name).all()}
    except Exception as e:
        logger.warning("Failed to read existing skills from registry: %s", e)
        return []

    for raw in skill_names:
        if isinstance(raw, dict):
            raw = raw.get("skill", "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        canonical = normalize_skill_name(raw.strip()).lower().strip()
        if not canonical:
            continue
        if canonical in seen or canonical in existing:
            continue
        seen.add(canonical)
        try:
            db.add(Skill(
                name=canonical,
                aliases="",
                domain="general",
                status=status,
                source=source,
                frequency=1,
            ))
            db.commit()
            existing.add(canonical)
            added.append(canonical)
        except IntegrityError:
            db.rollback()
            existing.add(canonical)
        except Exception as e:
            db.rollback()
            logger.warning("Failed to add skill '%s' to registry: %s", canonical, e)

    if added:
        try:
            skills_registry.rebuild(db)
            logger.info("Added %d user skills to global registry: %s", len(added), added)
        except Exception as e:
            logger.warning("Failed to rebuild skills registry after adding user skills: %s", e)

    return added


def _extract_skills_from_text(text: str) -> List[str]:
    """Extract skills from text using flashtext processor or regex fallback.

    Validates word boundaries to prevent false positives like matching
    \"go\" inside \"go-to\" or \"going\".
    """
    processor = skills_registry.get_processor()
    if processor:
        try:
            found = processor.extract_keywords(text, span_info=True)
        except TypeError:
            # Fallback for older flashtext versions without span_info
            found = [(kw, None, None) for kw in processor.extract_keywords(text)]

        text_lower = text.lower()
        verified = []
        for item in found:
            if isinstance(item, tuple):
                keyword, start, end = item
            else:
                keyword, start, end = item, None, None

            # Validate word boundaries to prevent false positives.
            # Flashtext matches substrings, so "go" matches inside "go-to",
            # "going", "ago", etc. We reject matches where the skill is
            # preceded by an alphanumeric character or followed by an
            # alphanumeric character or hyphen.
            if start is not None and end is not None:
                if start > 0 and text_lower[start - 1].isalnum():
                    continue
                if end < len(text_lower) and (text_lower[end].isalnum() or text_lower[end] == '-'):
                    continue

            verified.append(keyword)

        return list(dict.fromkeys(verified))

    # Regex fallback with hyphen-aware strict boundaries and alias-to-canonical mapping
    text_lower = text.lower()
    result = []
    seen = set()

    skills_key = tuple(skills_registry.get_all_skills())
    for pattern, canonical_lower in _compiled_skill_patterns(skills_key):
        if canonical_lower in seen:
            continue
        if pattern.search(text_lower):
            result.append(canonical_lower)
            seen.add(canonical_lower)
    return list(dict.fromkeys(result))


# ═══════════════════════════════════════════════════════════════════════════════
# SKILL NORMALIZATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_skill(name: str) -> str:
    """Normalize a skill name to its canonical form using SKILL_SYNONYMS."""
    if not name:
        return ""
    normalized = name.lower().strip()
    # Check synonyms table
    return SKILL_SYNONYMS.get(normalized, normalized)


def get_implied_skills(skill: str) -> list:
    """Get parent/implied skills from hierarchy with confidence."""
    normalized = normalize_skill(skill)
    implied = []
    current = normalized
    depth = 0
    while current in SKILL_HIERARCHY and depth < 3:
        parent = SKILL_HIERARCHY[current]["parent"]
        confidence = round(0.7 - (depth * 0.15), 2)  # 0.7, 0.55, 0.40 for deeper levels
        implied.append({"skill": parent, "confidence": max(0.4, confidence), "source": skill})
        current = normalize_skill(parent)
        depth += 1
    return implied


# ═══════════════════════════════════════════════════════════════════════════════
# TARGETED SKILL CONFIRMATION
# ═══════════════════════════════════════════════════════════════════════════════

def confirm_skills_in_text(target_skills: list, resume_text: str) -> dict:
    """
    Multi-layer targeted skill confirmation against resume text.
    Layer 1: Short skill special patterns (R, C, Go, C#, C++)
    Layer 2: Direct text search with alias expansion
    Layer 3: Certification inference
    Layer 4: Contextual indicators (implied skills)

    Returns: {skill: {"found": bool, "evidence": str, "confidence": str}}
    """
    import re as _re
    text_lower = resume_text.lower()
    results = {}

    for skill in target_skills:
        if not skill or not isinstance(skill, str):
            continue
        skill_lower = skill.lower().strip()
        found = False
        evidence = ""
        confidence = "not_found"

        # Layer 1: Short skill special patterns
        if skill_lower in SHORT_SKILL_PATTERNS:
            try:
                match = _re.search(SHORT_SKILL_PATTERNS[skill_lower], resume_text)
                if match:
                    start = max(0, match.start() - 30)
                    end = min(len(resume_text), match.end() + 30)
                    evidence = resume_text[start:end].strip()
                    found = True
                    confidence = "high"
            except _re.error:
                pass

        # Layer 2: Direct text search with alias expansion
        if not found:
            variants = _get_all_search_variants(skill_lower)
            for variant in variants:
                try:
                    pattern = r'\b' + _re.escape(variant) + r'\b'
                    match = _re.search(pattern, text_lower)
                    if match:
                        start = max(0, match.start() - 50)
                        end = min(len(text_lower), match.end() + 50)
                        evidence = resume_text[start:end].strip()
                        found = True
                        confidence = "high"
                        break
                except _re.error:
                    continue

        # Layer 3: Certification inference
        if not found:
            for cert_name, cert_skills in CERTIFICATION_SKILL_MAP.items():
                if cert_name in text_lower and skill_lower in [s.lower() for s in cert_skills]:
                    # Find the cert mention for evidence
                    idx = text_lower.index(cert_name)
                    start = max(0, idx - 20)
                    end = min(len(resume_text), idx + len(cert_name) + 20)
                    evidence = f"Implied by certification: {resume_text[start:end].strip()}"
                    found = True
                    confidence = "medium"
                    break

        # Layer 4: Contextual indicators (implied skills)
        if not found and skill_lower in CONTEXTUAL_SKILL_INDICATORS:
            for indicator in CONTEXTUAL_SKILL_INDICATORS[skill_lower]:
                if indicator in text_lower:
                    idx = text_lower.index(indicator)
                    start = max(0, idx - 30)
                    end = min(len(resume_text), idx + len(indicator) + 30)
                    evidence = resume_text[start:end].strip()
                    found = True
                    confidence = "contextual"
                    break

        results[skill] = {"found": found, "evidence": evidence, "confidence": confidence}

    return results


def _get_all_search_variants(skill: str) -> list:
    """Get all searchable forms of a skill for text confirmation"""
    variants = {skill}
    # Add from SKILL_ALIASES
    variants.update(SKILL_ALIASES.get(skill, []))
    # Reverse alias lookup
    for canonical, aliases in SKILL_ALIASES.items():
        if skill in [a.lower() for a in aliases]:
            variants.add(canonical)
    # Verb/noun form transformations
    if skill.endswith("ing"):
        variants.add(skill[:-3])        # mentoring → mentor
        variants.add(skill[:-3] + "ed") # mentoring → mentored
    elif skill.endswith("tion"):
        variants.add(skill[:-4] + "te") # communication → communicate
    elif skill.endswith("ment"):
        variants.add(skill[:-4] + "e")  # management → manage
        variants.add(skill[:-4] + "ing") # management → managing
    return list(variants)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SKILL MATCHING FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def match_skills(candidate_skills, jd_skills, jd_nice_to_have=None,
                 structured_skills=None, text_scanned_skills=None) -> dict:
    """Unified skill matching — single source of truth.

    Args:
        candidate_skills: List of skills identified from the candidate.
        jd_skills: List of required skills from the job description.
        jd_nice_to_have: Optional list of nice-to-have skills.
        structured_skills: Tier 0 skills (parser output, high confidence).
        text_scanned_skills: Tier 2 skills (text-scanned, low confidence).
            These are promoted into the candidate set only if they pass
            strict domain-context validation.

    Returns dict with:
    - core_match_ratio: float (0-1)
    - secondary_match_ratio: float (0-1)
    - matched_skills: list
    - matched_skills_detailed: list of dicts with skill, confidence, match_type
    - missing_skills: list
    - skill_score: float (0-100)
    - adjacent_skills: list
    - required_count: int
    - matched_count: int
    """
    if not isinstance(candidate_skills, list):
        candidate_skills = list(candidate_skills) if candidate_skills else []
    if not isinstance(jd_skills, list):
        jd_skills = list(jd_skills) if jd_skills else []
    if jd_nice_to_have is None:
        jd_nice_to_have = []

    jd_required_lower = {_normalize_skill(s) for s in (jd_skills or [])}

    # Step 1: Build candidate set from structured candidate_skills ONLY
    cand_normalized: List[str] = []
    for s in candidate_skills:
        if s and isinstance(s, str):
            cand_normalized.extend(_expand_skill(s))
    cand_set = set(cand_normalized)

    # Step 1b: Build synonym-normalized candidate set for enhanced matching
    cand_syn_set = set()
    for s in candidate_skills:
        if s and isinstance(s, str):
            cand_syn_set.add(normalize_skill(s))
            for v in _expand_skill(s):
                cand_syn_set.add(normalize_skill(v))

    # Step 2: Build domain context from structured skills
    context_subcats = {}
    for s in (structured_skills or candidate_skills):
        norm = _normalize_skill(s)
        for key in _get_skill_subcategory_keys(norm):
            context_subcats[key] = context_subcats.get(key, 0) + 1

    # Step 3: Promote text-scanned skills WITH strict validation
    promoted = []
    for s in (text_scanned_skills or []):
        s_norm = _normalize_skill(s)
        # Skip if already in candidate set (from structured)
        if s_norm in cand_set:
            continue

        subcats = _get_skill_subcategory_keys(s_norm)

        # Platform hierarchy relaxation: accept child skills when JD requires parent
        parent_info = SKILL_HIERARCHY.get(s_norm, {})
        parent = parent_info.get("parent") if isinstance(parent_info, dict) else None
        if parent and parent in jd_required_lower:
            promoted.append(s)
            cand_set.update(_expand_skill(s))
            cand_syn_set.add(normalize_skill(s))
            continue

        if s_norm in HIGH_COLLISION_SKILLS or not subcats:
            # High-collision or unknown-domain: require 2+ context skills from same subcategory
            has_strong_context = any(context_subcats.get(k, 0) >= 2 for k in subcats)
            if not has_strong_context:
                continue  # REJECT
        else:
            # Non-collision: require 1+ context skill from same domain
            skill_domains = {k[0] for k in subcats}
            context_domains = {k[0] for k in context_subcats}
            if not (skill_domains & context_domains):
                continue  # REJECT (different domain entirely)

        promoted.append(s)
        cand_set.update(_expand_skill(s))
        cand_syn_set.add(normalize_skill(s))

    matched = []
    missing = []
    matched_detailed = []

    for req in jd_skills:
        if not req or not isinstance(req, str):
            continue
        req_variants = _expand_skill(req)
        found = False
        match_type = None

        # Exact / alias match
        if any(v in cand_set for v in req_variants):
            found = True
            match_type = "exact" if _normalize_skill(req) in cand_set else "alias"

        # Synonym normalization match
        if not found:
            req_syn = normalize_skill(req)
            if req_syn in cand_syn_set:
                found = True
                match_type = "alias"

        # Substring match only when one skill is a whole token of the other
        # (React ⊂ React Native) — not Java ⊂ JavaScript or SQL ⊂ SQLAlchemy.
        if not found:
            req_norm = _normalize_skill(req)
            if len(req_norm) > 3:
                for c in cand_set:
                    if len(c) > 3 and _is_token_skill_match(req_norm, c):
                        if req_norm in HIGH_COLLISION_SKILLS:
                            req_subcats = _get_skill_subcategory_keys(req_norm)
                            c_subcats = _get_skill_subcategory_keys(c)
                            if not (req_subcats & c_subcats):
                                continue
                        found = True
                        match_type = "substring"
                        break

        # Fuzzy fallback (rapidfuzz, threshold 88)
        if not found:
            req_norm = _normalize_skill(req)
            if req_norm not in HIGH_COLLISION_SKILLS:  # Skip fuzzy for ambiguous skills
                try:
                    from rapidfuzz import fuzz
                    for c in list(cand_set)[:200]:  # cap for performance
                        if fuzz.token_sort_ratio(req_norm, c) >= 88:
                            found = True
                            match_type = "alias"
                            break
                except ImportError:
                    pass

        if found:
            matched.append(req)
            confidence = {"exact": 1.0, "alias": 0.95, "substring": 0.8}.get(match_type, 0.9)
            matched_detailed.append({
                "skill": req,
                "confidence": confidence,
                "match_type": match_type,
            })
        else:
            missing.append(req)

    # Post-loop: Check for hierarchy-inferred matches among missing skills.
    # These are added to matched_skills_detailed ONLY (not matched_skills)
    # to avoid inflating scores until downstream scoring integration.
    all_candidate_skills = [s for s in candidate_skills if s and isinstance(s, str)] + promoted
    for req in missing:
        req_syn = normalize_skill(req)
        for c in all_candidate_skills:
            implied = get_implied_skills(c)
            for imp in implied:
                if normalize_skill(imp["skill"]) == req_syn:
                    matched_detailed.append({
                        "skill": req,
                        "confidence": imp["confidence"],
                        "match_type": "hierarchy_inferred",
                        "source": imp.get("source", c),
                    })
                    break
            else:
                continue
            break

    # Adjacent (nice-to-have) matches
    adjacent = []
    for s in jd_nice_to_have:
        if not s or not isinstance(s, str):
            continue
        s_variants = _expand_skill(s)
        if any(v in cand_set for v in s_variants):
            adjacent.append(s)

    total_req = max(len(jd_skills), 1)
    if not jd_skills:
        skill_score = 0
        unevaluable_skills = True
        core_match_ratio = 0.0
    else:
        skill_score = round(len(matched) / total_req * 100)
        skill_score = min(100, skill_score)
        unevaluable_skills = False
        core_match_ratio = len(matched) / total_req

    secondary_match_ratio = len(adjacent) / max(len(jd_nice_to_have), 1)

    return {
        "core_match_ratio": core_match_ratio,
        "secondary_match_ratio": secondary_match_ratio,
        "matched_skills": matched,
        "matched_skills_detailed": matched_detailed,
        "missing_skills": missing,
        "skill_score": skill_score,
        "adjacent_skills": adjacent,
        "required_count": len(jd_skills),
        "matched_count": len(matched),
        "unevaluable_skills": unevaluable_skills,
    }


def extract_top_skills(required_skills: list, nice_to_have: list, limit: int = 7) -> list:
    """Return top N skills sorted by specificity (fewer taxonomy categories = more specific).

    Required skills are always ranked above nice-to-have skills.  Specificity is
    determined by counting how many top-level taxonomy domains contain the skill — a
    skill that only appears in one domain (e.g. "pytorch") is more specific than one
    that appears in many (e.g. "go").

    Returns a plain list of skill name strings (canonical form).
    """
    # Build a skill → domain-count map (lazily computed once)
    if not hasattr(extract_top_skills, "_domain_count_cache"):
        cache: Dict[str, int] = {}
        for domain_name, subcats in SKILL_TAXONOMY.items():
            for _subcat, skill_list in subcats.items():
                for s in skill_list:
                    norm = _normalize_skill(s)
                    cache[norm] = cache.get(norm, 0) + 1
        extract_top_skills._domain_count_cache = cache

    domain_count = extract_top_skills._domain_count_cache

    def _specificity(skill: str) -> int:
        """Lower is more specific."""
        return domain_count.get(_normalize_skill(skill), 999)

    def _sort_key(item):
        """(tier, specificity).  tier 0 = required, tier 1 = nice-to-have."""
        skill, tier = item
        return (tier, _specificity(skill))

    tagged = [(s, 0) for s in required_skills if s] + [(s, 1) for s in nice_to_have if s]
    # Deduplicate keeping first (required) occurrence
    seen = set()
    unique = []
    for skill, tier in tagged:
        norm = _normalize_skill(skill)
        if norm not in seen:
            seen.add(norm)
            unique.append((skill, tier))

    unique.sort(key=_sort_key)
    return [skill for skill, _tier in unique[:limit]]


# Domain label mapping from SKILL_TAXONOMY top-level keys
_TAXONOMY_DOMAIN_LABELS = {
    "programming_languages": "Backend",
    "web_frontend":         "Frontend",
    "web_backend":          "Backend",
    "databases":            "Backend",
    "cloud_platforms":      "DevOps",
    "aws_services":         "DevOps",
    "devops_infrastructure": "DevOps",
    "embedded_systems":     "Backend",
    "mobile":               "Frontend",
    "ai_ml":                "Data Engineering",
    "data_engineering":     "Data Engineering",
    "data_science_analytics": "Data Engineering",
    "architecture_design":  "Backend",
    "security":             "DevOps",
    "testing":              "QA",
    "blockchain":           "Backend",
    "gaming_graphics":      "Frontend",
    "networking":           "DevOps",
    "project_management":   "Management",
    "business_erp":         "Finance",
    "design_ux":            "Frontend",
    "soft_skills":          "Management",
}


def infer_domain_from_skills(skills: list) -> str:
    """Map a list of skill names to the dominant domain label.

    Uses SKILL_TAXONOMY to determine which domain category has the most skill
    matches.  Returns one of: "Backend", "Frontend", "Full Stack",
    "Data Engineering", "DevOps", "QA", "Finance", "Management", "General".
    """
    if not skills:
        return "General"

    domain_hits: Dict[str, int] = {}
    for skill in skills:
        norm = _normalize_skill(skill)
        for domain_name, subcats in SKILL_TAXONOMY.items():
            for _subcat, skill_list in subcats.items():
                if norm in {_normalize_skill(s) for s in skill_list}:
                    label = _TAXONOMY_DOMAIN_LABELS.get(domain_name, "General")
                    domain_hits[label] = domain_hits.get(label, 0) + 1

    if not domain_hits:
        return "General"

    # Check for full-stack signal: significant frontend AND backend presence
    has_frontend = domain_hits.get("Frontend", 0) >= 2
    has_backend = domain_hits.get("Backend", 0) >= 2
    if has_frontend and has_backend:
        return "Full Stack"

    return max(domain_hits, key=domain_hits.get)


def validate_skills_against_text(skills: List[str], text: str) -> List[str]:
    """Filter out skills not found in the original text (hallucination guard)."""
    if not skills or not text:
        return []

    text_lower = text.lower()
    validated = []

    for skill in skills:
        if not skill or not isinstance(skill, str):
            continue

        skill_lower = skill.lower()

        # Direct substring match
        if skill_lower in text_lower:
            validated.append(skill)
            continue

        # Check aliases from the skill registry
        aliases = SKILL_ALIASES.get(skill_lower, [])
        if any(alias.lower() in text_lower for alias in aliases):
            validated.append(skill)
            continue

        # Multi-word skill: check if any significant word (3+ chars) matches
        words = [w for w in skill_lower.split() if len(w) > 2]
        if words and any(word in text_lower for word in words):
            validated.append(skill)
            continue

    return validated
