"""
Real PyBoy + real ROM smoke tests. Skip themselves (see conftest.py) unless
POKEMON_RED_ROM points at a real, legally obtained ROM file and pyboy is
installed. Run locally with:

    POKEMON_RED_ROM=/path/to/pokemon_red.gb pytest tests/test_emulator_actions_e2e.py -q
"""
from __future__ import annotations

from conftest import requires_rom

from pokemon_stealth.emulator import PokemonEnv
from pokemon_stealth.state import extract_state


@requires_rom
def test_env_boots_and_ticks():
    env = PokemonEnv(headless=True)
    env.start()
    env.tick(60)
    assert env.frame_count == 60
    env.close()


@requires_rom
def test_cartridge_title_is_pokemon_red():
    env = PokemonEnv(headless=True)
    env.start()
    title = env.cartridge_title()
    assert "POKEMON" in title.upper()
    env.close()


@requires_rom
def test_extract_state_returns_real_values_without_crashing():
    env = PokemonEnv(headless=True)
    env.start()
    env.tick(60)
    state = extract_state(env)
    assert isinstance(state.map_id, int)
    assert isinstance(state.position, tuple)
    env.close()


@requires_rom
def test_save_and_load_state_roundtrip(tmp_path):
    env = PokemonEnv(headless=True)
    env.start()
    env.tick(60)
    before = extract_state(env)
    path = tmp_path / "test.state"
    env.save_state(path)
    env.tick(120)  # advance so state actually would differ if load didn't work
    env.load_state(path)
    after = extract_state(env)
    assert before.map_id == after.map_id
    assert before.position == after.position
    env.close()
