from __future__ import annotations

from pokemon_stealth.knowledge import KnowledgeStore


def test_write_and_load_claim_roundtrip(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    claim = store.write_claim(
        "Water-type moves are highly effective against Brock's Rock-type Pokemon.",
        scope="pokemon-red", tags=["brock", "water"], evidence=["R-0001"],
    )
    assert claim.id == "C-0001"
    loaded = store.load_all_claims()
    assert len(loaded) == 1
    assert loaded[0].statement == claim.statement
    assert loaded[0].evidence == ["R-0001"]


def test_write_claim_dedups_near_identical_statement(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_claim(
        "Water-type moves are highly effective against Brock's Rock-type Pokemon.",
        scope="pokemon-red", tags=["brock"], evidence=["R-0001"],
    )
    second = store.write_claim(
        "Water type moves are highly effective against Brock's Rock type Pokemon",
        scope="pokemon-red", tags=["brock"], evidence=["R-0002"],
    )
    all_claims = store.load_all_claims()
    assert len(all_claims) == 1  # merged, not duplicated
    assert second.id == "C-0001"
    assert set(all_claims[0].evidence) == {"R-0001", "R-0002"}  # evidence merged


def test_write_claim_distinct_statement_gets_new_id(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_claim("Water beats Rock at Brock's gym.", scope="pokemon-red", tags=[], evidence=["R-0001"])
    other = store.write_claim("Pewter Gym must be reached before the Boulder Badge.", scope="pokemon-red", tags=[], evidence=["R-0002"])
    assert other.id == "C-0002"
    assert len(store.load_all_claims()) == 2


def test_write_and_load_procedure_roundtrip(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    proc = store.write_procedure(
        "Beat Brock with Squirtle",
        applicability=["Squirtle starter", "Water Gun available"],
        method=["Heal before entering gym.", "Lead with Squirtle.", "Use Water Gun on Geodude/Onix."],
        verification=["Boulder Badge obtained"],
        known_failures=["entering underleveled"],
        scope="pokemon-red", tags=["brock", "gym"], evidence=["R-0004"],
    )
    loaded = store.load_all_procedures()
    assert len(loaded) == 1
    assert loaded[0].title == "Beat Brock with Squirtle"
    assert loaded[0].method == proc.method
    assert loaded[0].applicability == proc.applicability
    assert loaded[0].known_failures == ["entering underleveled"]


def test_write_and_load_failure_roundtrip(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_failure(
        "Entering Brock's fight with low HP caused a wipe before Onix.",
        scope="pokemon-red", tags=["brock"], evidence=["R-0006"],
    )
    loaded = store.load_all_failures()
    assert len(loaded) == 1
    assert "low HP" in loaded[0].description


def test_retrieve_returns_only_lexically_relevant_items(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_claim("Water beats Rock at Brock's gym.", scope="pokemon-red", tags=["brock"], evidence=["R-1"])
    store.write_claim("Pallet Town has three starter Pokemon.", scope="pokemon-red", tags=["pallet"], evidence=["R-2"])

    claims, procs, fails = store.retrieve("How do I beat Brock's rock type gym?", tags=["brock"])
    assert len(claims) == 1
    assert "Brock" in claims[0].statement


def test_retrieve_returns_empty_when_nothing_matches(tmp_path):
    store = KnowledgeStore(tmp_path / "stealth")
    store.write_claim("Pallet Town has three starter Pokemon.", scope="pokemon-red", tags=["pallet"], evidence=["R-1"])
    claims, procs, fails = store.retrieve("completely unrelated query about spaceships")
    assert claims == []
