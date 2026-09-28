"""Vertex AI OpenAI-compatible endpoint URLs.

One place builds the chat endpoint, because the host depends on the location: a regional location
(`us-central1`) is served from `us-central1-aiplatform.googleapis.com`, while `global` -- the only place the
newest Gemini Flash models answer -- is served from the bare `aiplatform.googleapis.com`. Building the regional
host for `global` yields a name that does not resolve.
"""
from __future__ import annotations


def llm_location(vertex_region: str, vertex_llm_location: str = "") -> str:
    """The location chat/extraction models are called in: the explicit LLM location, else the region."""
    return (vertex_llm_location or "").strip() or vertex_region


def openapi_base(project: str, location: str) -> str:
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project}/locations/{location}/endpoints/openapi"
