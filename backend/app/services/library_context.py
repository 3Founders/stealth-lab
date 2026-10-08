"""This repository's own knowledge inside find_ways: `repo_identity`, `library_rows`, `route_obs`.

docs/plan_2026-10_priors_library_survey.md §3.4 / §5.3. The evidence (plan §0) is that a repo's own
solved problems are the core and global knowledge is the enhancer: same-repo past fixes with diffs
lifted "right cause" 27 -> 42 of 96, and repo-first retrieval found useful Goals 22-24% of the time vs
10% over the whole corpus. So, when the caller sends them:

  * library entries (`.stealth/index/library.idx` rows) are judged by the SAME contextual judge, in the
    SAME batch, as the global Goal candidates -- in addition to the top `rerank_top_k`, never displacing
    one. Matches come back as `library_matches`; the caller reads the entry and its diff locally.
  * a library entry that names a global Goal (`g=`) pins that Goal: it is judged even if the fused
    search ranked it below the cut, sorts first among equally confident matches, and breaks an
    "ambiguous" tie when it is the only library-verified Goal in the tie.
  * a STRONG `repo_identity` adds this repository's own global Goals (public benchmarks of the same
    `owner/name` when the caller sends `public_name`; private Goals scoped `repository` = the hashed
    `repo_id`) -- the 3 nearest to the request are judged too. A WEAK identity (a `p:` id, or
    `strength: weak`: a shallow clone with no stored id, or no git) never matches other Goals: it only
    ever labels the caller's own rows. Two unrelated repos must not share Goals through a guess.

Nothing here is stored or logged: like `repo_claims`, these arguments exist for one request.
With none of them, find_ways runs exactly as before (every hook below is a no-op).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from app.stealth.library import IdxRow, parse_library_rows

REPO_ID = re.compile(r"^[rcp]:[0-9a-f]{16}$")
PUBLIC_NAME = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$")

LOCAL_JUDGE_K = 6           # library entries judged per request (lexically preselected)
SAME_REPO_JUDGE_K = 3       # this repo's global Goals judged per request (nearest to the request)
SAME_REPO_MAX_GOALS = 2000  # cap on the id set a public repo name expands to
LIBRARY_PIN, SAME_REPO_PIN = "library", "same_repo"
_PIN_RANK = {LIBRARY_PIN: 2, SAME_REPO_PIN: 1}

_TOK = re.compile(r"[a-z0-9]+")
_STOP = frozenset("the a an and or of to in on for with by from is are be this that it as at fix add use when"
                  .split())


@dataclass(frozen=True)
class RepoIdentity:
    repo_id: str
    public_name: Optional[str] = None
    strength: str = "strong"

    @property
    def weak(self) -> bool:
        return self.strength == "weak" or self.repo_id.startswith("p:")

    @property
    def benchmark_repo(self) -> Optional[str]:
        """`owner/name` as public benchmarks spell their repository, or None. The survey scanner sends the
        normalised remote (`github.com/owner/name`, `gitlab.com/group/sub/project`); public benchmarks are
        GitHub repositories named `owner/name`, so only a github.com remote (or a bare owner/name) maps to one.
        A GitHub owner never contains a dot, so a dotted first segment is a host, not an owner."""
        if not self.public_name:
            return None
        parts = self.public_name.split("/")
        if len(parts) == 3 and parts[0] == "github.com":
            return f"{parts[1]}/{parts[2]}"
        if len(parts) == 2 and "." not in parts[0]:
            return self.public_name
        return None


def parse_repo_identity(raw: Any) -> tuple[Optional[RepoIdentity], Optional[str]]:
    """(identity, problem). A malformed identity is ignored (with the reason reported), never guessed."""
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, Mapping):
        return None, "repo_identity must be an object {repo_id, public_name?, strength?}"
    repo_id = str(raw.get("repo_id") or "").strip().lower()
    if not REPO_ID.match(repo_id):
        return None, "repo_identity.repo_id must be r:|c:|p: followed by 16 hex characters (plan §5.1)"
    name = raw.get("public_name")
    name = str(name).strip().lower() if name else None
    if name and not PUBLIC_NAME.match(name):
        return None, "repo_identity.public_name must look like owner/name"
    strength = str(raw.get("strength") or "strong").strip().lower()
    return RepoIdentity(repo_id, name, "weak" if strength == "weak" else "strong"), None


def pin_rank(hit: Any) -> int:
    """2 = named by this repo's library, 1 = one of this repo's own global Goals, 0 = neither."""
    return _PIN_RANK.get((getattr(hit, "extra", None) or {}).get("pin"), 0)


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "ies", "ied", "ed", "es", "er", "s")


def _stem(t: str) -> str:
    """A crude, deterministic stem so "throttling" meets "throttle" and "limits" meets "limit". Only used to size
    the judge budget, never to decide a match."""
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            break
    return t[:-1] if len(t) > 4 and t.endswith("e") else t


def _tokens(text: str) -> set[str]:
    """Words of `text`, with camelCase, snake_case and path pieces split, stop words dropped, each stemmed."""
    text = _CAMEL.sub(" ", text or "")
    return {_stem(t) for t in _TOK.findall(text.lower()) if len(t) > 2 and t not in _STOP}


@dataclass
class LibraryContext:
    identity: Optional[RepoIdentity] = None
    rows: list[IdxRow] = field(default_factory=list)
    rows_truncated: bool = False
    problems: list[str] = field(default_factory=list)
    local_hits: list[Any] = field(default_factory=list)       # retrieval Hits for the selected rows (judged in place)
    pinned: dict[str, str] = field(default_factory=dict)      # global goal id -> pin reason
    same_repo_goals: int = 0

    @property
    def active(self) -> bool:
        return bool(self.rows) or (self.identity is not None and not self.identity.weak)

    def fingerprint(self) -> str:
        """What the governor's cache key must include: the knowledge answer depends on it."""
        ident = "" if self.identity is None else f"{self.identity.repo_id}/{self.identity.public_name}/{self.identity.strength}"
        return ident + "\x1f" + "\x1f".join(f"{r.id}:{r.block_sha}:{r.status}" for r in self.rows)

    # ---------------------------------------------------------------- local rows

    def select_local(self, query: str, make_hit: Any, k: int = LOCAL_JUDGE_K) -> None:
        """Preselect the library rows worth a judge call: every usable row when there are at most `k`,
        otherwise the rows sharing the most (stemmed) words with the request, then the most recently
        verified rows to fill the `k` slots -- so a paraphrased title with no shared word is still judged
        when the overlap leaves room. Entries recorded as failed attempts are not candidate solutions.
        Lexical overlap only orders the judge budget; the judge decides."""
        usable = [r for r in self.rows if r.outcome != "fail"]
        q = _tokens(query)
        if len(usable) > k:
            scored = sorted(usable, key=lambda r: (-len(q & _tokens(f"{r.title} {r.unit}")),
                                                   _neg(r.verified_at), r.id))
            usable = scored[:k]
        self.local_hits = [make_hit(r) for r in usable]
        for r in usable:
            if r.g:
                self.pinned.setdefault(r.g, LIBRARY_PIN)

    def matches(self) -> list[dict[str, Any]]:
        """`library_matches`: this repo's own entries in judged order (matches, then partial); unjudged
        entries (no judge answered) are listed after them, labelled, so the caller can still look."""
        by_id = {r.id: r for r in self.rows}
        out = []
        for h in self.local_hits:
            r = by_id.get(h.id)
            if r is None:
                continue
            if h.judged and h.relation not in ("matches", "partial"):
                continue
            out.append({"id": r.id, "title": r.title, "unit": r.unit, "g": r.g, "outcome": r.outcome,
                        "status": r.status, "verified_at": r.verified_at, "judged": bool(h.judged),
                        "relation": h.relation, "confidence": h.confidence,
                        "read": f"rg '^(GOAL|PROC|STEP)\\|{r.id}' .stealth/library.md"})
        rank = {"matches": 0, "partial": 1}
        out.sort(key=lambda m: (not m["judged"], rank.get(m["relation"] or "", 2), -(m["confidence"] or 0)))
        return out

    def report(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows), "rows_truncated": self.rows_truncated, "judged_local": len(self.local_hits),
            "pinned_goals": {reason: sum(1 for v in self.pinned.values() if v == reason)
                             for reason in sorted(set(self.pinned.values()))},
            "same_repo_goals": self.same_repo_goals,
            "identity": None if self.identity is None else {"strength": "weak" if self.identity.weak else "strong",
                                                            "public_name_sent": bool(self.identity.public_name)},
            **({"problems": self.problems} if self.problems else {}),
        }


