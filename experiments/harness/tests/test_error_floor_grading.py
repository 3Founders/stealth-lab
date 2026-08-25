"""Unit tests for the error-floor match rules and confusion accounting.

Each test cites the rubric clause it pins (fixtures/error_floor/_rubric.md).
The rules are the contract; if one of these fails, the RUBRIC changed and
the fixtures must be re-agreed, not the assertion relaxed.
"""
import pytest

import error_floor as ef


def obs(otype, key_field, key_value, label=None):
    label = label or f"{otype} {key_value}"
    return {"observation_type": otype, "label": label,
            "properties": {key_field: key_value}}


def file_obs(path):
    return obs("file_touched", "file_path", path)


def cmd_obs(otype, command):
    return obs(otype, "command", command)


def gold_excerpt(golds):
    return {"excerpt_id": "ef-test-001",
            "trace_event": {"event_id": "ev-t", "tool_name": "Bash"},
            "gold": golds, "notes": "test-local excerpt"}


class TestNormalization:
    def test_path_separators_and_dot_slash(self):
        assert ef.normalize_path("src\\auth\\tokens.py") == "src/auth/tokens.py"
        assert ef.normalize_path("./src/config.py") == "src/config.py"
        assert ef.normalize_path("././a/b") == "a/b"

    def test_command_whitespace_collapse(self):
        assert ef.normalize_command("  git   commit\t-m\nx ") == "git commit -m x"

    def test_stopwords_dropped_from_labels(self):
        assert ef.label_tokens("The authentication implementation was modified") == \
            frozenset({"authentication", "implementation", "modified"})

    def test_semantic_threshold_boundary_is_inclusive(self):
        # {a,b} vs {a,b,c,d}: Jaccard = 2/4 = 0.5 exactly -> matches.
        assert ef.semantic_match("alpha beta", "alpha beta gamma delta")
        # 1/3 < 0.5 -> does not.
        assert not ef.semantic_match("alpha beta", "beta gamma")


class TestMatchRules:
    def test_same_key_different_type_never_matches(self):
        g = cmd_obs("commit_made", "git commit -m x")
        p = cmd_obs("command_executed", "git commit -m x")
        assert not ef.observations_match(g, p)

    def test_typed_keys_compare_normalized(self):
        g = file_obs("src/auth/tokens.py")
        p = file_obs("src\\auth\\tokens.py")
        assert ef.observations_match(g, p)

    def test_semantic_compares_by_jaccard_not_string_equality(self):
        g = obs("semantic_label", None, None,
                label="authentication implementation was modified")
        p = obs("semantic_label", None, None,
                label="the authentication implementation was modified today")
        assert ef.observations_match(g, p)
        p2 = obs("semantic_label", None, None, label="tests were refactored")
        assert not ef.observations_match(g, p2)


class TestConfusionAccounting:
    def test_perfect_extractor_scores_clean(self):
        golds = [file_obs("a.py"), cmd_obs("test_run", "pytest -q")]
        g = ef.grade_excerpt(gold_excerpt(golds), [dict(x) for x in golds])
        assert (g["tp"], g["fp"], g["fn"]) == (2, 0, 0)

    def test_duplicate_predictions_pay_precision_only(self):
        golds = [file_obs("a.py")]
        preds = [file_obs("a.py"), file_obs("a.py")]
        g = ef.grade_excerpt(gold_excerpt(golds), preds)
        assert (g["tp"], g["fp"], g["fn"]) == (1, 1, 0)

    def test_type_mismatch_yields_fn_plus_fp_with_mirror_reasons(self):
        golds = [cmd_obs("commit_made", "cd x && git commit -m m")]
        preds = [cmd_obs("command_executed", "cd x && git commit -m m")]
        g = ef.grade_excerpt(gold_excerpt(golds), preds)
        assert (g["tp"], g["fp"], g["fn"]) == (0, 1, 1)
        assert g["fp_detail"][0]["reason"] == "type_mismatch_vs_gold"
        assert g["fn_detail"][0]["reason"] == "type_mismatch_in_predictions"

    def test_key_mismatch_reason(self):
        golds = [file_obs("wanted.py")]
        preds = [file_obs("other.py")]
        g = ef.grade_excerpt(gold_excerpt(golds), preds)
        assert g["fn_detail"][0]["reason"] == "key_mismatch_same_type"
        assert g["fp_detail"][0]["reason"] == "extra_no_gold"

    def test_silence_is_missing_no_candidate(self):
        g = ef.grade_excerpt(gold_excerpt([file_obs("a.py")]), [])
        assert (g["tp"], g["fp"], g["fn"]) == (0, 0, 1)
        assert g["fn_detail"][0]["reason"] == "missing_no_candidate"

    def test_greedy_matching_is_first_fit_in_gold_order(self):
        golds = [file_obs("first.py"), file_obs("second.py")]
        preds = [file_obs("second.py")]
        g = ef.grade_excerpt(gold_excerpt(golds), preds)
        # first.py unanswered (FN), second.py matched by the single pred.
        assert (g["tp"], g["fp"], g["fn"]) == (1, 0, 1)
        assert g["tp_ids"][0]["gold"]["key"] == "second.py"


class TestSummarize:
    def test_rates_and_zero_denominators(self):
        graded = [
            ef.grade_excerpt(gold_excerpt([file_obs("a.py")]),
                             [file_obs("a.py"), file_obs("stray.py")]),
            ef.grade_excerpt(
                gold_excerpt([obs("semantic_label", None, None, label="x done")]),
                []),
        ]
        s = ef.summarize(graded, extractor_name="unit")
        assert (s["tp"], s["fp"], s["fn"]) == (1, 1, 1)
        assert s["precision"] == 0.5 and s["recall"] == 0.5
        sem = s["per_type"]["semantic_label"]
        assert sem["precision"] is None and sem["recall"] == 0.0
        # FP attributed to the PREDICTION's type, FN to the GOLD's type.
        assert s["per_type"]["file_touched"]["predictions"] == 2
        assert s["per_type"]["semantic_label"]["gold"] == 1

    def test_f1_none_when_both_rates_none(self):
        graded = [ef.grade_excerpt(
            gold_excerpt([obs("semantic_label", None, None, label="y")]), [])]
        s = ef.summarize(graded)
        assert s["f1"] is None

    def test_discrepancies_list_only_dirty_excerpts(self):
        clean = ef.grade_excerpt(gold_excerpt([file_obs("a.py")]),
                                 [file_obs("a.py")])
        dirty = ef.grade_excerpt(gold_excerpt([]), [file_obs("b.py")])
        s = ef.summarize([clean, dirty])
        assert [d["excerpt_id"] for d in s["discrepancies"]] == ["ef-test-001"]
