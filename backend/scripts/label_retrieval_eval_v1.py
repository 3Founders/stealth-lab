"""Apply model-annotator labels (0-3) to retrieval_eval_v1 candidates,
per the rubric in retrieval_eval_v1.README.md. Judgment is encoded per
query below; anything not named -> 0. Writes labels back into
retrieval_eval_v1.jsonl (candidates array), flagged for human review.
"""
import json, re
from pathlib import Path

DATA = Path(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\tests\data")
CAND = DATA / "retrieval_eval_v1.candidates.jsonl"
OUT = DATA / "retrieval_eval_v1.jsonl"


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# key: a distinctive substring of the query. value: {label: [name-substrings]}
# label 3 = direct match, 2 = useful/relevant, 1 = related-not-useful.
RULES = {
    "tool schemas by loading them only when": {3: ["defer tool-schema loading"], 2: ["mcp-lazy-tool-schema-loading", "corpus-s24-trim-mcp-tool-context"], 1: ["context-engineering", "using-agent-skills"]},
    "run multiple coding agents concurrently on one repository": {3: ["parallel-agent git-worktree isolation"], 2: ["dispatching-parallel-agents", "subagent-driven-development"], 1: ["ai-team-orchestration"]},
    "deterministic structural outline of a source file": {3: ["structural-summary-before-full-read"], 2: [], 1: ["acquire-codebase-knowledge", "code-tour", "context-map"]},
    "list blobs with the Azure Blob Storage Python SDK": {3: ["azure-storage-blob-py"], 2: ["azure-storage-blob-ts", "azure-storage-blob-java", "azure-storage-blob-rust", "azure-storage"], 1: ["azure-storage-file-datalake-py", "azure-storage-file-share-py", "azure-storage-file-share-ts", "azure-storage-queue-py"]},
    "javax namespace to the jakarta namespace": {3: ["javax-to-jakarta-migration"], 2: [], 1: ["java-refactoring-extract-method", "java-refactoring-remove-parameter", "refactor", "deprecation-and-migration"]},
    "record Twilio voice calls correctly": {3: ["twilio-call-recordings"], 2: ["twilio-voice-twiml", "twilio-voice-outbound-calls"], 1: ["twilio-conference-calls", "twilio-conversation-orchestrator", "twilio-voice-conversation-relay"]},
    "red green refactor test-driven loop": {3: ["test-driven-development", "corpus-s09-red-green-refactor-tdd-loop"], 2: ["corpus-s07-red-green-refactor", "eval-driven-dev", "spec-driven-development"], 1: ["doubt-driven-development", "refactor"]},
    "CodeQL code scanning through a GitHub Actions workflow": {3: ["codeql"], 2: ["security-scan", "secret-scanning", "github-actions-hardening"], 1: ["create-github-action-workflow-specification", "security-review"]},
    "diagnose why a metric changed": {3: ["metric-diagnostics"], 2: ["diagnose", "analyze-data-quality"], 1: ["kpi-reporting", "runtime-behavior-probe", "observability-and-instrumentation", "power-bi-performance-troubleshooting"]},
    "develop and debug a Temporal workflow": {3: ["temporal-developer"], 2: ["temporal-ops", "temporal-cloud-setup", "temporal-serverless"], 1: []},
    "complete Rust Model Context Protocol server project": {3: ["rust-mcp-server-generator"], 2: ["ruby-mcp-server-generator", "swift-mcp-server-generator", "java-mcp-server-generator", "go-mcp-server-generator"], 1: []},
    "resize a Canva design into multiple social media formats": {3: ["canva-resize-for-social-media"], 2: ["canva-bulk-create", "adobe-create-social-variations"], 1: ["canva-edit-design", "canva-branded-presentation", "canva-brand-check", "canva-translate-design"]},
    "reverse-engineer a competitor's paid ad strategy": {3: ["competitor-ad-intelligence"], 2: ["ad-campaign-analyzer"], 1: ["gtm-positioning-strategy", "product-business-analysis"]},
    "right Twilio authentication method": {3: ["twilio-security-api-auth"], 2: ["twilio-iam-auth-setup", "twilio-security-hardening"], 1: ["twilio-webhook-architecture", "twilio-account-setup", "twilio-identity-verification-advisor", "twilio-verify-send-otp", "twilio-security-compliance-hipaa"]},
    "keep a large MCP toolset from eating the context window": {3: ["defer tool-schema loading"], 2: ["mcp-lazy-tool-schema-loading", "corpus-s24-trim-mcp-tool-context"], 1: ["context-engineering"]},
    "several AI agents work on the same codebase at the same time": {3: ["parallel-agent git-worktree isolation"], 2: ["dispatching-parallel-agents"], 1: ["ai-team-orchestration"]},
    "cheaply peek at how a code file is organized": {3: ["structural-summary-before-full-read"], 2: [], 1: ["acquire-codebase-knowledge", "code-tour"]},
    "automatic batching regressions in React 18 class components": {3: ["react18-batching-patterns"], 2: ["react18-lifecycle-patterns"], 1: ["react18-string-refs", "react18-legacy-context", "react-best-practices", "react19-concurrent-patterns"]},
    "go-to-definition find-references and hover for a programming language": {3: ["lsp-setup"], 2: [], 1: ["acquire-codebase-knowledge", "code-tour", "csharp-docs"]},
    "move of a dotnet solution off Oracle and onto PostgreSQL": {3: ["creating-oracle-to-postgres-master-migration-plan", "planning-oracle-to-postgres-migration"], 2: ["migrating-oracle-to-postgres-data-access-code", "reviewing-oracle-to-postgres-migration", "scaffolding-oracle-to-postgres-migration-test", "migrating-oracle-to-postgres-stored-procedures", "creating-oracle-to-postgres-migration-integration"], 1: ["roll a postgres schema migration safely", "dotnet-upgrade", "deprecation-and-migration", "creating-oracle-to-postgres-migration-bug-report"]},
    "map and document an unfamiliar codebase to onboard": {3: ["acquire-codebase-knowledge"], 2: ["wiki-onboarding", "code-tour", "architecture-blueprint-generator", "context-map", "codebase-memory-mcp", "wiki-architect"], 1: ["uml-and-software-architecture-visualization", "doc-and-modernize"]},
    "multi-axis review of a code change before merging": {3: ["code-review-and-quality"], 2: ["requesting-code-review", "code-review", "review-and-refactor", "maintainer-review"], 1: ["security-diff-scan", "pr-draft-summary"]},
    "Dynamics 365 Finance and Supply Chain solution blueprint": {3: ["d365-solution-blueprint"], 2: [], 1: ["create-specification", "prd"]},
    "surgically restructure code to improve maintainability": {3: ["refactor"], 2: ["review-and-refactor", "code-simplification", "refactor-method-complexity-reduce", "refactor-plan"], 1: ["implementation-strategy", "create-implementation-plan"]},
    "stop context bloat coming from tool definitions": {3: ["defer tool-schema loading"], 2: ["corpus-s24-trim-mcp-tool-context"], 1: ["context-engineering", "prompt-optimizer"]},
    "sandbox concurrent automation processes so their file writes do not clash": {3: ["parallel-agent git-worktree isolation"], 2: ["dispatching-parallel-agents"], 1: ["sandbox-sdk"]},
    "wire up editor code intelligence like jump to symbol": {3: ["lsp-setup"], 2: [], 1: ["editorconfig", "acquire-codebase-knowledge", "code-exemplars-blueprint-generator"]},
    "run security static analysis as part of continuous integration": {3: [], 2: ["security-scan", "roslyn-analyzers", "security-and-hardening"], 1: ["security-review", "threat-model-analyst", "attack-path-analysis", "mcp-implementation-security-review"]},
    "drive a browser to exercise a locally running web front end": {3: ["webapp-testing"], 2: ["corpus-s30-drive-a-browser-via-stable-refs", "browser-testing-with-devtools", "frontend-testing-debugging", "agent-browser"], 1: ["url-to-code", "web-perf"]},
    "shift a legacy servlet app to the newer enterprise Java package names": {3: ["javax-to-jakarta-migration"], 2: [], 1: ["java-helidon", "create-spring-boot-java-project", "dotnet-upgrade"]},
    "create validate and edit reproducible SQL or Python notebooks": {3: ["jupyter-notebooks"], 2: [], 1: ["sql-code-review", "analyze-data-quality", "validate-data", "bigquery-pipeline-audit"]},
    "rotate the account access keys on an Azure storage account": {3: [], 2: [], 1: ["azure-security-keyvault-keys-dotnet", "azure-keyvault-keys-ts", "azure-keyvault-py", "azure-security-keyvault-keys-java", "azure-keyvault-keys-rust", "azure-storage"]},
    "transcribe an existing Twilio call recording into text": {3: [], 2: ["azure-ai-transcription-py", "azure-speech-to-text-rest-py"], 1: ["twilio-call-recordings", "narrator", "speak-summary"]},
    "write data-driven unit tests with pytest fixtures and parametrize": {3: [], 2: ["pytest-coverage", "test-coverage-improver"], 1: ["csharp-nunit", "csharp-xunit", "csharp-tunit", "unit-test-vue-pinia", "test-driven-development"]},
    "profile and speed up a slow SQL query": {3: ["sql-optimization", "postgresql-optimization"], 2: ["performance-optimization", "supabase-postgres-best-practices"], 1: ["sql-code-review", "postgresql-code-review", "kql"]},
    "upgrade an Azure workload to a higher pricing tier or SKU": {3: ["azure-upgrade"], 2: [], 1: []},
    "embed a Zoom meeting inside a Flutter mobile app": {3: [], 2: ["zoom-video-sdk-flutter"], 1: ["build-zoom-meeting-sdk-app", "build-zoom-meeting-app", "zoom-meeting-sdk-web", "zoom-meeting-sdk-react-native", "zoom-meeting-sdk-android", "choose-zoom-approach", "plan-zoom-integration"]},
    "build a Slack app that posts messages from a workflow": {3: [], 2: [], 1: ["slack-to-teams", "build-zoom-team-chat-app", "agentic-workflows"]},
    "generate a simple text-to-speech narration of an article": {3: [], 2: ["narrator", "speak-summary"], 1: ["podcast-generation", "subtitles", "faceless-channel"]},
    "deploy a static site to Netlify": {3: [], 2: ["azure-static-web-apps", "publish-to-pages", "corpus-s03-deploy-a-project-to-vercel"], 1: ["publish-artifact-to-sites", "deploy", "share"]},
    "review my code": {3: ["code-review-and-quality"], 2: ["code-review", "requesting-code-review", "receiving-code-review", "review-and-refactor", "security-review"], 1: ["code-tour"]},
    "help me refactor this": {3: ["refactor"], 2: ["refactor-method-complexity-reduce", "refactor-plan", "review-and-refactor"], 1: ["java-refactoring-remove-parameter", "java-refactoring-extract-method"]},
    "improve the quality of this codebase": {3: [], 2: ["refactor", "review-and-refactor", "code-simplification", "refactor-method-complexity-reduce"], 1: ["acquire-codebase-knowledge", "improve-skill", "receiving-code-review"]},
    "make this better": {3: [], 2: [], 1: ["improve-skill"]},
    "load test my API to measure performance under concurrency": {3: [], 2: ["performance-optimization", "android-performance", "web-perf"], 1: ["csharp-async", "corpus-s31-eliminate-request-waterfalls"]},
    "isolate and fix a flaky failing test": {3: [], 2: ["triage a failing ci job", "corpus-s09-red-green-refactor-tdd-loop", "test-coverage-improver"], 1: ["debugging-and-error-recovery", "runtime-behavior-probe"]},
    "read a very large CSV file into a pandas dataframe": {3: [], 2: [], 1: []},
    "generate a large synthetic dataset for model training": {3: [], 2: [], 1: ["arize-dataset", "arize-experiment", "corpus-s50-build-evaluate-a-tool-use-task"]},
    "review the meeting notes and summarize action items": {3: ["meeting-minutes"], 2: ["notion-meeting-intelligence", "meeting-prep"], 1: ["roundup", "daily-prep", "memo-builder"]},
    "origin of the em dash in writing": {3: ["em-dash"], 2: [], 1: ["writing-skills"]},
}


def label_for(query, name):
    nn = norm(name)
    for key, bands in RULES.items():
        if norm(key) in norm(query) or key.lower() in query.lower():
            for lab in (3, 2, 1):
                for sub in bands.get(lab, []):
                    if norm(sub) in nn or nn in norm(sub):
                        return lab
            return 0
    return 0


cands = [json.loads(l) for l in CAND.read_text(encoding="utf-8").splitlines() if l.strip()]
out_rows = []
counts = {0: 0, 1: 0, 2: 0, 3: 0}
for r in cands:
    is_no_match = r["bucket"] == "no_match"
    labelled = []
    for c in r["candidates"]:
        lab = 0 if is_no_match else label_for(r["query"], c["name"])
        counts[lab] += 1
        labelled.append({**c, "label": lab})
    out_rows.append({"query": r["query"], "bucket": r["bucket"], "note": r["note"],
                     "candidates": labelled})

OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows) + "\n", encoding="utf-8")
print("label distribution:", counts)
print("queries:", len(out_rows), " candidates:", sum(counts.values()))
# sanity: every non-no_match query should have >=1 label>=2 unless deliberately hard
weak = [r["query"] for r in out_rows if r["bucket"] not in ("no_match",)
        and not any(c["label"] >= 2 for c in r["candidates"])]
print("queries with no label>=2 (expected for the hard/none buckets):")
for q in weak:
    print("   -", q)
