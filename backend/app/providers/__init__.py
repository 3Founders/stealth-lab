"""Model and agent providers behind one calling interface (see service.py for the order of checks).

    from app.providers import call_unit, CallRequest

Layout:  types (shapes) | url_guard (SSRF) | secrets (credential refs) | adapters (one per endpoint
kind) | registry (who may use which connection) | service (call_unit, routing candidates, price sync).
"""
from app.providers.service import ProviderCandidates, call_unit, find_unit, sync_prices, unit_availability  # noqa: F401
from app.providers.types import (CallRequest, CallResult, Connection, ProviderCallDenied, ProviderCallFailed,  # noqa: F401
                                 ProviderError, UnitSpec, unit_of)
