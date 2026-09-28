"""Vertex chat endpoint URLs: `global` (where gemini-3.x Flash answers) uses the bare host; regional
locations keep the regional host; with no LLM location configured nothing changes."""
import pytest

from app.services.vertex_endpoints import llm_location, openapi_base

P = "my-project"


def test_regional_location_uses_the_regional_host():
    assert openapi_base(P, "us-central1") == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/my-project/locations/us-central1/endpoints/openapi")


def test_global_location_uses_the_bare_host_not_global_dash_aiplatform():
    url = openapi_base(P, "global")
    assert url == "https://aiplatform.googleapis.com/v1/projects/my-project/locations/global/endpoints/openapi"
    assert "global-aiplatform" not in url


@pytest.mark.parametrize("region,llm,expected", [
    ("us-central1", "", "us-central1"),          # unset -> unchanged behaviour
    ("us-central1", "   ", "us-central1"),
    ("us-central1", "global", "global"),         # explicit LLM location wins
    ("asia-south1", "us-central1", "us-central1"),
])
def test_llm_location_falls_back_to_the_region(region, llm, expected):
    assert llm_location(region, llm) == expected


def test_the_vertex_clients_build_their_urls_through_the_helper(monkeypatch):
    """Both call sites must use the helper: a copy of the old f-string would break `global` in one of them."""
    import inspect

    from app.services import ingestion_jobs
    from app.services.semantic import providers

    for mod in (ingestion_jobs, providers):
        src = inspect.getsource(mod)
        assert "openapi_base(" in src and "-aiplatform.googleapis.com/v1/" not in src.replace(
            "aiplatform.googleapis.com/v1/projects/", "")
