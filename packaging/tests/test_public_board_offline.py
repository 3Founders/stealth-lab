"""Offline proving tests for the P5 public scoreboard generator
(stealthlab_connect.scoreboard_gen).

No network, no database, no paid calls: synthetic results/spend rows in
tmp_path, hand-computed expectations for every number that reaches the
public page, and the board rules pinned structurally:
  - every exact-p= on the page travels with its discordant-pair counts
  - spend line present / honestly absent
  - generated timestamp on both artifacts
  - unusable tasks disclosed, never silently dropped
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stealthlab_connect.scoreboard_gen as sg


# --------------------------------------------------------------------------
# Synthetic data helpers (episode schema mirrors scripted_arms._base_episode)
# --------------------------------------------------------------------------

def _ep(task_id, arm, *, resolved=False, valid=True, served="ox-alpha",
        reused=(), followed=(), refused=(), offered=(),
        reuse_caused_failure=False, unseen=False, tin=0, tout=0):
    return {
        "task_id": task_id,
        "arm": arm,
        "valid": valid,
        "invalid_reason": None if valid else "unparseable_decision_after_repair",
        "resolved": resolved,
        "reused_procedure_ids": list(reused),
        "followed_memory_ids": list(followed),
        "refused_procedure_ids": list(refused),
        "reuse_caused_failure": reuse_caused_failure,
        "stale_offered": list(offered),
        "tokens_in": tin,
        "tokens_out": tout,
        "tool_calls": 2,
        "latency_seconds": 1.0,
        "human_interventions": 0,
        "unseen_task": unseen,
        "served_by_model": served,
    }


def _row(task_id, a, b, c):
    return {"task_id": task_id, "A": a, "B": b, "C": c}


PASS = dict(resolved=True, tin=100_000, tout=50_000)
FAIL = dict(resolved=False, tin=20_000, tout=10_000)


def scenario_a_rows():
    """4 usable tasks, hand-checked matrix:
       t1: A only resolves | t2: B and C resolve (C refuses the stale offer)
       t3: C only resolves (B follows bad memory -> false reuse) | t4: nobody.
       Pair discordance: A-vs-B 1/1, A-vs-C 1/2, B-vs-C 0/1 (t2 is a
       concordant B,C pass)."""
    return [
        _row("t1", _ep("t1", "A", **PASS), _ep("t1", "B", **FAIL),
             _ep("t1", "C", **FAIL)),
        _row("t2", _ep("t2", "A", **FAIL),
             _ep("t2", "B", resolved=True, tin=100_000, tout=50_000,
                 offered=["p_stale"]),
             _ep("t2", "C", resolved=True, tin=100_000, tout=50_000,
                 offered=["p_stale"], refused=["p_stale"])),
        _row("t3", _ep("t3", "A", **FAIL),
             _ep("t3", "B", followed=["blob-x"],
                 reuse_caused_failure=True, **FAIL),
             _ep("t3", "C", **PASS)),
        _row("t4", _ep("t4", "A", **FAIL), _ep("t4", "B", **FAIL),
             _ep("t4", "C", **FAIL)),
    ]


def scenario_big_rows():
    """11 usable tasks: t1 A-only win; t2..t11 C-only wins.
       A-vs-C: 1 first-only vs 10 second-only -> exact two-sided p =
       24/2048 = 0.01171875 (significant). B-vs-C: 0 vs 10 ->
       2/1024 = 0.001953125. A-vs-B: n=1 -> small-n caveat territory."""
    rows = [_row("t1", _ep("t1", "A", **PASS), _ep("t1", "B", **FAIL),
                 _ep("t1", "C", **FAIL))]
    for i in range(2, 12):
        tid = f"t{i}"
        rows.append(_row(tid, _ep(tid, "A", **FAIL), _ep(tid, "B", **FAIL),
                         _ep(tid, "C", **PASS)))
    return rows


@pytest.fixture()
def fixtures_dir(tmp_path):
    d = tmp_path / "fixtures" / "micro"
    d.mkdir(parents=True)
    (d / "procedures.json").write_text(json.dumps({
        "procedures": [
            {"procedure_id": "p_stale", "stale": True},
            {"procedure_id": "p_good", "stale": False},
        ]
    }), encoding="utf-8")
    return d


FIXED_TS = "2026-08-26T00:00:00+00:00"


def _model(rows, fixtures_dir, spend=None, **kw):
    return sg.build_model(
        rows, spend, generated_at=FIXED_TS,
        results_source="real_arms_results.jsonl",
        spend_source="real_arms_spend.jsonl",
        fixtures_dir=fixtures_dir, **kw)


# --------------------------------------------------------------------------
# Harness-root discovery
# --------------------------------------------------------------------------

def test_find_harness_root_explicit_path():
    root = sg.find_harness_root(Path(sg.__file__).parents[3] / "experiments" / "harness")
    assert (root / "scoreboard.py").is_file()


def test_find_harness_root_env_override(monkeypatch):
    real = Path(sg.__file__).parents[3] / "experiments" / "harness"
    monkeypatch.setenv(sg.ENV_HARNESS_ROOT, str(real))
    assert sg.find_harness_root() == real.resolve()


def test_find_harness_root_bad_env_names_the_var(monkeypatch, tmp_path):
    monkeypatch.setenv(sg.ENV_HARNESS_ROOT, str(tmp_path))
    with pytest.raises(sg.HarnessRootNotFound, match="STEALTHLAB_HARNESS_ROOT"):
        sg.find_harness_root()


def test_ensure_harness_importable_is_idempotent():
    root = sg.find_harness_root()
    sg.ensure_harness_importable(root)
    sg.ensure_harness_importable(root)
    assert sum(1 for p in sys.path if p == str(root.resolve())) == 1


# --------------------------------------------------------------------------
# JSONL readers + spend-path resolution
# --------------------------------------------------------------------------

def test_load_jsonl_skips_torn_lines_and_non_objects(tmp_path):
    p = tmp_path / "r.jsonl"
    good = json.dumps({"task_id": "t"})
    torn = '{"task_id": "te'
    p.write_text(f"{good}\n{torn}\n\n[1,2]\n{good}\n", encoding="utf-8")
    rows = sg.load_jsonl(p)
    assert rows == [{"task_id": "t"}, {"task_id": "t"}]


def test_load_jsonl_none_is_empty():
    assert sg.load_jsonl(None) == []


def test_default_spend_path_prefers_code_default_then_alt_names(tmp_path):
    res = tmp_path / "real_arms_results.jsonl"
    res.write_text("{}\n", encoding="utf-8")
    code_default = tmp_path / "real_arms_results_spend.jsonl"
    gitignore_name = tmp_path / "real_arms_spend.jsonl"
    run1_name = tmp_path / sg.ALT_SPEND_NAME
    assert sg.default_spend_path(res) == code_default  # none exist: canonical
    gitignore_name.write_text("{}\n", encoding="utf-8")
    assert sg.default_spend_path(res) == gitignore_name
    run1_name.write_text("{}\n", encoding="utf-8")
    assert sg.default_spend_path(res) == gitignore_name  # sibling name first
    gitignore_name.unlink()
    assert sg.default_spend_path(res) == run1_name
    code_default.write_text("{}\n", encoding="utf-8")
    assert sg.default_spend_path(res) == code_default


# --------------------------------------------------------------------------
# Transformation math (hand-computed)
# --------------------------------------------------------------------------

def test_arm_stats_match_hand_computed_matrix(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    stats = model["arms_stats"]
    assert [stats[a]["n"] for a in "ABC"] == [4, 4, 4]
    assert [(stats[a]["passes"], stats[a]["pass_rate"]) for a in "ABC"] == \
        [(1, 0.25), (1, 0.25), (2, 0.5)]
    # cost: pass = 100k*2.5/1e6 + 50k*10/1e6 = $0.75; fail = $0.15 each
    assert stats["A"]["total_cost_usd"] == pytest.approx(0.75 + 3 * 0.15)
    assert stats["A"]["mean_cost_usd"] == pytest.approx(1.2 / 4)
    # stale-refusal columns keep numerator/denominator discipline
    assert stats["B"]["stale_opportunities"] == 1
    assert stats["C"]["stale_refusals_correct"] == 1
    assert stats["C"]["stale_refusals_missed"] == 0
    assert stats["B"]["false_reuse_count"] == 1


def test_discordant_counts_and_p_values_exact(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    by_pair = {(c["first"], c["second"]): c for c in model["comparisons"]}
    ab, ac, bc = by_pair[("A", "B")], by_pair[("A", "C")], by_pair[("B", "C")]
    assert (ab["discordant_first_only"], ab["discordant_second_only"]) == (1, 1)
    assert (ac["discordant_first_only"], ac["discordant_second_only"]) == (1, 2)
    assert (bc["discordant_first_only"], bc["discordant_second_only"]) == (0, 1)
    # hand-derived: n=2 split 1-1 -> p=1.0; n=1 -> p=1.0
    assert ab["p"] == pytest.approx(1.0)
    assert bc["p"] == pytest.approx(1.0)


def test_significant_pair_and_small_n_caveat(fixtures_dir):
    model = _model(scenario_big_rows(), fixtures_dir)
    by_pair = {(c["first"], c["second"]): c for c in model["comparisons"]}
    assert by_pair[("A", "C")]["p"] == pytest.approx(0.01171875)
    assert by_pair[("B", "C")]["p"] == pytest.approx(0.001953125)
    assert by_pair[("A", "B")]["n_discordant"] == 1
    md = sg.render_markdown(model)
    html_page = sg.render_html(model)
    assert "0.0117" in md and "exact-p=" in md
    caveat = [c for c in model["caveats"] if "small-n" in c]
    assert caveat and "A vs B" in caveat[0]
    # board rule pinned structurally: every exact-p travels with its counts,
    # on the markdown page AND inside the escaped HTML.
    for page in (md, html_page):
        # every rendering of a comparison carries its counts: bullets(3) +
        # POWER-ANALYSIS FOOTER(3) + the embedded canonical terminal
        # rendering's own footer(3). A bare p-value has no path to the page.
        p_lines = [ln for ln in page.splitlines() if "exact-p=" in ln]
        assert len(p_lines) == 9
        assert all("discordant pairs" in ln for ln in p_lines)
        assert "POWER-ANALYSIS FOOTER" in page


def test_unusable_task_excluded_and_disclosed(fixtures_dir):
    rows = scenario_a_rows()
    broken = _ep("t_broken", "B", valid=False)
    rows.append(_row("t_broken", _ep("t_broken", "A", **PASS), broken,
                     _ep("t_broken", "C", **PASS)))
    model = _model(rows, fixtures_dir)
    assert model["counts"]["tasks_classified"] == 5
    assert model["counts"]["usable"] == 4
    assert model["counts"]["excluded"] == 1
    stats = model["arms_stats"]
    assert all(stats[a]["n"] == 4 for a in "ABC")
    assert any("excluded from the paired statistics" in c
               for c in model["caveats"])


def test_error_row_counted_never_scored(fixtures_dir):
    rows = scenario_a_rows()
    rows.append({"task_id": "t_err", "error": "AllModelsFailedError: boom",
                 "traceback": "..."})
    model = _model(rows, fixtures_dir)
    assert model["counts"]["tasks_with_error"] == 1
    assert model["counts"]["usable"] == 4
    assert any("runner-level error" in c for c in model["caveats"])


# --------------------------------------------------------------------------
# Spend line
# --------------------------------------------------------------------------

def _spend_rows():
    return [
        {"ts": 1.0, "task_id": "t1", "arm": "A", "model": "ox-alpha",
         "attempt": 0, "status": 429, "error": None, "latency_s": 0.2,
         "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        {"ts": 2.0, "task_id": "t1", "arm": "A", "model": "ox-alpha",
         "attempt": 1, "status": None, "error": "ConnectError: timeout",
         "latency_s": 30.0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        {"ts": 3.0, "task_id": "t1", "arm": "A", "model": "ox-alpha",
         "attempt": 2, "status": 200, "error": None, "latency_s": 1.5,
         "tokens_in": 100_000, "tokens_out": 50_000, "cost_usd": 0.75},
        {"ts": 4.0, "task_id": "t1", "arm": "C", "model": "ox-alpha",
         "attempt": 0, "status": 200, "error": None, "latency_s": 1.1,
         "tokens_in": 10_000, "tokens_out": 5_000, "cost_usd": 0.075},
    ]


def test_spend_aggregation_matches_runner_semantics(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir, spend=_spend_rows())
    sp = model["spend"]
    assert sp["present"]
    s = sp["summary"]
    assert s["attempts"] == 4
    assert s["billed_calls"] == 2
    assert s["failed_attempts"] == 2
    assert s["tokens_in"] == 110_000
    assert s["tokens_out"] == 55_000
    assert s["cost_usd"] == pytest.approx(0.825)
    assert sp["count_429"] == 1
    assert sp["cost_by_arm_usd"] == {"A": pytest.approx(0.75),
                                     "C": pytest.approx(0.075)}
    md = sg.render_markdown(model)
    assert "SPEND:" in md and "$0.8250" in md and "(2 billed, 2 failed)" in md
    assert "HTTP 429) attempts: 1" in md
    html_page = sg.render_html(model)
    assert "SPEND:" in html_page


def test_missing_spend_renders_honest_absence(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir, spend=None)
    assert not model["spend"]["present"]
    md = sg.render_markdown(model)
    html_page = sg.render_html(model)
    for page in (md, html_page):
        assert "honestly absent" in page
        assert "$0.0000" not in page  # never a fabricated zero-cost claim


# --------------------------------------------------------------------------
# Page assembly rules
# --------------------------------------------------------------------------

def test_generated_timestamp_on_both_pages(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    md = sg.render_markdown(model)
    html_page = sg.render_html(model)
    assert f"Generated: {FIXED_TS}" in md
    assert FIXED_TS in html_page
    assert 'name="generated-at"' in html_page


def test_footer_block_lists_all_three_pairs(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    md = sg.render_markdown(model)
    assert "A vs B" in md and "A vs C" in md and "B vs C" in md
    assert "POWER-ANALYSIS FOOTER" in md
    assert "(exact McNemar, alpha=0.05, target power=0.8)" in md


def test_canonical_terminal_rendering_embedded(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    md = sg.render_markdown(model)
    assert model["canonical_text"] in md


def test_html_escapes_model_controlled_text(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir,
                   title="<script>alert(1)</script>")
    html_page = sg.render_html(model)
    assert "<script>alert(1)</script>" not in html_page
    assert "&lt;script&gt;" in html_page


def test_models_seen_listed_from_valid_episodes(fixtures_dir):
    model = _model(scenario_a_rows(), fixtures_dir)
    assert model["models_seen"] == ["ox-alpha"]


def test_zero_discordant_pairs_render_note_not_p(fixtures_dir):
    rows = [_row("t1", _ep("t1", "A", **PASS), _ep("t1", "B", **PASS),
                 _ep("t1", "C", **PASS))]
    model = _model(rows, fixtures_dir)
    md = sg.render_markdown(model)
    assert "p=N/A" in md
    assert "no discordant pairs" in md


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@pytest.fixture()
def sweep_files(tmp_path, fixtures_dir):
    results = tmp_path / "real_arms_results.jsonl"
    results.write_text(
        "".join(json.dumps(r) + "\n" for r in scenario_a_rows()),
        encoding="utf-8")
    spend = tmp_path / "real_arms_spend.jsonl"
    spend.write_text(
        "".join(json.dumps(r) + "\n" for r in _spend_rows()),
        encoding="utf-8")
    out_dir = tmp_path / "out"
    return {"results": results, "spend": spend, "out": out_dir,
            "fixtures": fixtures_dir}


def test_cli_writes_both_static_pages(sweep_files):
    rc = sg.main([
        "--results", str(sweep_files["results"]),
        "--spend", str(sweep_files["spend"]),
        "--fixtures-dir", str(sweep_files["fixtures"]),
        "--out-dir", str(sweep_files["out"]),
        "--generated-at", FIXED_TS,
        "--title", "SS40 Public Scoreboard",
    ])
    assert rc == 0
    md = (sweep_files["out"] / sg.MD_FILENAME).read_text(encoding="utf-8")
    html_page = (sweep_files["out"] / sg.HTML_FILENAME).read_text(
        encoding="utf-8")
    assert f"Generated: {FIXED_TS}" in md
    assert "POWER-ANALYSIS FOOTER" in md and "SPEND:" in md
    assert "discordant pairs" in md and "exact-p=" in md
    assert FIXED_TS in html_page and "SPEND:" in html_page


def test_cli_refuses_missing_results_no_fake_page(tmp_path, capsys):
    out_dir = tmp_path / "out"
    rc = sg.main(["--results", str(tmp_path / "absent.jsonl"),
                  "--out-dir", str(out_dir)])
    assert rc == 2
    assert not (out_dir / sg.MD_FILENAME).exists()
    assert not (out_dir / sg.HTML_FILENAME).exists()
    assert "never generated from absent data" in capsys.readouterr().err


def test_cli_resolves_spend_ledger_without_explicit_flag(sweep_files):
    rc = sg.main([
        "--results", str(sweep_files["results"]),
        "--fixtures-dir", str(sweep_files["fixtures"]),
        "--out-dir", str(sweep_files["out"]),
        "--generated-at", FIXED_TS,
    ])
    assert rc == 0
    md = (sweep_files["out"] / sg.MD_FILENAME).read_text(encoding="utf-8")
    assert "SPEND:" in md  # found via default_spend_path beside --results