def _neg(date: Optional[str]) -> str:
    """Sort key putting later ISO dates first (lexical complement; missing dates last)."""
    return "".join(chr(0x7F - ord(c)) for c in date) if date else "\x7f"


def build(repo_identity: Any, library_rows: str) -> LibraryContext:
    identity, problem = parse_repo_identity(repo_identity)
    rows, truncated = parse_library_rows(library_rows) if (library_rows or "").strip() else ([], False)
    return LibraryContext(identity=identity, rows=rows, rows_truncated=truncated,
                          problems=[problem] if problem else [])


async def same_repo_goal_ids(pool: Any, identity: Optional[RepoIdentity]) -> list[str]:
    """Global Goals of public benchmarks of this repository (`benchmarks.environment_specification
    ->> 'repo'` = owner/name). Only for a strong identity whose owner sent `public_name`. One indexed
    read of ids (db/146_benchmarks_repo_index.sql); private Goals scoped to the hashed repo_id are
    matched inside the search query itself, without this lookup."""
    if identity is None or identity.weak or not identity.benchmark_repo:
        return []
    rows = await pool.fetch(
        "SELECT DISTINCT goal_id::text AS goal_id FROM benchmarks "
        "WHERE environment_specification ? 'repo' AND lower(environment_specification ->> 'repo') = $1 "
        "AND goal_id IS NOT NULL LIMIT $2",
        identity.benchmark_repo, SAME_REPO_MAX_GOALS)
    return [r["goal_id"] for r in rows]


def local_hit_text(row: IdxRow) -> str:
    """What the judge reads for a library entry: its title (a goal statement) and where it applies."""
    where = "" if row.unit in ("", ".") else f" (in {row.unit})"
    return f"{row.title}{where}"


def make_local_hit_factory(hit_cls: Any) -> Any:
    def make(row: IdxRow) -> Any:
        return hit_cls(row.id, row.title, local_hit_text(row), "local", extra={"local": True})
    return make


def apply_tiebreak(resolved: Sequence[Any], margin: float) -> Optional[Any]:
    """The one library-pinned Goal among matches tied with the top (within `margin`), else None."""
    if len(resolved) < 2:
        return None
    top = resolved[0].confidence or 0
    tied = [h for h in resolved if top - (h.confidence or 0) < margin]
    pinned = [h for h in tied if pin_rank(h) == _PIN_RANK[LIBRARY_PIN]]
    return pinned[0] if len(pinned) == 1 and len(tied) > 1 else None
