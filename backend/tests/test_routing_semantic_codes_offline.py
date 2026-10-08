"""Semantic codes for Ways: the codebook (build / assign / persist / remap) and how a code reaches the prior."""
from __future__ import annotations

import numpy as np

from app.routing import predict, semantic_codes as sc, service


def _clusters(seed=0, n=60, d=16, k=4):
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(k, d))
    x = np.concatenate([c + 0.05 * rng.normal(size=(n, d)) for c in centres])
    return x, np.repeat(np.arange(k), n)


def test_similar_ways_share_a_code_and_different_ones_do_not():
    x, truth = _clusters()
    cb = sc.build(x, k1=4, k2=2, min_group=10, version="cb-test", embedding_model_id="m")
    codes = cb.assign_many(x)
    for k in range(4):
        tops = {sc.coarse(c) for c, t in zip(codes, truth) if t == k}
        assert len(tops) == 1                                    # one cluster -> one coarse group
    assert len({sc.coarse(c) for c in codes}) == 4
    assert all(cb.assign(v) == c for v, c in zip(x[:10], codes[:10]))
    assert cb.assign([1.0, 2.0]) is None                         # wrong dimension: no code, not a wrong one


def test_codebook_round_trips_and_remaps_to_itself():
    x, _ = _clusters(seed=1)
    cb = sc.build(x, k1=4, k2=3, min_group=10, version="v1", embedding_model_id="m")
    again = sc.Codebook.from_bytes(cb.to_bytes())
    assert again.version == "v1" and again.embedding_model_id == "m"
    assert again.assign_many(x) == cb.assign_many(x)
    mapping = sc.remap(cb, again)
    assert mapping and all(k == v for k, v in mapping.items())


def test_small_groups_get_a_single_sub_code_and_names_parse():
    x, _ = _clusters(n=5)
    cb = sc.build(x, k1=4, k2=8, min_group=20, version="v", embedding_model_id="m")
    assert all(c.endswith(".0") for c in cb.assign_many(x))
    assert sc.parse_code("c07.3") == (7, 3) and sc.parse_code("x") is None
    assert sc.node_id("c07.3", "v") != sc.node_id("c07", "v") != sc.node_id("c07.3", "v2")


def test_a_code_node_becomes_a_parent_of_the_case_prior():
    s, d = 64, 3
    g = predict.Globals(version=0, arrays={"code_x": np.stack([np.full((s, d), 2.0), np.full((s, d), -2.0)], axis=1),
                                           "tau_c": np.ones(s)},
                        meta={"code_nodes": ["c01", "c01.2"], "k": d})
    assert np.allclose(service._code_parent(g, "c01.2"), -2.0)      # its own node when the fit saw it
    assert np.allclose(service._code_parent(g, "c01.5"), 2.0)       # else its coarse group
    assert service._code_parent(g, "c09.1") is None
    assert service._code_parent(g, None) is None
    assert service._code_parent(predict.Globals(0, {"tau_c": np.ones(s)}, {"k": d}), "c01.2") is None
