"""The abstract goals tools are ranked under (docs/tool_routing_plan.md).

Fifteen kinds of thing a coding agent reaches outside its repository for. Each has a description (what the retriever and
the triage match a request against) and a few words that mark a tool card as serving it. The ids are stable: outcomes
are recorded per (tool, goal) once the ranking learns (P3).
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
    "packages": ("package registries: versions, dependencies, changelogs and vulnerabilities (npm, PyPI, crates, Maven)",
                 ("npm", "pypi", "package", "packages", "dependency", "dependencies", "crates", "maven", "vulnerability", "cve", "semver")),
    "browser": ("drive a real browser: open pages, click, fill forms, take screenshots, end-to-end and UI tests",
                ("browser", "playwright", "puppeteer", "selenium", "chrome", "screenshot", "e2e", "automation", "headless")),
    "database": ("query or change a database: SQL and NoSQL, schemas, migrations, Postgres, MySQL, SQLite, MongoDB, Redis",
                 ("database", "sql", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "supabase", "query", "schema", "neon")),
    "vcs": ("work with GitHub or GitLab: issues, pull requests, reviews, branches, releases, comments",
            ("github", "gitlab", "bitbucket", "issue", "issues", "pull", "pr", "repository", "commit", "release", "git")),
    "ci_cd": ("CI and deployment: pipelines, build status, test runs, deploys, GitHub Actions, Vercel, Netlify",
              ("ci", "cd", "pipeline", "actions", "build", "deploy", "deployment", "vercel", "netlify", "jenkins", "circleci", "railway")),
    "cloud": ("cloud infrastructure: AWS, GCP, Azure, Kubernetes, Docker, Terraform, DNS, storage buckets",
              ("aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform", "cloud", "s3", "cloudflare", "infrastructure", "dns")),
    "observability": ("logs, errors and metrics of running software: Sentry, Datadog, Grafana, traces, incidents",
                      ("logs", "logging", "sentry", "datadog", "grafana", "metrics", "monitoring", "tracing", "incident", "errors", "observability")),
    "design": ("design files and assets: Figma, components, tokens, icons, mockups",
               ("figma", "design", "mockup", "ui", "icons", "tokens", "sketch", "canva")),
    "data": ("outside data: scraping sites, company or people enrichment, finance and market data, maps, travel",
             ("scrape", "scraping", "scraper", "enrichment", "finance", "stock", "crypto", "market", "maps", "weather", "flights", "seo")),
    "files_media": ("files and media: PDFs, spreadsheets, documents, images, audio and video, converting or generating them",
                    ("pdf", "excel", "spreadsheet", "csv", "image", "images", "video", "audio", "ocr", "convert", "document", "documents")),
    "messaging": ("talk to people or systems: Slack, email, chat, notifications, calendars, tickets (Jira, Linear)",
                  ("slack", "email", "gmail", "discord", "teams", "notification", "calendar", "jira", "linear", "notion", "chat")),
    "payments": ("payments and billing: Stripe, invoices, subscriptions, checkout",
                 ("stripe", "payment", "payments", "billing", "invoice", "subscription", "checkout", "paypal")),
}


def goal_ids() -> list[str]:
    return list(TOOL_GOALS)


def goal_text(goal: str) -> str:
    desc, _words = TOOL_GOALS[goal]
    return desc
