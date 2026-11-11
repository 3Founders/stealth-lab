"""The abstract goals tools are ranked under (docs/tool_routing_plan.md).

Twenty-five kinds of thing an agent reaches outside its repository or chat for. Each has a description (what the
retriever and the triage match a request against) and a few words that mark a tool card as serving it. The ids are
stable: outcomes are recorded per (tool, goal) (db/162_tool_observations.sql), so an id is never reused for another kind.

2026-10-31: fifteen became twenty-five. "Make me a banner" and "a minimal fox logo" were triaged as design and offered
Figma-type tools, because generating an image had no goal of its own; the chat-side kinds (notes and wikis, tickets,
analytics, customers, stores, social posts, running code, other AI models) were filed under the nearest developer kind.
"""
from __future__ import annotations

# id -> (description, marker words for tagging a tool card; matched as whole words, lowercase)
TOOL_GOALS: dict[str, tuple[str, tuple[str, ...]]] = {
    "web": ("search the web or read a web page: current information, articles, a URL's content",
            ("web", "search", "browse", "crawl", "fetch", "url", "news", "google", "bing", "serp", "exa", "tavily", "brave")),
    "docs": ("look up a library's, framework's or API's documentation and examples, up to date for its version",
             ("docs", "documentation", "reference", "api", "sdk", "context7", "library", "framework", "manual")),
    "code_search": ("search code beyond this repository: other repositories, GitHub code, examples of an API in use",
                    ("code", "repository", "repositories", "grep", "sourcegraph", "snippet", "codebase")),
    "packages": ("package registries: versions, dependencies, changelogs and licences (npm, PyPI, crates, Maven)",
                 ("npm", "pypi", "package", "packages", "dependency", "dependencies", "crates", "maven", "semver", "changelog")),
    "browser": ("drive a real browser: open pages, click, fill forms, take screenshots, end-to-end and UI tests",
                ("browser", "playwright", "puppeteer", "selenium", "chrome", "screenshot", "e2e", "automation", "headless")),
    "database": ("query or change a database: SQL and NoSQL, schemas, migrations, Postgres, MySQL, SQLite, MongoDB, Redis, "
                 "vector stores",
                 ("database", "sql", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "supabase", "query", "schema",
                  "neon", "vector", "pinecone", "qdrant")),
    "vcs": ("work with GitHub or GitLab: pull requests, reviews, branches, releases, repository issues and comments",
            ("github", "gitlab", "bitbucket", "issue", "issues", "pull", "pr", "repository", "commit", "release", "git")),
    "ci_cd": ("CI and deployment: pipelines, build status, test runs, deploys, GitHub Actions, Vercel, Netlify",
              ("ci", "cd", "pipeline", "actions", "build", "deploy", "deployment", "vercel", "netlify", "jenkins", "circleci", "railway")),
    "cloud": ("cloud infrastructure: AWS, GCP, Azure, Kubernetes, Docker, Terraform, DNS, storage buckets",
              ("aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform", "cloud", "s3", "cloudflare", "infrastructure", "dns")),
    "observability": ("logs, errors and metrics of running software: Sentry, Datadog, Grafana, traces, incidents",
                      ("logs", "logging", "sentry", "datadog", "grafana", "metrics", "monitoring", "tracing", "incident", "errors",
                       "observability")),
    "security": ("security: known vulnerabilities (CVEs) in code or dependencies, secret scanning, auth and identity "
                 "providers, access policies",
                 ("security", "vulnerability", "vulnerabilities", "cve", "snyk", "osv", "secret", "secrets", "auth", "oauth",
                  "identity", "sast", "pentest")),
    "design": ("work with design files and systems: Figma frames, components, design tokens, turning a design into code",
               ("figma", "design", "mockup", "ui", "tokens", "sketch", "frame", "frames", "wireframe")),
    "generate": ("create new images, illustrations, logos, banners, video, music, audio or speech with AI generation models",
                 ("generate", "generation", "dalle", "midjourney", "flux", "diffusion", "fal", "banner", "artwork",
                  "replicate", "video", "tts", "speech", "voice", "elevenlabs", "illustration", "logo")),
    "files_media": ("work with existing files: PDFs, spreadsheets, documents, images and audio -- read, convert, edit, OCR",
                    ("pdf", "excel", "spreadsheet", "csv", "convert", "document", "documents", "ocr", "docx", "pptx", "resize",
                     "image", "images", "audio")),
    "data": ("outside data: scraping sites, company or people enrichment, finance and market data, maps, weather, travel",
             ("scrape", "scraping", "scraper", "enrichment", "finance", "stock", "crypto", "market", "maps", "weather", "flights",
              "seo", "geocoding")),
    "messaging": ("talk to people: Slack, Discord, Teams, email, SMS and push notifications, calendars and meetings",
                  ("slack", "email", "gmail", "discord", "teams", "notification", "calendar", "chat", "sms", "twilio", "meeting")),
    "tickets": ("project and issue tracking: Jira, Linear, Asana, Trello, ClickUp -- tickets, sprints, boards, tasks",
                ("jira", "linear", "asana", "trello", "clickup", "ticket", "tickets", "sprint", "backlog", "kanban")),
    "workspace": ("notes, wikis and shared documents: Notion, Confluence, Google Drive and Docs, Obsidian",
                  ("notion", "confluence", "wiki", "notes", "drive", "obsidian", "docs", "knowledge", "workspace")),
    "analytics": ("product and web analytics: events, funnels, dashboards -- PostHog, Mixpanel, Amplitude, Google Analytics",
                  ("analytics", "posthog", "mixpanel", "amplitude", "funnel", "events", "dashboard", "ga4", "tracking")),
    "crm": ("customers and sales: CRM records, leads, deals, contacts -- HubSpot, Salesforce, Pipedrive",
            ("crm", "hubspot", "salesforce", "pipedrive", "leads", "deals", "contacts", "sales", "customer", "customers")),
    "payments": ("payments and billing: Stripe, invoices, subscriptions, checkout",
                 ("stripe", "payment", "payments", "billing", "invoice", "subscription", "checkout", "paypal")),
    "commerce": ("online stores: products, orders, inventory and storefronts -- Shopify, WooCommerce",
                 ("shopify", "woocommerce", "store", "ecommerce", "commerce", "products", "orders", "inventory", "cart")),
    "social": ("social platforms: post to or read X/Twitter, LinkedIn, Reddit, YouTube, Instagram",
               ("twitter", "linkedin", "reddit", "youtube", "instagram", "tiktok", "social", "tweet", "mastodon", "bluesky")),
    "sandbox": ("run code in an isolated cloud machine: execute scripts or notebooks, compute, try a package safely",
                ("sandbox", "e2b", "modal", "execute", "notebook", "jupyter", "runtime", "interpreter", "compute", "repl")),
    "ai_models": ("call other AI models: chat and embedding APIs, model hubs, fine-tuning -- Hugging Face, OpenRouter",
                  ("llm", "model", "models", "huggingface", "openrouter", "embedding", "embeddings", "inference", "openai",
                   "anthropic", "gemini", "ollama")),
}


