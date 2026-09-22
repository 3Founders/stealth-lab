"""
V1 experimental defaults for the contribution/verification/ranking/Credits
system. Every number here is a configuration constant, not a business rule
-- the business rules (which reason gets rewarded, when a cap applies) live
in credits.py/verification.py and reference these names, never a literal.

All are overridable via environment variables so a deployment can tune V1
without a code change; the literal values below are exactly the founder
directive's own stated example defaults.
"""
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Credits: reward amounts (§7) ------------------------------------------
NEW_PROCEDURE_REWARD = _f("KEL_NEW_PROCEDURE_REWARD", 10.0)
IMPROVEMENT_REWARD = _f("KEL_IMPROVEMENT_REWARD", 20.0)
VERIFIED_REUSE_REWARD = _f("KEL_VERIFIED_REUSE_REWARD", 5.0)

# Single-hop attribution only (§9) -- the immediate parent of an improvement
# gets a smaller share of a verified-reuse reward on the CHILD procedure.
# No multi-hop royalty chain, no Shapley value.
PARENT_ATTRIBUTION_SHARE = _f("KEL_PARENT_ATTRIBUTION_SHARE", 0.25)

# --- Credits: anti-gaming caps (§10) ---------------------------------------
DAILY_REWARD_CAP_PER_CONTRIBUTOR = _f("KEL_DAILY_REWARD_CAP", 200.0)
WEEKLY_REWARD_CAP_PER_CONTRIBUTOR = _f("KEL_WEEKLY_REWARD_CAP", 800.0)

# A single Procedure can only pay out verified-reuse rewards this many times
# per rolling 24h window, regardless of how many distinct reusers show up --
# blunt but simple V1 protection against a burst of coordinated "independent"
# reuse from the same actor cluster.
DAILY_VERIFIED_REUSE_REWARDS_PER_PROCEDURE = _i("KEL_DAILY_REUSE_REWARDS_PER_PROCEDURE", 20)

# --- Verification pipeline (§3) --------------------------------------------
# Layers, for procedure_usage_events.verification_layer:
LAYER_NONE = 0
LAYER_DETERMINISTIC = 1
LAYER_LLM = 2
LAYER_EXECUTION = 3
LAYER_INDEPENDENT_REUSE = 4
LAYER_HUMAN_REVIEW = 5

MIN_LAYER_FOR_VERIFIED = LAYER_EXECUTION  # DB CHECK enforces this too (defense in depth)

# --- Duplicate detection thresholds (Layer 1, cosine similarity in [0,1]) --
DUPLICATE_REJECT_THRESHOLD = _f("KEL_DUPLICATE_REJECT_THRESHOLD", 0.97)
DUPLICATE_REVIEW_THRESHOLD = _f("KEL_DUPLICATE_REVIEW_THRESHOLD", 0.85)

# --- Layer 1 schema minimums ------------------------------------------------
MIN_STEPS_FOR_CANDIDATE = _i("KEL_MIN_STEPS_FOR_CANDIDATE", 1)

# --- Standing (§6) -- simple, explainable, linear weights on real counts --
STANDING_WEIGHT_ACCEPTED_SUBMISSION = _f("KEL_STANDING_W_ACCEPTED", 1.0)
STANDING_WEIGHT_ACCEPTED_IMPROVEMENT = _f("KEL_STANDING_W_IMPROVEMENT", 1.5)
STANDING_WEIGHT_VERIFIED_OUTCOME = _f("KEL_STANDING_W_VERIFIED_OUTCOME", 2.0)
STANDING_WEIGHT_RELIABLE_EVIDENCE = _f("KEL_STANDING_W_EVIDENCE", 0.5)

# Evidence is "reliable" for Standing purposes at or above this strength_score.
RELIABLE_EVIDENCE_MIN_STRENGTH = _f("KEL_RELIABLE_EVIDENCE_MIN_STRENGTH", 0.6)

# --- Ranking (§4) -----------------------------------------------------------
WILSON_Z = _f("KEL_WILSON_Z", 1.96)  # 95% confidence, matches the existing
                                       # app.services.procedure_extraction.capability.wilson_interval default

# --- Rate limits (hardening pass §12) ---------------------------------------
# Reuses app.services.governance.RateLimiter/RateLimit verbatim (same
# mechanism app/api/deps.py::enforce_limits already uses for /v1/chat etc.)
# -- no new rate-limiting infrastructure. Keyed by the AUTHENTICATED
# principal's subject (see app/api/economy.py), never by a request-body
# identity string.
SUBMISSION_RATE_LIMIT_MAX = _i("KEL_SUBMISSION_RATE_LIMIT_MAX", 10)
SUBMISSION_RATE_LIMIT_WINDOW_HOURS = _i("KEL_SUBMISSION_RATE_LIMIT_WINDOW_HOURS", 1)
USAGE_EVENT_RATE_LIMIT_MAX = _i("KEL_USAGE_EVENT_RATE_LIMIT_MAX", 60)
USAGE_EVENT_RATE_LIMIT_WINDOW_HOURS = _i("KEL_USAGE_EVENT_RATE_LIMIT_WINDOW_HOURS", 1)
