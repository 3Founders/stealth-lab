"""License decisions per item.

GitHub display names (the `license_name` column of nebius/SWE-rebench) map to SPDX expressions through an exact
table built from every distinct value in the pinned dataset (2026-09-29). A name that does not say which license
it is -- bare "BSD" or "BSD License" (2-, 3- or 4-clause?), "Public Domain", a missing value -- has no entry and
is rejected as `license_unmappable`: guessing the variant would be guessing the terms.

An expression is decided with the core allowlist (`repo_license_policy.classify_spdx`):
  "A OR B"   -- the licensee may choose, so it is allowed when any alternative is allowed (the first allowed one is
                recorded as the license the item is used under);
  "A AND B"  -- every part applies, so every part must be allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GITHUB_NAME_TO_SPDX: dict[str, str] = {
    "MIT License": "MIT",
    "MIT": "MIT",
    "MIT license": "MIT",
    "The MIT License (MIT)": "MIT",
    "Expat License": "MIT",
    "MIT No Attribution": "MIT-0",
    "MIT-CMU License": "MIT-CMU",
    "MIT/X Consortium license": "X11",
    "Apache License 2.0": "Apache-2.0",
    "Apache 2.0": "Apache-2.0",
    'BSD 3-Clause "New" or "Revised" License': "BSD-3-Clause",
    "BSD 3-Clause": "BSD-3-Clause",
    "BSD-3-Clause": "BSD-3-Clause",
    "BSD 3-Clause License": "BSD-3-Clause",
    "3-clause BSD License": "BSD-3-Clause",
    "3-Clause BSD license": "BSD-3-Clause",
    "3-Clause BSD License": "BSD-3-Clause",
    "BSD License (3 Clause)": "BSD-3-Clause",
    "BSD-3": "BSD-3-Clause",
    "BSD3 License": "BSD-3-Clause",
    "BSD-3 Clause": "BSD-3-Clause",
    "New BSD License": "BSD-3-Clause",
    "Modified BSD License": "BSD-3-Clause",
    "Revised BSD License": "BSD-3-Clause",
    "BSD 3-Clause Clear License": "BSD-3-Clause-Clear",
    'BSD 2-Clause "Simplified" License': "BSD-2-Clause",
    "BSD-2-Clause": "BSD-2-Clause",
    "BSD 2-clause license": "BSD-2-Clause",
    "BSD-2 License": "BSD-2-Clause",
    "Simplified BSD 2-Clause License": "BSD-2-Clause",
    "BSD-2-Clause-Patent": "BSD-2-Clause-Patent",
    "BSD-2-Clause Plus Patent License": "BSD-2-Clause-Patent",
    'BSD 4-Clause "Original" or "Old" License': "BSD-4-Clause",
    "ISC License": "ISC",
    "The Unlicense": "Unlicense",
    "Unlicense": "Unlicense",
    "Creative Commons Zero v1.0 Universal": "CC0-1.0",
    "Creative Commons Zero v1.0 Universal license (CC0 1.0)": "CC0-1.0",
    "CC0 1.0 Universal": "CC0-1.0",
    "Zope Public License 2.1": "ZPL-2.1",
    "PSF License Agreement": "PSF-2.0",
    "Academic Free License v3.0": "AFL-3.0",
    "Academic Free License 3.0": "AFL-3.0",
    "Do What The F*ck You Want To Public License": "WTFPL",
    "PostgreSQL License": "PostgreSQL",
    "Mozilla Public License 2.0": "MPL-2.0",
    "MIT/Apache-2.0 Dual License": "MIT OR Apache-2.0",
    "MIT/Apache-2.0 dual license": "MIT OR Apache-2.0",
    "Apache License 2.0 or MIT License": "Apache-2.0 OR MIT",
    "Apache License 2.0 or MIT license": "Apache-2.0 OR MIT",
    "Apache License 2.0 and MIT License": "Apache-2.0 AND MIT",
    "Apache 2.0 or BSD3": "Apache-2.0 OR BSD-3-Clause",
    "Apache License 2.0 or BSD 3-clause": "Apache-2.0 OR BSD-3-Clause",
}


@dataclass(frozen=True)
class LicenseDecision:
    decision: str                 # ALLOW | QUARANTINE | REJECT | UNMAPPABLE
    expression: Optional[str]     # the SPDX expression the source states
    used_under: Optional[str]     # the single SPDX id the item is used under (ALLOW only)
    reason: str
    allowlist_version: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"


def spdx_for_github_name(name: Optional[str]) -> Optional[str]:
    return GITHUB_NAME_TO_SPDX.get((name or "").strip()) if name else None


def decide(expression: Optional[str], *, records_attribution: bool) -> LicenseDecision:
    """Decide an SPDX expression (a single id, or ids joined by exactly one of OR / AND)."""
    from app.services.repo_license_policy import classify_spdx

    expr = (expression or "").strip()
    if not expr or expr.upper() == "NOASSERTION":
        return LicenseDecision("UNMAPPABLE", expression, None,
                               "no identifiable license" if not expr else "license file not identifiable (NOASSERTION)")
    has_or, has_and = " OR " in expr, " AND " in expr
    if has_or and has_and:
        return LicenseDecision("UNMAPPABLE", expr, None, "mixed AND/OR expressions are not decided automatically")
    parts = [p.strip() for p in expr.split(" OR " if has_or else " AND ")] if (has_or or has_and) else [expr]
    verdicts = [(p, classify_spdx(p, records_attribution=records_attribution)) for p in parts]
    version = verdicts[0][1].allowlist_version
    if has_or:
        allowed = [(p, v) for p, v in verdicts if v.decision == "ALLOW"]
        if allowed:
            return LicenseDecision("ALLOW", expr, allowed[0][0], f"{allowed[0][0]} chosen from {expr}", version)
        worst = "REJECT" if all(v.decision == "REJECT" for _, v in verdicts) else "QUARANTINE"
        return LicenseDecision(worst, expr, None, "; ".join(v.reason for _, v in verdicts), version)
    blocked = [(p, v) for p, v in verdicts if v.decision != "ALLOW"]
    if blocked:
        worst = "REJECT" if any(v.decision == "REJECT" for _, v in blocked) else "QUARANTINE"
        return LicenseDecision(worst, expr, None, "; ".join(v.reason for _, v in blocked), version)
    return LicenseDecision("ALLOW", expr, expr, verdicts[0][1].reason if len(parts) == 1 else f"all of {expr} allowed",
                           version)
