"""The local tier (arm L1): what a repo's own `.stealth/` would hold, built from the checkout alone.

    claims  -- repo facts (stand-in for survey_repo's claims.md)
    library -- this repo's past fixes with their diffs (stand-in for library.md bootstrapped from git history)

Both are STUBS until workstream C (feat/survey) merges its scanner and git-history miner; `local_context()` is
the single seam to swap (experiment.json providers.local = "survey"), and the swap is logged in DEVIATIONS.md
before any scored notes are built.

Leakage rules (tested in tests/test_local_eval.py):
* only commits that are ANCESTORS of base_commit are read (`git log <base_commit>`), so the task's own fix and
  anything after it can never appear;
* the window is bounded (history_window_days before the base commit, at most history_max_commits);
* nothing is read from the dataset's install_config / test command (that would be privileged information).
"""
from __future__ import annotations

import math
import re
import json
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FIX_RE = re.compile(r"\b(fix(e[sd])?|bug|issue|regression|error|crash|incorrect|wrong|broken|resolve[sd]?|"
                    r"handle|prevent|fail(s|ing|ure)?)\b|#\d+", re.I)
DOC_ONLY_RE = re.compile(r"(^|/)(docs?|\.github|changelog|changes|news)(/|$)|\.(md|rst|txt)$|(^|/)(CHANGELOG|CHANGES|HISTORY|NEWS)", re.I)
LOCK_RE = re.compile(r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|poetry\.lock|uv\.lock|go\.sum|"
                     r"Gemfile\.lock|composer\.lock|\.snap)$")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
STOP = set("the a an and or of to in for on is it this that with be as by at from are was not no but if then "
           "when we i you can should would could have has had do does did use using used get set new add".split())

MANIFESTS = ("package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini", "noxfile.py",
             "Cargo.toml", "go.mod", "go.work", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
             "settings.gradle.kts", "build.sbt", "Gemfile", "composer.json", "mix.exs", "pubspec.yaml",
             "Package.swift", "CMakeLists.txt", "Makefile", "justfile", "pnpm-workspace.yaml", "lerna.json",
             "nx.json", "turbo.json", "deno.json", "global.json", "Directory.Build.props")
EXT_LANG = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
            ".mjs": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".scala": "scala",
            ".cs": "csharp", ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
            ".swift": "swift", ".ex": "elixir", ".exs": "elixir", ".dart": "dart", ".clj": "clojure"}


@dataclass
class Entry:
    sha: str
    when: int
    subject: str
    body: str
    files: list[str]
    score: float = 0.0
    diff: str = ""
    solution: str = ""      # survey provider: library/solutions/<id>.diff, relative to .stealth/library


@dataclass
class LocalContext:
    claims: list[str] = field(default_factory=list)
    library: list[Entry] = field(default_factory=list)
    scanned_commits: int = 0
    fix_commits: int = 0


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=check)
    return r.stdout


def tokens(text: str) -> list[str]:
    out = []
    for t in TOKEN_RE.findall(text or ""):
        for part in re.split(r"(?<=[a-z])(?=[A-Z])|_", t):   # camelCase / snake_case pieces too
            p = part.lower()
            if len(p) > 1 and p not in STOP:
                out.append(p)
    return out


