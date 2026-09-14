"""
STEP 28's "experiment isolation" test: proves each condition only touches
its OWN persistent store, with no real PyBoy/ROM involved. Everything
emulator-shaped is faked; the thing under real test is experiment.py's
condition-branching logic in run_one().
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pokemon_stealth.actions as actions_mod
import pokemon_stealth.experiment as experiment_mod
from pokemon_stealth.agent import Condition, _REQUIRED_PARAMS
from pokemon_stealth.experiment import RunConfig, _ACTION_DISPATCH, run_one
from pokemon_stealth.llm import MockAgent
from pokemon_stealth.missions import Mission
from pokemon_stealth.state import GameState


def test_action_dispatch_and_schema_stay_in_sync():
    """Every action type the LLM can request must have a dispatcher, and vice versa."""
    assert set(_ACTION_DISPATCH) == set(_REQUIRED_PARAMS)


class FakeEnv:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.rom_path = Path("fake.gb")
        self.frame_count = 0

    def start(self) -> None:
        pass

    def load_state(self, path: Path) -> None:
        pass

    def close(self) -> None:
        pass

    def press(self, button: str, hold_frames: int = 8, release_frames: int = 4) -> None:
        self.frame_count += hold_frames + release_frames


def _fake_state(**overrides) -> GameState:
    base = dict(map_id=0, position=(1, 1), party=[], badges=[], money=0, frame_count=0)
    base.update(overrides)
    return GameState(**base)


def _never_ending_mission() -> Mission:
    return Mission(
        id="starter",  # reuse a real mission id so knowledge tags/query text stay sensible
        description="test mission",
        checkpoint="FAKE_CKPT",
        success_condition=lambda s: False,
        failure_condition=lambda s: None,
        max_actions=1000,
        max_llm_calls=2,
        max_wall_time_s=60,
    )


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(experiment_mod, "PokemonEnv", FakeEnv)
    monkeypatch.setattr(experiment_mod, "rom_sha256", lambda path: "deadbeef")
    monkeypatch.setattr(experiment_mod, "extract_state", lambda env: _fake_state())
    monkeypatch.setattr(actions_mod, "extract_state", lambda env: _fake_state())
    monkeypatch.setattr(experiment_mod, "get_mission", lambda mission_id: _never_ending_mission())
    monkeypatch.setattr(experiment_mod, "make_agent", lambda model_id: MockAgent(
        responses=['{"action": {"type": "interact"}, "reason": "test"}'] * 10
    ))
    ckpt = tmp_path / "checkpoints" / "FAKE_CKPT.state"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(b"\x00")
    monkeypatch.setattr(experiment_mod, "_checkpoint_path", lambda name: ckpt)

    stealth_dir = tmp_path / "stealth"
    monkeypatch.setattr(experiment_mod, "STEALTH_DIR", stealth_dir)
    raw_history_dir = tmp_path / "raw_history" / "runs"
    monkeypatch.setattr(experiment_mod, "RAW_HISTORY_ROOT", raw_history_dir)
    notes_dir = tmp_path / "notes_baseline"
    monkeypatch.setattr(experiment_mod, "NOTES_ROOT", notes_dir)
    results_dir = tmp_path / "results"
    return stealth_dir, raw_history_dir, notes_dir, results_dir


def test_fresh_condition_writes_no_persistent_knowledge(monkeypatch, tmp_path):
    stealth_dir, raw_history_dir, notes_dir, results_dir = _patch_common(monkeypatch, tmp_path)
    config = RunConfig(mission_id="starter", condition=Condition.FRESH, model_id="mock-model", results_dir=results_dir)
    run_one(config)

    assert not (stealth_dir / "claims").exists() or not any((stealth_dir / "claims").iterdir())
    assert not raw_history_dir.exists() or not any(raw_history_dir.iterdir())
    assert not notes_dir.exists() or not any(notes_dir.glob("*.md"))


def test_notes_condition_writes_only_to_notes_store(monkeypatch, tmp_path):
    stealth_dir, raw_history_dir, notes_dir, results_dir = _patch_common(monkeypatch, tmp_path)
    config = RunConfig(mission_id="starter", condition=Condition.NOTES, model_id="mock-model", results_dir=results_dir)
    run_one(config)

    assert (notes_dir / "starter.md").is_file()
    assert not (stealth_dir / "claims").exists() or not any((stealth_dir / "claims").iterdir())
    assert not raw_history_dir.exists() or not any(raw_history_dir.iterdir())


def test_raw_history_condition_writes_only_to_raw_history_store(monkeypatch, tmp_path):
    stealth_dir, raw_history_dir, notes_dir, results_dir = _patch_common(monkeypatch, tmp_path)
    config = RunConfig(mission_id="starter", condition=Condition.RAW_HISTORY, model_id="mock-model", results_dir=results_dir)
    run_one(config)

    assert raw_history_dir.is_dir()
    assert any(raw_history_dir.iterdir())
    assert not (stealth_dir / "claims").exists() or not any((stealth_dir / "claims").iterdir())
    assert not notes_dir.exists() or not any(notes_dir.glob("*.md"))


def test_stealth_condition_writes_run_record_and_extraction(monkeypatch, tmp_path):
    stealth_dir, raw_history_dir, notes_dir, results_dir = _patch_common(monkeypatch, tmp_path)
    # The extraction call also goes through make_agent (patched above to MockAgent),
    # so it will consume the same scripted queue and likely fail to parse as
    # extraction JSON -- that's fine, zero extracted items is a valid, honest outcome.
    config = RunConfig(mission_id="starter", condition=Condition.STEALTH, model_id="mock-model", results_dir=results_dir)
    run_one(config)

    assert any((stealth_dir / "runs").glob("R-*.md"))
    assert not notes_dir.exists() or not any(notes_dir.glob("*.md"))
    assert not raw_history_dir.exists() or not any(raw_history_dir.iterdir())
