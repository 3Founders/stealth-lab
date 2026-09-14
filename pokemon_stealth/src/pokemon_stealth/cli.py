"""CLI entry point (STEP 18)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from pokemon_stealth.config import CHECKPOINTS_DIR, RESULTS_DIR, require_rom_path, rom_sha256
from pokemon_stealth.emulator import PokemonEnv
from pokemon_stealth.experiment import RunConfig, run_experiment, run_one
from pokemon_stealth.agent import Condition
from pokemon_stealth.metrics import learning_curve, load_runs, summarize
from pokemon_stealth.missions import MISSIONS
from pokemon_stealth.state import extract_state

app = typer.Typer(help="Stealth-vs-baseline Pokemon Red experiment harness.")
checkpoint_app = typer.Typer(help="Create/load emulator savestate checkpoints.")
app.add_typer(checkpoint_app, name="checkpoint")


@checkpoint_app.command("create")
def checkpoint_create(
    name: str,
    from_checkpoint: Optional[str] = typer.Option(None, help="Load this existing checkpoint first, then let you play up to the new point via --demo before saving."),
    demo: bool = typer.Option(False, help="Render a window so you can manually play to the checkpoint moment before it's saved."),
):
    """
    Boots the ROM (optionally from an existing checkpoint), optionally with
    a visible window so you can manually play forward, then saves a
    savestate under checkpoints/<name>.state. This harness does not attempt
    autonomous navigation to bootstrap checkpoints (STEP 7's "no fragile
    navigation" applies here too) -- creating S0/S1/S2/S3 is a manual,
    one-time step.
    """
    env = PokemonEnv(headless=not demo)
    env.start()
    if from_checkpoint:
        env.load_state(CHECKPOINTS_DIR / f"{from_checkpoint}.state")
        typer.echo(f"Loaded checkpoint '{from_checkpoint}'.")
    if demo:
        typer.echo("Window open. Play up to the desired point, then press Ctrl+C here to save.")
        try:
            while True:
                env.tick(1)
        except KeyboardInterrupt:
            pass
    env.save_state(CHECKPOINTS_DIR / f"{name}.state")
    typer.echo(f"Saved checkpoint '{name}' -> {CHECKPOINTS_DIR / f'{name}.state'}")
    env.close()


@checkpoint_app.command("load")
def checkpoint_load(name: str, demo: bool = typer.Option(False, help="Open a window to visually confirm.")):
    env = PokemonEnv(headless=not demo)
    env.start()
    env.load_state(CHECKPOINTS_DIR / f"{name}.state")
    state = extract_state(env)
    typer.echo(json.dumps(state.to_llm_dict(), indent=2))
    env.close()


@app.command()
def run(
    mission: str = typer.Option(..., help=f"one of {sorted(MISSIONS)}"),
    condition: str = typer.Option(..., help="fresh|raw_history|notes|stealth"),
    model: str = typer.Option(..., help="e.g. claude-sonnet-5"),
    seed: Optional[int] = typer.Option(None),
    demo: bool = typer.Option(False, help="Render a window (slower, for watching)."),
):
    """Run one mission under one condition."""
    config = RunConfig(mission_id=mission, condition=Condition(condition), model_id=model, seed=seed, headless=not demo)
    metrics = run_one(config)
    typer.echo(json.dumps(metrics.to_row(), indent=2))


@app.command()
def experiment(
    mission: str = typer.Option(...),
    conditions: str = typer.Option("fresh,notes,stealth", help="comma-separated"),
    runs: int = typer.Option(3),
    model: str = typer.Option(...),
    seed_start: int = typer.Option(1),
    extraction_model: Optional[str] = typer.Option(None, help="Model used for post-run knowledge extraction; defaults to --model."),
    demo: bool = typer.Option(False),
):
    """Run a comparison experiment across conditions."""
    condition_list = [c.strip() for c in conditions.split(",") if c.strip()]
    results = run_experiment(
        mission_id=mission, conditions=condition_list, runs=runs, model_id=model,
        seed_start=seed_start, extraction_model_id=extraction_model, headless=not demo,
    )
    typer.echo(f"Completed {len(results)} runs. Summary:")
    rows = [m.to_row() for m in results]
    typer.echo(json.dumps(summarize(rows), indent=2))


@app.command()
def teacher_student(
    mission: str = typer.Option(...),
    teacher_model: str = typer.Option(...),
    student_model: str = typer.Option(...),
    teacher_runs: int = typer.Option(3),
    student_runs: int = typer.Option(3),
):
    """
    STEP 23: teacher_model runs the STEALTH condition first (accumulating
    Claims/Procedures into stealth/), then student_model runs STEALTH on
    the SAME mission with a fresh session, reading what the teacher wrote.
    """
    typer.echo(f"Teacher phase: {teacher_model} x{teacher_runs}")
    teacher_results = run_experiment(mission_id=mission, conditions=["stealth"], runs=teacher_runs, model_id=teacher_model)
    typer.echo(f"Student phase: {student_model} x{student_runs}")
    student_results = run_experiment(mission_id=mission, conditions=["stealth"], runs=student_runs, model_id=student_model)
    typer.echo(json.dumps({
        "teacher": summarize([m.to_row() for m in teacher_results]),
        "student": summarize([m.to_row() for m in student_results]),
    }, indent=2))


@app.command()
def summary(results_dir: Path = RESULTS_DIR):
    """Print STEP 20 comparison summary from results/runs.jsonl."""
    runs = load_runs(results_dir)
    typer.echo(json.dumps(summarize(runs), indent=2))


@app.command()
def curve(mission: str, condition: str = "stealth", results_dir: Path = RESULTS_DIR):
    """Print STEP 21 sequential learning-curve data for one mission/condition."""
    runs = load_runs(results_dir)
    typer.echo(json.dumps(learning_curve(runs, mission, condition), indent=2))


@app.command()
def verify_memory_map(demo: bool = typer.Option(False)):
    """
    Load the ROM at power-on (no checkpoint) and print decoded state next
    to what a fresh New Game should show, so a wrong memory_map.py offset
    is caught immediately instead of trusted blindly.
    """
    path = require_rom_path()
    typer.echo(f"ROM: {path}  sha256={rom_sha256(path)}")
    env = PokemonEnv(headless=not demo)
    env.start()
    env.tick(60)
    typer.echo(f"Cartridge title (header, hardware-verified): {env.cartridge_title()!r}")
    state = extract_state(env)
    typer.echo("Decoded state at power-on (expect map_id=Title-screen/intro state, not yet New-Game):")
    typer.echo(json.dumps(state.to_llm_dict(), indent=2))
    env.close()


if __name__ == "__main__":
    app()
