-- 220: remember the hosted checkout URL on the purchase row.
--
-- Creating a Razorpay Payment Link takes about 4-5 seconds, and the Way page used to call Razorpay
-- again on every repeat attempt to find the link it had already made. Storing `short_url` means the
-- page can pre-warm the checkout when the pointer reaches the button and then redirect instantly,
-- and a reloaded page reuses the same link instead of waiting on the gateway.
--
-- The URL is not a secret: it is the page the buyer is sent to. It is cleared nowhere on purpose —
-- activation, refund and consumption keep the history of which link produced the purchase.
--
-- Additive and idempotent.

ALTER TABLE procedure_purchases ADD COLUMN IF NOT EXISTS checkout_url TEXT;
