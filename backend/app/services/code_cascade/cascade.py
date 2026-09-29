"""The structural half of the code cascade (S1-S3): files in, ranked non-trivial SPANS out, with a count at every stage.

    S1  inventory.content_verdict   drop vendored / generated / minified / binary / tiny files
    S2  python_features + scoring   score every function, class and method; weigh files by centrality
    S3  this module                 drop plumbing and copy-paste, cap spans per file, keep the top N

No model is called here, so this half is free and deterministic: the same files give the same ranking. Everything it keeps
is a CANDIDATE for the model judge (S4); nothing here decides that a span is good.

The funnel report exists because "we found the important code" is not checkable, while "of 412 files, 231 survived S1, 1,806
spans were scored, 640 were plumbing, 41 were copy-paste, and the top 40 came from 22 files" is.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping, Optional

from app.services.code_cascade import graph, inventory, python_features, scoring, treesitter_features
from app.services.code_cascade.scoring import RankedSpan

DEFAULT_MAX_SPANS = 40
DEFAULT_PER_FILE = 3


@dataclass
class Funnel:
    files_in: int = 0
    s1_dropped: Counter = field(default_factory=Counter)
    files_after_s1: int = 0
    unsupported_language: Counter = field(default_factory=Counter)
    parse_failed: Counter = field(default_factory=Counter)   # tree-sitter files skipped whole (syntax errors / no spans)
    files_scored: int = 0
    spans_scored: int = 0
    spans_plumbing: Counter = field(default_factory=Counter)
    spans_zero_score: int = 0
    spans_duplicate: int = 0
    spans_candidates: int = 0
    spans_kept: int = 0
    files_with_kept_spans: int = 0

    def as_dict(self) -> dict:
        return {
            "files_in": self.files_in, "s1_dropped": dict(self.s1_dropped), "files_after_s1": self.files_after_s1,
            "unsupported_language": dict(self.unsupported_language), "parse_failed_or_empty": dict(self.parse_failed), "files_scored": self.files_scored,
            "spans_scored": self.spans_scored, "spans_plumbing": dict(self.spans_plumbing),
            "spans_zero_score": self.spans_zero_score, "spans_duplicate": self.spans_duplicate,
            "spans_candidates": self.spans_candidates, "spans_kept": self.spans_kept,
            "files_with_kept_spans": self.files_with_kept_spans,
        }


@dataclass(frozen=True)
class KeptSpan:
    path: str
    language: str
    ranked: RankedSpan
    text: str
    file_importance: float
    file_indegree: int

    @property
    def line_start(self) -> int:
        return self.ranked.features.line_start

    @property
    def line_end(self) -> int:
        return self.ranked.features.line_end


@dataclass
class CascadeResult:
    kept: list[KeptSpan]
    funnel: Funnel
    file_scores: dict[str, float]


def run_structural_cascade(
    files: Mapping[str, str], *, max_spans: int = DEFAULT_MAX_SPANS, per_file: int = DEFAULT_PER_FILE,
    languages: Optional[set[str]] = None,
) -> CascadeResult:
    funnel = Funnel(files_in=len(files))
    survivors: dict[str, tuple[str, str]] = {}         # path -> (text, role)
    languages_of: dict[str, str] = {}
    for path, text in files.items():
        verdict = inventory.content_verdict(path, text)
        if not verdict.kept:
            funnel.s1_dropped[verdict.reason or "unknown"] += 1
            continue
        language = verdict.language or ""
        supported = language == "python" or treesitter_features.supported(language)
        if not supported or (languages is not None and language not in languages):
            funnel.unsupported_language[language or "?"] += 1
            continue
        survivors[path] = (text, "test" if verdict.is_test else "example" if verdict.is_example else "source")
        languages_of[path] = language
    funnel.files_after_s1 = len(survivors) + sum(funnel.unsupported_language.values())
    funnel.files_scored = len(survivors)

    indegree = graph.build_indegree({p: (text, languages_of[p]) for p, (text, _t) in survivors.items()},
                                    go_mod_text=files.get("go.mod"))

    per_file_spans: dict[str, list[RankedSpan]] = {}
    for path, (text, role) in survivors.items():
        ranked: list[RankedSpan] = []
        language = languages_of[path]
        extracted = (python_features.extract_spans(text) if language == "python"
                     else treesitter_features.extract_spans(text, language))
        if not extracted and language != "python" and treesitter_features.supported(language):
            funnel.parse_failed[language] += 1
        for features in extracted:
            funnel.spans_scored += 1
            if features.trivial_reason:
                funnel.spans_plumbing[features.trivial_reason] += 1
                continue
            score = scoring.span_score(features)
            if score <= 0:
                funnel.spans_zero_score += 1
                continue
            ranked.append(RankedSpan(path=path, features=features, score=score, is_test=role == "test", role=role))
        per_file_spans[path] = ranked

    file_scores = {path: scoring.file_importance(spans, indegree.get(path, 0), role=survivors[path][1])
                   for path, spans in per_file_spans.items()}

    # copy-paste: identical identifier-normalised structure keeps only its best-scoring, best-placed copy
    best_by_fingerprint: dict[str, RankedSpan] = {}
    for path, spans in per_file_spans.items():
        for span in spans:
            current = best_by_fingerprint.get(span.features.fingerprint)
            if current is None or (span.score * scoring.ROLE_WEIGHT[span.role]) > (current.score * scoring.ROLE_WEIGHT[current.role]):
                best_by_fingerprint[span.features.fingerprint] = span
    keep_ids = {id(s) for s in best_by_fingerprint.values()}

    candidates: list[tuple[float, RankedSpan]] = []
    for path, spans in per_file_spans.items():
        for span in spans:
            if id(span) not in keep_ids:
                funnel.spans_duplicate += 1
                continue
            funnel.spans_candidates += 1
            weight = scoring.ROLE_WEIGHT[span.role]
            # the span's own score, lifted by how central its file is (a leaf helper loses to a core module's logic)
            lift = 1.0 + 0.15 * min(indegree.get(path, 0), 10)
            candidates.append((span.score * weight * lift, span))
    candidates.sort(key=lambda pair: pair[0], reverse=True)

    taken: Counter = Counter()
    kept: list[KeptSpan] = []
    for _score, span in candidates:
        if len(kept) >= max_spans:
            break
        if taken[span.path] >= per_file:
            continue
        taken[span.path] += 1
        text = survivors[span.path][0]
        lines = text.splitlines()
        kept.append(KeptSpan(
            path=span.path, language=languages_of[span.path], ranked=span,
            text="\n".join(lines[span.features.line_start - 1: span.features.line_end]),
            file_importance=file_scores.get(span.path, 0.0), file_indegree=indegree.get(span.path, 0)))
    funnel.spans_kept = len(kept)
    funnel.files_with_kept_spans = len({k.path for k in kept})
    return CascadeResult(kept=kept, funnel=funnel, file_scores=file_scores)
