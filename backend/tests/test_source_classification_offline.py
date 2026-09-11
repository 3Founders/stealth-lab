"""
G4 / B13 -- the deterministic source-content classifier
(`app.services.source_classification.classify_source_content`). Pure.
"""
from __future__ import annotations

from app.services.source_classification import (
    SOURCE_CLASSIFIER_VERSION,
    SOURCE_KINDS,
    classify_source_content,
)


def test_ordered_imperative_steps_are_a_procedure():
    r = classify_source_content(
        "How to migrate the datastore without downtime.",
        name="zero-downtime-migration",
        steps=["Create a backward-compatible schema", "Enable dual-write",
               "Backfill the new column", "Verify data parity", "Cut reads over"],
    )
    assert r["kind"] == "PROCEDURE"
    assert r["confidence"] >= 0.3
    assert r["classifier_version"] == SOURCE_CLASSIFIER_VERSION


def test_api_field_list_with_numbers_is_reference_not_procedure():
    body = (
        "PaymentIntent object.\n\n"
        "Parameters:\n"
        "1. amount -- integer, required. The amount in cents.\n"
        "2. currency -- string, required. Three-letter ISO code.\n"
        "3. metadata -- object, optional. Key-value pairs.\n"
        "Returns: a PaymentIntent. See also: the Charges API. Status codes: 200, 402.\n"
    )
    r = classify_source_content(body, name="payment-intent-reference",
                                steps=["amount -- integer", "currency -- string"])
    assert r["kind"] == "REFERENCE", r


def test_rationale_prose_is_a_claim():
    body = (
        "We decided to use RRF fusion because a linear score blend let one "
        "leg dominate. Empirically, RRF outperforms weighted-sum on our "
        "eval set, and the trade-off in tunability was worth it."
    )
    r = classify_source_content(body, name="why-rrf")
    assert r["kind"] == "CLAIM", r


def test_doc_with_both_steps_and_heavy_reference_is_mixed():
    body = (
        "Configure the exporter.\n\n"
        "Parameters: endpoint (string), timeout (integer), headers (object). "
        "Returns nothing. See also the config schema. Default: 30s. "
        "Environment variables: EXPORTER_URL, EXPORTER_TOKEN.\n"
    )
    r = classify_source_content(
        body, name="exporter",
        steps=["Install the exporter package", "Set EXPORTER_URL", "Run the exporter",
               "Verify metrics arrive"],
    )
    assert r["kind"] in ("MIXED", "PROCEDURE"), r
    assert r["signals"]["reference"] >= 2


def test_empty_input_is_unknown_not_an_error():
    r = classify_source_content("", name="", steps=[])
    assert r["kind"] == "UNKNOWN"
    assert r["confidence"] == 0.0


def test_stepless_but_no_lexical_signal_defers_to_structure():
    r = classify_source_content("Some neutral prose with no markers.", name="x", steps=[])
    assert r["kind"] == "UNKNOWN"
    r2 = classify_source_content("Some neutral prose.", name="x",
                                 steps=["do the first thing", "do the second thing"])
    # verbs present -> PROCEDURE via lexical; if not, structural fallback still PROCEDURE
    assert r2["kind"] == "PROCEDURE"


def test_all_kinds_are_in_the_declared_set():
    for body, steps in [("Parameters: x. Returns: y.", []),
                        ("We decided because reasons.", []),
                        ("Run the thing. Verify it.", ["run x", "verify y"]),
                        ("", [])]:
        assert classify_source_content(body, steps=steps)["kind"] in SOURCE_KINDS
