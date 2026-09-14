"""
Stealth knowledge store (STEP 13-16): plain markdown files with YAML
frontmatter, grep/read-friendly, no vector DB. This module owns:

- reading/writing Claims, Procedures, Failures
- cheap lexical retrieval (BM25-style, hand-rolled -- no extra dependency
  for a corpus this small)
- conservative deduplication before a new item is written

STEP 34 (anti-contamination) matters here: nothing in this module ever
seeds a claim/procedure that wasn't produced by extraction.py from a real
run. There is no "starter knowledge" bootstrap anywhere in this file.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _normalize(text: str) -> str:
    return " ".join(_tokenize(text))


@dataclass
class Claim:
    id: str
    scope: str
    status: str
    evidence: list[str]
    tags: list[str]
    statement: str
    live: bool = True
    belief: Optional[float] = None


@dataclass
class Procedure:
    id: str
    scope: str
    evidence: list[str]
    tags: list[str]
    title: str
    applicability: list[str]
    method: list[str]
    verification: list[str]
    known_failures: list[str] = field(default_factory=list)


@dataclass
class Failure:
    id: str
    scope: str
    evidence: list[str]
    tags: list[str]
    description: str


def _write_frontmatter_doc(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False)
    path.write_text(f"---\n{yaml_text}---\n\n{body}\n", encoding="utf-8")


def _read_frontmatter_doc(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_text, body = text.split("---", 2)
    frontmatter = yaml.safe_load(fm_text) or {}
    return frontmatter, body.strip()


class KnowledgeStore:
    def __init__(self, root: Path):
        self.root = root
        self.claims_dir = root / "claims"
        self.procedures_dir = root / "procedures"
        self.failures_dir = root / "failures"
        self.runs_dir = root / "runs"
        for d in (self.claims_dir, self.procedures_dir, self.failures_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # --- id allocation ----------------------------------------------------
    def _next_id(self, directory: Path, prefix: str) -> str:
        existing = sorted(p.stem for p in directory.glob(f"{prefix}-*.md"))
        if not existing:
            return f"{prefix}-0001"
        last_num = max(int(name.split("-")[1]) for name in existing)
        return f"{prefix}-{last_num + 1:04d}"

    # --- reads --------------------------------------------------------------
    def load_all_claims(self) -> list[Claim]:
        out = []
        for path in sorted(self.claims_dir.glob("C-*.md")):
            fm, body = _read_frontmatter_doc(path)
            statement = body.split("\n", 1)[-1].strip() if "\n" in body else body
            # body is "# Claim\n\n<statement>" -- pull the statement line.
            lines = [l for l in body.splitlines() if l.strip() and not l.startswith("#")]
            statement = lines[0] if lines else body
            out.append(
                Claim(
                    id=fm["id"], scope=fm.get("scope", "unknown"), status=fm.get("status", "observed"),
                    evidence=fm.get("evidence", []) or [], tags=fm.get("tags", []) or [],
                    statement=statement, live=fm.get("live", True), belief=fm.get("belief"),
                )
            )
        return out

    def load_all_procedures(self) -> list[Procedure]:
        out = []
        for path in sorted(self.procedures_dir.glob("P-*.md")):
            fm, body = _read_frontmatter_doc(path)
            title = ""
            applicability: list[str] = []
            method: list[str] = []
            verification: list[str] = []
            known_failures: list[str] = []
            section = None
            for line in body.splitlines():
                s = line.strip()
                if s.startswith("# "):
                    title = s[2:].strip()
                elif s.startswith("## Applicability"):
                    section = applicability
                elif s.startswith("## Method"):
                    section = method
                elif s.startswith("## Verification"):
                    section = verification
                elif s.startswith("## Known failures"):
                    section = known_failures
                elif s.startswith("##"):
                    section = None
                elif s and section is not None:
                    out_line = re.sub(r"^[\d\.\-\s]+", "", s).strip()
                    if out_line:
                        section.append(out_line)
            out.append(
                Procedure(
                    id=fm["id"], scope=fm.get("scope", "unknown"), evidence=fm.get("evidence", []) or [],
                    tags=fm.get("tags", []) or [], title=title, applicability=applicability,
                    method=method, verification=verification, known_failures=known_failures,
                )
            )
        return out

    def load_all_failures(self) -> list[Failure]:
        out = []
        for path in sorted(self.failures_dir.glob("F-*.md")):
            fm, body = _read_frontmatter_doc(path)
            description = body.strip()
            out.append(
                Failure(
                    id=fm["id"], scope=fm.get("scope", "unknown"), evidence=fm.get("evidence", []) or [],
                    tags=fm.get("tags", []) or [], description=description,
                )
            )
        return out

    # --- dedup (STEP 16) ---------------------------------------------------
    @staticmethod
    def _token_overlap(a: str, b: str) -> float:
        ta, tb = set(_tokenize(a)), set(_tokenize(b))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    DEDUP_THRESHOLD = 0.72  # conservative: near-exact reworded restatements only

    def find_similar_claim(self, statement: str) -> Optional[Claim]:
        for c in self.load_all_claims():
            if self._token_overlap(statement, c.statement) >= self.DEDUP_THRESHOLD:
                return c
        return None

    def find_similar_procedure(self, title: str) -> Optional[Procedure]:
        for p in self.load_all_procedures():
            if self._token_overlap(title, p.title) >= self.DEDUP_THRESHOLD:
                return p
        return None

    def find_similar_failure(self, description: str) -> Optional[Failure]:
        for f in self.load_all_failures():
            if self._token_overlap(description, f.description) >= self.DEDUP_THRESHOLD:
                return f
        return None

    # --- writes -------------------------------------------------------------
    def write_claim(
        self, statement: str, *, scope: str, tags: list[str], evidence: list[str],
        status: str = "observed",
    ) -> Claim:
        existing = self.find_similar_claim(statement)
        if existing is not None:
            merged_evidence = sorted(set(existing.evidence) | set(evidence))
            if merged_evidence != existing.evidence:
                self._update_evidence(self.claims_dir / f"{existing.id}.md", merged_evidence)
                existing.evidence = merged_evidence
            return existing

        claim_id = self._next_id(self.claims_dir, "C")
        fm = {"id": claim_id, "scope": scope, "status": status, "evidence": evidence, "tags": tags}
        _write_frontmatter_doc(self.claims_dir / f"{claim_id}.md", fm, f"# Claim\n\n{statement}")
        return Claim(id=claim_id, scope=scope, status=status, evidence=evidence, tags=tags, statement=statement)

    def write_procedure(
        self, title: str, *, applicability: list[str], method: list[str], verification: list[str],
        known_failures: list[str], scope: str, tags: list[str], evidence: list[str],
    ) -> Procedure:
        existing = self.find_similar_procedure(title)
        if existing is not None:
            merged_evidence = sorted(set(existing.evidence) | set(evidence))
            if merged_evidence != existing.evidence:
                self._update_evidence(self.procedures_dir / f"{existing.id}.md", merged_evidence)
                existing.evidence = merged_evidence
            return existing

        proc_id = self._next_id(self.procedures_dir, "P")
        fm = {"id": proc_id, "scope": scope, "evidence": evidence, "tags": tags}
        body_lines = [f"# {title}", "", "## Applicability"]
        body_lines += [f"- {a}" for a in applicability]
        body_lines += ["", "## Method"]
        body_lines += [f"{i+1}. {m}" for i, m in enumerate(method)]
        body_lines += ["", "## Verification"]
        body_lines += [f"- {v}" for v in verification]
        if known_failures:
            body_lines += ["", "## Known failures"]
            body_lines += [f"- {k}" for k in known_failures]
        _write_frontmatter_doc(self.procedures_dir / f"{proc_id}.md", fm, "\n".join(body_lines))
        return Procedure(
            id=proc_id, scope=scope, evidence=evidence, tags=tags, title=title,
            applicability=applicability, method=method, verification=verification,
            known_failures=known_failures,
        )

    def write_failure(self, description: str, *, scope: str, tags: list[str], evidence: list[str]) -> Failure:
        existing = self.find_similar_failure(description)
        if existing is not None:
            merged_evidence = sorted(set(existing.evidence) | set(evidence))
            if merged_evidence != existing.evidence:
                self._update_evidence(self.failures_dir / f"{existing.id}.md", merged_evidence)
                existing.evidence = merged_evidence
            return existing

        failure_id = self._next_id(self.failures_dir, "F")
        fm = {"id": failure_id, "scope": scope, "evidence": evidence, "tags": tags}
        _write_frontmatter_doc(self.failures_dir / f"{failure_id}.md", fm, description)
        return Failure(id=failure_id, scope=scope, evidence=evidence, tags=tags, description=description)

    def _update_evidence(self, path: Path, evidence: list[str]) -> None:
        fm, body = _read_frontmatter_doc(path)
        fm["evidence"] = evidence
        _write_frontmatter_doc(path, fm, body)

    # --- retrieval (STEP 14) -------------------------------------------------
    def _bm25_scores(self, query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
        query_terms = _tokenize(query)
        if not query_terms or not docs:
            return [0.0] * len(docs)
        doc_tokens = [_tokenize(d) for d in docs]
        doc_lens = [len(t) for t in doc_tokens]
        avgdl = sum(doc_lens) / len(doc_lens) if doc_lens else 0.0
        n = len(docs)
        df = Counter()
        for tokens in doc_tokens:
            for term in set(tokens):
                df[term] += 1
        idf = {term: math.log(1 + (n - c + 0.5) / (c + 0.5)) for term, c in df.items()}
        scores = []
        for tokens, dl in zip(doc_tokens, doc_lens):
            tf = Counter(tokens)
            score = 0.0
            for term in query_terms:
                if term not in tf:
                    continue
                f = tf[term]
                denom = f + k1 * (1 - b + b * dl / (avgdl or 1))
                score += idf.get(term, 0.0) * (f * (k1 + 1)) / (denom or 1)
            scores.append(score)
        return scores

    def retrieve(
        self, query: str, tags: Optional[list[str]] = None, k_claims: int = 5, k_procedures: int = 3,
        k_failures: int = 3,
    ) -> tuple[list[Claim], list[Procedure], list[Failure]]:
        tags = tags or []
        query_full = query + " " + " ".join(tags)

        claims = self.load_all_claims()
        live_claims = [c for c in claims if c.live]
        claim_scores = self._bm25_scores(query_full, [c.statement + " " + " ".join(c.tags) for c in live_claims])
        # Only items with nonzero lexical relevance -- if nothing matches,
        # return nothing rather than padding with irrelevant items (no
        # giant context dumps -- STEP 14).
        ranked_claims = [c for c, s in sorted(zip(live_claims, claim_scores), key=lambda x: -x[1]) if s > 0][:k_claims]

        procedures = self.load_all_procedures()
        proc_texts = [p.title + " " + " ".join(p.applicability) + " " + " ".join(p.tags) for p in procedures]
        proc_scores = self._bm25_scores(query_full, proc_texts)
        ranked_procs = [p for p, s in sorted(zip(procedures, proc_scores), key=lambda x: -x[1]) if s > 0][:k_procedures]

        failures = self.load_all_failures()
        fail_texts = [f.description + " " + " ".join(f.tags) for f in failures]
        fail_scores = self._bm25_scores(query_full, fail_texts)
        ranked_fails = [f for f, s in sorted(zip(failures, fail_scores), key=lambda x: -x[1]) if s > 0][:k_failures]

        return ranked_claims, ranked_procs, ranked_fails

    # --- run records --------------------------------------------------------
    def write_run_record(self, run_id: str, frontmatter: dict, body: str) -> Path:
        path = self.runs_dir / f"{run_id}.md"
        _write_frontmatter_doc(path, frontmatter, body)
        return path
