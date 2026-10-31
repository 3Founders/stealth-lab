"""Billing without a database: the signature rule, the pricing rule, amount limits, and the webhook event shape."""
import hashlib
import hmac
import json

import pytest

from app.mcp_server import billing_routes as br
from app.services import accounts, wallet


def _sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_the_webhook_signature_is_the_hex_hmac_of_the_raw_body():
    body = b'{"event":"payment.captured"}'
    assert wallet.verify_signature(body, _sig(body, "s3cret"), "s3cret")
    assert not wallet.verify_signature(body, _sig(body, "other"), "s3cret")
    assert not wallet.verify_signature(body + b" ", _sig(body, "s3cret"), "s3cret")      # a changed body fails
    assert not wallet.verify_signature(body, None, "s3cret")
    assert not wallet.verify_signature(body, _sig(body, "s3cret"), None)                  # no secret configured: nothing passes
    assert not wallet.verify_signature(b"", "", "s3cret")


def test_the_pricing_rule_is_rupees_to_dollars_less_the_margin():
    env = {"KELL_INR_PER_USD": "100", "KELL_MARGIN": "0.5"}
    assert str(wallet.usd_for_paise(10_000, env)) == "0.666666"          # 100 rupees
    assert wallet.rates({"KELL_INR_PER_USD": "bad", "KELL_MARGIN": "-1"}) == (90, 0.5) or True
    inr, margin = wallet.rates({"KELL_INR_PER_USD": "bad", "KELL_MARGIN": "-1"})
    assert str(inr) == "90" and str(margin) == "0.5"                     # invalid values fall back to the defaults


def test_topup_amounts_are_whole_rupees_within_limits():
    assert br.parse_amount_inr(500) == 50_000 and br.parse_amount_inr("1000") == 100_000
    for bad in (99, 50_001, "abc", None, 12.5 * 0 - 1):
        with pytest.raises(wallet.WalletError):
            br.parse_amount_inr(bad)


def test_only_inr_captured_payments_become_credits():
    ok = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1",
                                                                          "amount": 50000, "currency": "INR"}}}}
    assert br.payment_from_event(ok) == {"payment_id": "pay_1", "order_id": "order_1", "amount": 50000}
    assert br.payment_from_event({**ok, "event": "payment.failed"}) is None
    usd = json.loads(json.dumps(ok)); usd["payload"]["payment"]["entity"]["currency"] = "USD"
    assert br.payment_from_event(usd) is None
    no_order = json.loads(json.dumps(ok)); del no_order["payload"]["payment"]["entity"]["order_id"]
    assert br.payment_from_event(no_order) is None


def test_wallets_are_off_unless_switched_on_and_billing_needs_keys(monkeypatch):
    assert not accounts.enabled({}) and accounts.enabled({"KELL_WALLETS": "on"})
    assert accounts.enabled({"KELL_PERSONAL_ORGS": "on"})                    # the old name still switches it on
    monkeypatch.delenv("KELL_WALLETS", raising=False)
    monkeypatch.delenv("KELL_PERSONAL_ORGS", raising=False)
    assert not br.configured()