def bm25_rank(query: str, docs: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    toks = [tokens(d) for d in docs]
    if not toks:
        return []
    avg = sum(map(len, toks)) / len(toks) or 1.0
    df = Counter(t for d in toks for t in set(d))
    n = len(toks)
    q = set(tokens(query))
    scores = []
    for d in toks:
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    return scores


def fix_commits(repo: Path, base_commit: str, window_days: int, max_commits: int) -> tuple[list[Entry], int]:
    """Non-merge ancestors of base_commit inside the window that look like fixes and touch non-doc files."""
    base_time = int(git(repo, "show", "-s", "--format=%ct", base_commit).strip())
    since = base_time - window_days * 86400
    raw = git(repo, "log", "--no-merges", f"-n{max_commits}", f"--since={since}", "--name-only",
              "--format=%x1e%H%x1f%ct%x1f%s%x1f%b%x1f", base_commit)
    out, scanned = [], 0
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        scanned += 1
        sha, ct, subj, body, names = (rec.split("\x1f") + ["", "", "", "", ""])[:5]
        files = [f for f in names.strip().splitlines() if f.strip()]
        if not FIX_RE.search(f"{subj}\n{body}"):
            continue
        code_files = [f for f in files if not DOC_ONLY_RE.search(f) and not LOCK_RE.search(f)]
        if not code_files:
            continue
        out.append(Entry(sha=sha.strip(), when=int(ct or 0), subject=subj.strip(), body=body.strip()[:1500],
                         files=files))
    return out, scanned


def commit_diff(repo: Path, sha: str, max_chars: int) -> str:
    diff = git(repo, "show", "--format=", "--patch", "--no-color", sha, "--", ".",
               ":(exclude)*.lock", ":(exclude)package-lock.json", ":(exclude)pnpm-lock.yaml", ":(exclude)go.sum",
               check=False)
    return diff if len(diff) <= max_chars else diff[: max_chars - 20].rstrip() + "\n...[truncated]"


def claims_stub(repo: Path, base_commit: str) -> list[str]:
    """Deterministic repo facts from the tree at base_commit (manifests, languages, CI, test dirs, declared
    scripts). Stand-in for survey_repo's claims.md; never reads the dataset's install/test commands."""
    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", base_commit).splitlines() if f]
    facts = []
    langs = Counter(EXT_LANG[Path(f).suffix] for f in files if Path(f).suffix in EXT_LANG)
    if langs:
        facts.append("languages (by file count): " + ", ".join(f"{k} {v}" for k, v in langs.most_common(4)))
    man = [f for f in files if Path(f).name in MANIFESTS and f.count("/") <= 2]
    if man:
        facts.append("manifests: " + ", ".join(sorted(man)[:15]) + (" ..." if len(man) > 15 else ""))
    roots = sorted({str(Path(f).parent).replace("\\", "/") for f in man if Path(f).name in
                    ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
                     "build.gradle.kts", "setup.py")})
    if len(roots) > 1:
        facts.append(f"multi-package repository: {len(roots)} package roots, e.g. " + ", ".join(roots[:6]))
    ci = sorted(f for f in files if f.startswith(".github/workflows/") or f in (".gitlab-ci.yml", ".travis.yml",
                                                                                  "azure-pipelines.yml", ".circleci/config.yml"))
    if ci:
        facts.append("CI: " + ", ".join(ci[:6]))
    tdirs = Counter(f.split("/")[0] for f in files if re.search(r"(^|/)(tests?|spec|__tests__)(/|$)", f))
    if tdirs:
        facts.append("tests live under: " + ", ".join(d for d, _ in tdirs.most_common(4)))
    if "package.json" in files:
        import json
        try:
            pkg = json.loads(git(repo, "show", f"{base_commit}:package.json"))
            scripts = pkg.get("scripts") or {}
            keep = {k: v for k, v in scripts.items() if re.match(r"(test|build|lint|check|typecheck)", k)}
            if keep:
                facts.append("package.json scripts: " + "; ".join(f"{k} = {v}" for k, v in list(keep.items())[:6]))
            eng = pkg.get("engines")
            if eng:
                facts.append(f"engines: {eng}")
        except ValueError:
            pass
    for name, pat in (("pyproject.toml", r"requires-python\s*=\s*['\"]([^'\"]+)"), ("go.mod", r"^go\s+(\S+)"),
                      ("Cargo.toml", r"edition\s*=\s*['\"](\d+)")):
        if name in files:
            m = re.search(pat, git(repo, "show", f"{base_commit}:{name}", check=False), re.M)
            if m:
                facts.append(f"{name}: {m.group(0).strip()}")
    return facts


SURVEY_ENV = "LOCAL_EVAL_SURVEY_MJS"   # path to workstream C's packaging/npm/lib/survey/survey.mjs
RUNNER = Path(__file__).resolve().parent / "survey_runner.mjs"


def survey_path() -> Path:
    import os

    p = os.environ.get(SURVEY_ENV)
    if not p:
        raise NotImplementedError(
            f"set {SURVEY_ENV} to workstream C's packaging/npm/lib/survey/survey.mjs (feat/survey, or main once "
            "merged), and log the switch from the stub in DEVIATIONS.md before building scored notes")
    return Path(p)


def _kv(fields: list[str]) -> dict[str, str]:
    return dict(x.split("=", 1) for x in fields if "=" in x)