# Security subgoals (P1-01, docs/goal_router.md). Narrower than the `security` goal so that
# "scan dependencies for CVEs" does not collapse into "pentest this repo". Ids are stable and
# never reused. The canonical Goals are seeded by db/187_tool_goals_security.sql; the mapper can
# use these markers once it is pointed at subgoals.
SECURITY_SUBGOALS: dict[str, tuple[str, tuple[str, ...]]] = {
    "security_dependency_cve": (
        "find known vulnerabilities (CVEs) in this repository's dependencies and lockfiles",
        ("cve", "vulnerability", "vulnerabilities", "dependency", "dependencies", "osv", "snyk",
         "advisory", "advisories", "lockfile", "sca", "software composition"),
    ),
    "security_secret_scan": (
        "find hardcoded secrets, keys, tokens and credentials in a repository or diff",
        ("secret", "secrets", "credential", "credentials", "api key", "token", "hardcoded",
         "gitleaks", "trufflehog", "entropy"),
    ),
    "security_sast": (
        "statically analyze source code for security weaknesses without running it",
        ("sast", "static analysis", "codeql", "semgrep", "taint", "injection", "xss", "ssrf",
         "path traversal", "unsafe"),
    ),
    "security_auth_review": (
        "review authentication and authorization for privilege escalation and broken access control",
        ("auth", "authentication", "authorization", "oauth", "jwt", "session", "privilege",
         "access control", "idor", "rbac", "tenant"),
    ),
    "security_pentest": (
        "test a repository or application for exploitable vulnerabilities and produce findings",
        ("pentest", "penetration", "exploit", "exploitable", "red team", "attack", "payload",
         "vulnerability scan", "bug bounty"),
    ),
}

ALL_GOALS: dict[str, tuple[str, tuple[str, ...]]] = {**TOOL_GOALS, **SECURITY_SUBGOALS}


def goal_ids() -> list[str]:
    """The 25 tool goals the catalog taxonomy uses (stable)."""
    return list(TOOL_GOALS)


def security_goal_ids() -> list[str]:
    """The security subgoals added by P1-01 (stable)."""
    return list(SECURITY_SUBGOALS)


def goal_text(goal: str) -> str:
    desc, _words = ALL_GOALS[goal]
    return desc
