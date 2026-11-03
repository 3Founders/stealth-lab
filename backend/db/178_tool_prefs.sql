-- The person's tool preference (app/tool_routing/prefs.py): may kell use tools that need a sign-in?
-- One row per account (the Supabase subject), so it follows the person to every device. Default on.

CREATE TABLE IF NOT EXISTS tool_prefs (
    owner_id           TEXT PRIMARY KEY,
    allow_signin_tools BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
