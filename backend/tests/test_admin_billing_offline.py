"""GST invoice maths, numbering and the billing admin gate. Offline: no database."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.admin_billing import check_billing_admin
from app.services import auth_context as ac
from app.services import invoicing as inv

MH, KA = "27", "29"


def test_inclusive_split_adds_up_exactly_and_uses_cgst_sgst_in_state():
    s = inv.gst_split(11_800, Decimal("18"), MH, MH)
    assert s == {"taxable": 10_000, "cgst": 900, "sgst": 900, "igst": 0}
    for total in (100, 999, 10_000, 12_345, 5_000_000, 1):
        s = inv.gst_split(total, Decimal("18"), MH, MH)
        assert s["taxable"] + s["cgst"] + s["sgst"] + s["igst"] == total
        assert s["igst"] == 0 and 0 <= s["sgst"] - s["cgst"] <= 1


def test_other_state_is_all_igst():
    s = inv.gst_split(11_800, Decimal("18"), MH, KA)
    assert s == {"taxable": 10_000, "cgst": 0, "sgst": 0, "igst": 1_800}
    assert inv.gst_split(10_000, Decimal("0"), MH, KA) == {"taxable": 10_000, "cgst": 0, "sgst": 0, "igst": 0}


def test_gst_on_top_and_empty_amounts_are_refused():
    with pytest.raises(inv.InvoiceError):
        inv.gst_split(1000, Decimal("18"), MH, MH, inclusive=False)
    with pytest.raises(inv.InvoiceError):
        inv.gst_split(0, Decimal("18"), MH, MH)


def test_financial_year_and_number_fit_the_16_character_limit():
    assert inv.financial_year(date(2026, 10, 30)) == "2627"
    assert inv.financial_year(date(2027, 3, 31)) == "2627"
    assert inv.financial_year(date(2027, 4, 1)) == "2728"
    assert inv.invoice_number("KEL", "2627", 1) == "KEL/2627/000001"
    assert len(inv.invoice_number("ABCD", "2627", 999_999)) == 16
    with pytest.raises(inv.InvoiceError):
        inv.invoice_number("ABCDE", "2627", 1)


def test_gstin_format_and_state_code():
    assert inv.valid_gstin("27ABCDE1234F1Z5")
    for bad in ("27abcde1234f1z5", "27ABCDE1234F1X5", "2ABCDE1234F1Z5", "", None):
        assert not inv.valid_gstin(bad)
    assert inv.state_of_gstin("27ABCDE1234F1Z5") == "27"


def test_seller_must_be_complete_before_an_invoice_can_be_issued():
    assert inv.seller_problems(None)
    full = {"legal_name": "A", "gstin": "27ABCDE1234F1Z5", "state_code": "27", "address": "x", "sac_code": "998439"}
    assert inv.seller_problems(full) == []
    assert any("sac code" in p for p in inv.seller_problems({**full, "sac_code": ""}))
    assert any("state code" in p for p in inv.seller_problems({**full, "state_code": "29"}))
    assert any("format" in p for p in inv.seller_problems({**full, "gstin": "bad"}))


def test_profile_and_buyer_are_cleaned_and_checked():
    p = inv.clean_profile({"legal_name": " Acme ", "gstin": "27abcde1234f1z5", "address": "Mumbai", "sac_code": "998439"})
    assert p["gstin"] == "27ABCDE1234F1Z5" and p["state_code"] == "27" and p["invoice_prefix"] == "KEL"
    with pytest.raises(inv.InvoiceError):
        inv.clean_profile({"legal_name": "A", "gstin": "27ABCDE1234F1Z5", "state_code": "29"})
    with pytest.raises(inv.InvoiceError):
        inv.clean_profile({"legal_name": ""})
    assert inv.clean_buyer({"name": "B", "state_code": "29"})["state_code"] == "29"
    assert inv.clean_buyer({"name": "B", "gstin": "29ABCDE1234F1Z5"})["state_code"] == "29"
    with pytest.raises(inv.InvoiceError):
        inv.clean_buyer({"name": "B"})          # place of supply is needed
    with pytest.raises(inv.InvoiceError):
        inv.clean_buyer({"name": "", "state_code": "29"})


def _ctx(scopes, claims=None):
    return ac.AuthContext(user_id="u", subject="s", scopes=frozenset(scopes), claims=claims or {})


def test_only_a_person_with_the_admin_scope_passes_and_mfa_is_enforced_when_on():
    check_billing_admin(_ctx({ac.ADMIN_OPS}), mfa=False)
    with pytest.raises(HTTPException) as e:
        check_billing_admin(_ctx({"something:else"}), mfa=False)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        check_billing_admin(object(), mfa=False)                      # a service, the shared key or an anonymous caller
    with pytest.raises(HTTPException) as e2:
        check_billing_admin(_ctx({ac.ADMIN_OPS}, {"aal": "aal1"}), mfa=True)
    assert "multi-factor" in e2.value.detail
    check_billing_admin(_ctx({ac.ADMIN_OPS}, {"aal": "aal2"}), mfa=True)
