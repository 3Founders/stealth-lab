-- 195_tool_listings.sql
--
-- Next free number: 194 (verification) was highest before this file.
--
-- What this adds (docs/goal_router.md P3-06): priced tools, paid from the person's own plan wallet, credits only (no
-- cash payouts in this plan).
--
--   * tool_listings: a card's price per call, who provides it, how many free trial calls a person gets, and its status.
--     A card with no active listing is free.
--   * tool_charges: one row per priced call, keyed by the call (call_key UNIQUE), so a call is debited exactly once:
--     who paid (the person, or the provider for a trial call), the price, the provider's and kell's shares, and its
--     state: held (before the call), settled (it ran), refunded (it failed). The person's money moves in
--     provider_call_ledger (136/164), the ledger the wallet balance is computed from (app/services/wallet.py balance()),
--     through that ledger's own one-way flow (tg_ledger_guard): a reserved row (held against the balance) becomes
--     settled (spent) or failed (released), exactly once. So a tool call comes off the same balance as a model call.
--
-- Access: application only, like 160/162/163.
-- Idempotent.

CREATE TABLE IF NOT EXISTS tool_listings (
    card_id           text PRIMARY KEY REFERENCES tool_cards (id) ON DELETE CASCADE,
    provider          text NOT NULL,
    price_usd         numeric(12, 6) NOT NULL CHECK (price_usd >= 0),
    take_rate         numeric(5, 4) NOT NULL DEFAULT 0.15 CHECK (take_rate >= 0 AND take_rate <= 1),
    free_trial_calls  integer NOT NULL DEFAULT 0 CHECK (free_trial_calls >= 0),
    status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'delisted')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_charges (
    id              bigserial PRIMARY KEY,
    call_key        text NOT NULL UNIQUE,                    -- one charge per call
    owner_id        text NOT NULL,
    card_id         text NOT NULL,
    tool            text NOT NULL DEFAULT '',
    price_usd       numeric(12, 6) NOT NULL CHECK (price_usd >= 0),
    provider_usd    numeric(12, 6) NOT NULL CHECK (provider_usd >= 0),
    kell_usd        numeric(12, 6) NOT NULL CHECK (kell_usd >= 0),
    paid_by         text NOT NULL CHECK (paid_by IN ('owner', 'provider_trial', 'free_trial_calls')),
    ledger_id       uuid,                                    -- the provider_call_ledger row, when the person paid
    trial_id        bigint,
    state           text NOT NULL DEFAULT 'held' CHECK (state IN ('held', 'settled', 'refunded')),
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_charges_owner ON tool_charges (owner_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_charges_card ON tool_charges (card_id, at DESC);

ALTER TABLE tool_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_charges ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON tool_listings, tool_charges FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON tool_listings, tool_charges FROM authenticated;
    END IF;
END $$;
