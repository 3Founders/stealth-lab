"""
Which sandbox runs a Way's scripts, and what it costs to choose.

The choice is a deployment decision, not a per-person one: a person cannot pick "run my code on Modal" from a
browser, because the code runs on the server and the credentials are the server's. What a person *can* do is see
which one is active and why. That is what this endpoint answers, and it is also what the desktop's setting reads.

The defaults here are the ones docs/pricing_and_sandbox_per_way.md §4.1 argues for, and the reasoning is worth
repeating because it is counter-intuitive: **Modal is the fallback even though Agent37 is ~36× cheaper**, because
Modal's default instance is gVisor with egress blocked, while Agent37 ships a container with open egress. Script
steps run code written by strangers who paid money, so isolation outranks the bill.
"""
from __future__ import annotations

import os
from typing import Any

from app.submissions import sandbox


def _truthy(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def describe() -> dict[str, Any]:
    """The active sandbox, the alternatives, and what each one would cost per run.

    Per-run figures are derived from published rates and a 120-second script (the default timeout), so they answer
    "is this a rounding error or a line item". They are estimates from a rate card, not a quote.
    """
    active = sandbox.provider()
    agent37_ready = bool(sandbox._agent37_key())          # noqa: SLF001 -- same module, the key is the readiness test
    modal_ready = sandbox.configured()

    return {
        "active": active or None,
        "configured": bool(active),
        "script_timeout_s": sandbox.SCRIPT_TIMEOUT_S,
        # The one variable that changes the choice, so a deployment can switch without a code change.
        "switch_with": "SUBMISSION_SANDBOX=agent37 | modal",
        "options": [
            {
                "id": "agent37",
                "label": "Agent37",
                "ready": agent37_ready,
                "is_default": True,
                "isolation": "container by default; gVisor available as an option",
                "network": "open by default; this module asks for deny-all",
                "cost_per_2min_run_usd": 0.0002,
                "cost_note": "The default: ~36x cheaper sustained and 13x faster wake. We close egress explicitly.",
                "chosen_when": "AGENT37_API_KEY is set (the default) and SUBMISSION_SANDBOX is not modal",
            },
            {
                "id": "modal",
                "label": "Modal",
                "ready": modal_ready,
                "is_default": False,
                "isolation": "gVisor (its default runtime)",
                "network": "blocked per sandbox (block_network=True)",
                "cost_per_2min_run_usd": 0.0069,
                "cost_note": "Stronger default isolation and no egress unless asked; choose it when that matters more.",
                "chosen_when": "SUBMISSION_SANDBOX=modal, or Agent37 is not configured",
            },
        ],
        "unchosen_reason": None if active else
            "No sandbox is configured, so a script step fails with a sentence saying so. Set AGENT37_API_KEY, "
            "or MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.",
        "deny_egress_on_agent37": (os.environ.get("AGENT37_NETWORK") or "deny-all").strip().lower() == "deny-all",
    }
