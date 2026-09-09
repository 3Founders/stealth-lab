"""Unit tests for the declarative ablation matrix (spec section 25)."""
import ablation_config


def test_matrix_covers_the_smallest_sensible_ladder():
    assert set(ablation_config.ABLATION_MATRIX) == {"A", "C", "E", "F", "G"}
    assert ablation_config.smallest_sensible_matrix() == ["A", "C", "E", "F", "G"]


def test_capability_delta_for_c_is_its_full_capability_set_since_a_has_none():
    delta = ablation_config.capability_delta("C")
    assert delta == {"retrieval", "applicability", "procedure_reuse"}


def test_capability_delta_for_e_is_exactly_decomposition():
    assert ablation_config.capability_delta("E") == {"decomposition"}


def test_capability_delta_for_f_is_exactly_implementation_selection():
    assert ablation_config.capability_delta("F") == {"implementation_selection"}


def test_capability_delta_for_g_is_both_e_and_f_capabilities_since_g_builds_on_c():
    assert ablation_config.capability_delta("G") == {"decomposition", "implementation_selection"}


def test_g_is_the_union_of_every_capability_across_the_matrix():
    all_caps = set()
    for cfg in ablation_config.ABLATION_MATRIX.values():
        all_caps |= cfg.capabilities
    assert ablation_config.ABLATION_MATRIX["G"].capabilities == all_caps


def test_e_and_f_each_name_the_new_machinery_their_capability_would_require():
    assert ablation_config.ABLATION_MATRIX["E"].new_machinery_required
    assert ablation_config.ABLATION_MATRIX["F"].new_machinery_required
    assert ablation_config.ABLATION_MATRIX["A"].new_machinery_required == ()
