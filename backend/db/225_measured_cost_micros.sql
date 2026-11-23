-- Keep a Way's measured cost to a precision it can actually have (docs/blinkit/measurement.md).
--
-- 224 added `measured_cost_paise`, a ROUNDED figure. Measured the same day against the live database: a cheap model
-- step costs $0.00042 ≈ ₹0.035, which rounds to **0 paise**. A Way that costs money would have been recorded as free,
-- and `set_price` would then allow it to be priced above a floor of zero — the exact failure the floor exists to
-- prevent.
--
-- So the exact figure lives in micro-rupees (1e-6), and the paise column stays only as a convenience for display.
--
-- Additive and idempotent. 224 is immutable once applied, which is why this is 225 and not an edit.

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS measured_cost_micros bigint;

COMMENT ON COLUMN procedures.measured_cost_paise IS
    'Mean cost in paise over the measured runs, ROUNDED. Display only: a step often costs under one paisa.';
COMMENT ON COLUMN procedures.measured_cost_micros IS
    'Mean cost in micro-rupees (1e-6) over the measured runs. This is the floor a price must clear.';