def parse_claims(stealth: Path) -> list[str]:
    """CLAIM|id|status|topic|scope|statement|source=...|... -> '[topic] statement (scope; source)'."""
    out = []
    for page in [stealth / "claims.md", *sorted((stealth / "claims").glob("*.md"))]:
        if not page.exists():
            continue
        for line in page.read_text(encoding="utf-8", errors="replace").splitlines():
            f = line.split("|")
            if f[0] != "CLAIM" or len(f) < 6 or f[2] not in ("current", "absent"):
                continue
            scope = "" if f[4] in ("repository", "repo", ".") else f"{f[4]}; "
            src = _kv(f[6:]).get("source", "").split("#")[0]
            out.append(f"[{f[3]}] {f[5]} ({scope}{src})" if (src or scope) else f"[{f[3]}] {f[5]}")
    return out


def parse_library(stealth: Path) -> list[Entry]:
    """GOAL/PROC blocks of library.md (+ library/archive-*.md) -> entries carrying their solution diff path."""
    entries: dict[str, Entry] = {}
    for page in [stealth / "library.md", *sorted((stealth / "library").glob("archive-*.md"))]:
        if not page.exists():
            continue
        for line in page.read_text(encoding="utf-8", errors="replace").splitlines():
            f = line.split("|")
            if f[0] == "GOAL" and len(f) >= 3:
                entries[f[1]] = Entry(sha=_kv(f[3:]).get("commit", f[1]), when=0, subject=f[2], body="", files=[])
            elif f[0] == "PROC" and len(f) >= 3 and f[1].split(".")[0] in entries:
                kv = _kv(f[3:])
                e = entries[f[1].split(".")[0]]
                e.files = [t.split("#")[0] for t in kv.get("touches", "").split(",") if t]
                e.solution = kv.get("solution", "")
    return list(entries.values())


def survey_context(repo: Path, base_commit: str, issue: str, mem: dict) -> LocalContext:
    """Workstream C's real scanner + miner on a fresh worktree at base_commit (its `git log HEAD` there sees only
    ancestors of base_commit), then the library ranked against the issue as a terms.idx lookup would (BM25 over
    titles and touched paths). `since` is two years before the BASE commit, not before today."""
    import datetime
    import shutil
    import tempfile

    scanner = survey_path()   # refuse before touching git when the scanner is not configured
    base_time = int(git(repo, "show", "-s", "--format=%ct", base_commit).strip())
    since = datetime.datetime.fromtimestamp(base_time - mem["history_window_days"] * 86400,
                                            datetime.timezone.utc).strftime("%Y-%m-%d")
    wt = Path(tempfile.mkdtemp(prefix="survey_", dir=repo.parent))
    try:
        git(repo, "worktree", "add", "--detach", "--force", str(wt), base_commit)
        r = subprocess.run(["node", str(RUNNER), str(scanner), str(wt), since, str(mem["history_max_commits"])],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"survey failed: {r.stderr[-400:]}")
        summary = json.loads(r.stdout.strip().splitlines()[-1])
        stealth = wt / ".stealth"
        lib = parse_library(stealth)
        for e, s in zip(lib, bm25_rank(issue, [f"{e.subject}\n{' '.join(e.files)}" for e in lib])):
            e.score = s
        top = [e for e in sorted(lib, key=lambda e: -e.score) if e.score > 0][: mem["library_entries"]]
        for e in top[: mem["library_diffs"]]:
            sol = stealth / "library" / e.solution
            if e.solution and sol.exists():
                d = sol.read_text(encoding="utf-8", errors="replace")
                n = mem["diff_max_chars"]
                e.diff = d if len(d) <= n else d[: n - 20].rstrip() + "\n...[truncated]"
        hist = summary.get("history") or {}
        return LocalContext(claims=parse_claims(stealth), library=top,
                            scanned_commits=int(hist.get("scanned") or 0), fix_commits=len(lib))
    finally:
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)


def local_context(repo: Path, base_commit: str, issue: str, mem: dict, provider: str = "stub") -> LocalContext:
    if provider == "survey":
        return survey_context(repo, base_commit, issue, mem)
    if provider != "stub":
        raise ValueError(f"unknown local provider {provider!r}")
    commits, scanned = fix_commits(repo, base_commit, mem["history_window_days"], mem["history_max_commits"])
    scores = bm25_rank(issue, [f"{c.subject}\n{c.body}\n{' '.join(c.files)}" for c in commits])
    for c, s in zip(commits, scores):
        c.score = s
    top = [c for c in sorted(commits, key=lambda c: (-c.score, -c.when)) if c.score > 0][: mem["library_entries"]]
    for c in top[: mem["library_diffs"]]:
        c.diff = commit_diff(repo, c.sha, mem["diff_max_chars"])
    return LocalContext(claims=claims_stub(repo, base_commit), library=top, scanned_commits=scanned,
                        fix_commits=len(commits))
