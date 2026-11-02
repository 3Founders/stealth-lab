-- The launch code the founders asked for (2026-11-02): "Firsttrial" unlocks the one-person Pilot - the Rs 649 plan for
-- one person, 30 days, $4 of usage. Codes are stored upper-case; matching is case-insensitive, so "Firsttrial" as typed works.
-- It is managed afterwards in the admin dashboard's Billing page -> Referral codes (turn it off, edit the note, delete it if
-- no order used it). No reward is paid to anyone for it.
INSERT INTO billing_referral_codes (code, kind, note, active, created_by) VALUES
    ('FIRSTTRIAL', 'issued', 'Launch code for the one-person Pilot (Rs 649, 30 days)', TRUE, 'seed')
ON CONFLICT (code) DO NOTHING;
