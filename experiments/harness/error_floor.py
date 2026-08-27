"""
Extraction error-floor grader (Band 3 prep, board MEASURE item 4).

Grades an observation extractor's output over hand-gold trace excerpts
(fixtures/error_floor/, contract in fixtures/error_floor/_rubric.md) with
DEFINED match rules -- typed canonical-key equality after documented
normalization, token-Jaccard >= threshold for semantic labels, one-to-one
greedy matching in gold order -- and produces per-type precision/recall
with numerator/denominator beside every rate.

This module is PURE: no I/O beyond fixture loading, no backend import
(lane rule), no model calls. The extractor under test enters as a plain
callable adapter: trace_event dict -> list[{observation_type, label,
properties}] -- the same shape app/services/observations.py's extractors
return, without this harness depending on them.

`grade_excerpt`/`observations_match`/`semantic_match` accept an optional
`judge` (sync `(gold_label, pred_label) -> bool`) consulted ONLY when a
semantic_label pair fails the Jaccard rule -- purity is preserved by
default (`judge=None` is byte-identical to every prior grading run); a
live judge is wired up one layer out, in run_error_floor.py + the new
semantic_judge.py, never inside this module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURES_DIR = HERE / "fixtures" / "error_floor"

#: Closed set per the rubric. A fixture or prediction outside it is a
#: validation error, forcing a conscious rubric extension rather than a
#: silent new category.
KNOWN_TYPES = (
    "file_touched",
    "commit_made",
    "test_run",
    "command_executed",
    "semantic_label",
)

#: Which property carries the canonical identity key for each type.
KEY_FIELD = {
    "file_touched": "file_path",
    "commit_made": "command",
    "test_run": "command",
    "command_executed": "command",
}

#: semantic_label has no key field; its text is graded by token Jaccard.
SEMANTIC_TYPE = "semantic_label"
SEMANTIC_JACCARD_THRESHOLD = 0.5
SEMANTIC_STOPWORDS = frozenset(
    "a an the was were is are been be to of in on for and with by at from".split()
)

MIN_EXCERPTS = 30
MAX_EXCERPTS = 50


class FixtureError(ValueError):
    """A fixture violates the rubric contract; the instrument refuses to run."""


# --------------------------------------------------------------------------
# normalization + match rules (the rubric, verbatim)
# --------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """Backslashes -> forward slashes, strip leading './' (rubric rule 2)."""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def normalize_command(command: str) -> str:
    """Collapse whitespace runs to single spaces, strip ends (rubric rule 2)."""
    return " ".join(command.split())


def label_tokens(text: str) -> frozenset[str]:
    """Lowercase [a-z0-9]+ tokens minus the stopword list (rubric rule 2)."""
    return frozenset(
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in SEMANTIC_STOPWORDS
    )


def semantic_match(gold_label: str, pred_label: str, judge=None) -> bool:
    """Token-Jaccard first pass (free, deterministic). `judge`, when given,
    is a sync `(gold_label, pred_label) -> bool` callable consulted ONLY on
    a Jaccard miss - a paraphrase/synonym adjudicator, never a replacement
    for the cheap default (see semantic_judge.py for the live implementation
    and the design rationale). `judge=None` is byte-identical to the
    pre-judge grading behavior."""
    gt, pt = label_tokens(gold_label), label_tokens(pred_label)
    if not gt or not pt:
        return False
    j = len(gt & pt) / len(gt | pt)
    if j >= SEMANTIC_JACCARD_THRESHOLD:
        return True
    if judge is None:
        return False
    return bool(judge(gold_label, pred_label))


def observation_key(obs: dict) -> str | None:
    """Canonical key for a well-formed observation, else None."""
    otype = obs.get("observation_type")
    props = obs.get("properties") or {}
    if otype == SEMANTIC_TYPE:
        label = obs.get("label")
        return label.strip() if isinstance(label, str) and label.strip() else None
    field = KEY_FIELD.get(otype)
    if field is None:
        return None
    val = props.get(field)
    if not isinstance(val, str) or not val.strip():
        return None
    return normalize_path(val) if field == "file_path" else normalize_command(val)


def observations_match(gold: dict, pred: dict, judge=None) -> bool:
    gk, pk = observation_key(gold), observation_key(pred)
    if gk is None or pk is None:
        return False
    if gold["observation_type"] != pred["observation_type"]:
        return False
    if gold["observation_type"] == SEMANTIC_TYPE:
        return semantic_match(gold["label"], pred["label"], judge=judge)
    return gk == pk


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _validate_observation(obs, where: str) -> None:
    if not isinstance(obs, dict):
        raise FixtureError(f"{where}: observation must be a dict")
    otype = obs.get("observation_type")
    if otype not in KNOWN_TYPES:
        raise FixtureError(f"{where}: unknown observation_type {otype!r}")
    if otype == SEMANTIC_TYPE:
        if not isinstance(obs.get("label"), str) or not obs["label"].strip():
            raise FixtureError(f"{where}: semantic_label requires non-empty label")
        return
    field = KEY_FIELD[otype]
    props = obs.get("properties")
    if not isinstance(props, dict) or not isinstance(props.get(field), str) \
            or not props[field].strip():
        raise FixtureError(f"{where}: {otype} requires properties.{field}")


def load_excerpts(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> list[dict]:
    """Load + validate every *.json excerpt file in fixtures_dir."""
    excerpts: list[dict] = []
    seen: set[str] = set()
    paths = sorted(p for p in Path(fixtures_dir).glob("*.json"))
    if not paths:
        raise FixtureError(f"no excerpt files under {fixtures_dir}")
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for ex in data.get("excerpts", []):
            eid = ex.get("excerpt_id", "")
            where = f"{path.name}:{eid}"
            if not eid.startswith("ef-"):
                raise FixtureError(f"{where}: excerpt_id must start with 'ef-'")
            if eid in seen:
                raise FixtureError(f"{where}: duplicate excerpt_id")
            seen.add(eid)
            event = ex.get("trace_event")
            if not isinstance(event, dict) or not isinstance(event.get("tool_name"), str) \
                    or not event["tool_name"]:
                raise FixtureError(f"{where}: trace_event needs a tool_name")
            if not isinstance(ex.get("gold"), list):
                raise FixtureError(f"{where}: gold must be a list")
            for i, g in enumerate(ex["gold"]):
                _validate_observation(g, f"{where} gold[{i}]")
            if not isinstance(ex.get("notes"), str) or not ex["notes"].strip():
                raise FixtureError(f"{where}: notes required (authorship rule)")
            excerpts.append(ex)

    n = len(excerpts)
    if not MIN_EXCERPTS <= n <= MAX_EXCERPTS:
        raise FixtureError(f"{n} excerpts outside sanctioned range {MIN_EXCERPTS}-{MAX_EXCERPTS}")

    types_with_gold = {g["observation_type"] for ex in excerpts for g in ex["gold"]}
    missing = set(KNOWN_TYPES) - types_with_gold
    if missing:
        raise FixtureError(f"no gold observations cover types: {sorted(missing)}")
    return excerpts


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------

def grade_excerpt(excerpt: dict, predictions: list[dict], judge=None) -> dict:
    """One-to-one greedy match in gold order (rubric 'Confusion accounting').

    Returns {tp, fp, fn, tp_ids, fp_items, fn_items}; reasons per rubric.
    Malformed predictions (unknown type / missing key) count as FPs of
    their claimed type -- emitting junk costs precision even when it is
    not even well-formed. `judge` (optional) is forwarded to
    `observations_match` for semantic_label Jaccard-miss adjudication;
    omitted, grading is unchanged from the pre-judge rubric.
    """
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for gi, gold in enumerate(excerpt["gold"]):
        for pi, pred in enumerate(predictions):
            if pi in used:
                continue
            if observations_match(gold, pred, judge=judge):
                pairs.append((gi, pi))
                used.add(pi)
                break

    fp_items, fn_items = [], []
    for pi, pred in enumerate(predictions):
        if pi in used:
            continue
        fp_items.append(_fp_record(pred, excerpt["gold"]))
    matched_golds = {gi for gi, _ in pairs}
    for gi, gold in enumerate(excerpt["gold"]):
        if gi in matched_golds:
            continue
        fn_items.append(_gold_reason(gold, predictions))

    return {
        "excerpt_id": excerpt["excerpt_id"],
        "tp": len(pairs),
        "fp": len(fp_items),
        "fn": len(fn_items),
        "tp_ids": [
            {"gold": _obs_record(excerpt["gold"][gi]),
             "pred": _obs_record(predictions[pi])}
            for gi, pi in pairs
        ],
        "fp_detail": _dedup_reasons(fp_items),
        "fn_detail": _dedup_reasons(fn_items),
    }


def _key_of_valid(o: dict) -> str:
    k = observation_key(o)
    return k if k is not None else "<malformed>"


def _obs_record(obs: dict) -> dict:
    otype = obs.get("observation_type")
    return {
        "observation_type": otype if otype in KNOWN_TYPES else f"<invalid:{otype!r}>",
        "key": _key_of_valid(obs),
    }


def _fp_record(pred: dict, golds: list[dict]) -> dict:
    """FP reason per rubric: did its key match a gold of ANOTHER type?"""
    rec = _obs_record(pred)
    ptype = pred.get("observation_type")
    pkey = observation_key(pred)
    rec["reason"] = (
        "type_mismatch_vs_gold"
        if pkey is not None and any(
            g.get("observation_type") != ptype and observation_key(g) == pkey
            for g in golds)
        else "extra_no_gold"
    )
    return rec


def _gold_reason(gold: dict, predictions: list[dict]) -> dict:
    """FN reason codes per rubric: why did this gold go unanswered?"""
    gtype = gold["observation_type"]
    gkey = observation_key(gold)
    same_key_other_type = any(
        p.get("observation_type") != gtype and observation_key(p) is not None
        and observation_key(p) == gkey
        for p in predictions
    )
    same_type_wrong_key = any(
        p.get("observation_type") == gtype
        and observation_key(p) is not None
        and observation_key(p) != gkey
        for p in predictions
    )
    if same_key_other_type:
        reason = "type_mismatch_in_predictions"
    elif same_type_wrong_key:
        reason = "key_mismatch_same_type"
    else:
        reason = "missing_no_candidate"
    return {"observation_type": gtype, "key": gkey, "reason": reason}


def _dedup_reasons(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def _rates(tp: int, fp: int, fn: int) -> dict:
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None
        and (precision + recall) > 0
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def summarize(graded: list[dict], n_errors: int = 0,
              extractor_name: str = "") -> dict:
    """Aggregate graded excerpts into the detail dict the scoreboard prints."""
    tp = sum(g["tp"] for g in graded)
    fp = sum(g["fp"] for g in graded)
    fn = sum(g["fn"] for g in graded)
    n_pred = sum(g["tp"] + g["fp"] for g in graded)
    n_gold = sum(g["tp"] + g["fn"] for g in graded)

    per_type: dict[str, dict] = {}
    for otype in KNOWN_TYPES:
        t_tp = sum(
            pair["gold"]["observation_type"] == otype
            for g in graded for pair in g["tp_ids"])
        t_fp = sum(
            rec["observation_type"] == otype
            for g in graded for rec in g["fp_detail"])
        t_fn = sum(
            rec["observation_type"] == otype
            for g in graded for rec in g["fn_detail"])
        entry = {
            "gold": t_tp + t_fn,
            "predictions": t_tp + t_fp,
            "tp": t_tp, "fp": t_fp, "fn": t_fn,
        }
        entry.update(_rates(t_tp, t_fp, t_fn))
        per_type[otype] = entry

    discrepancies = [
        {"excerpt_id": g["excerpt_id"], "fp": g["fp_detail"], "fn": g["fn_detail"]}
        for g in graded if g["fp"] or g["fn"]
    ]
    summary = {
        "extractor": extractor_name,
        "n_excerpts": len(graded),
        "n_error_excerpts": n_errors,
        "n_gold": n_gold,
        "n_predictions": n_pred,
        "tp": tp, "fp": fp, "fn": fn,
    }
    summary.update(_rates(tp, fp, fn))
    summary["per_type"] = per_type
    summary["discrepancies"] = discrepancies
    return summary
