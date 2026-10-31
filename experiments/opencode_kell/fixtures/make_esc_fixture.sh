#!/usr/bin/env bash
# A bug that needs a change in two files at once (rounding mode in tax.py AND sum-then-round in invoice.py); each fix alone
# leaves a test failing. For the escalation run. usage: make_esc_fixture.sh <empty-dir>
set -e
d="$1"; mkdir -p "$d/shop" "$d/tests"; cd "$d"
touch shop/__init__.py
cat > shop/tax.py <<'P'
"""Sales tax in whole cents."""
from decimal import Decimal, ROUND_DOWN

RATES = {"EU": Decimal("0.19"), "US": Decimal("0.0725")}


def tax_cents(net_cents, region):
    """Tax on an amount in cents, rounded to a whole cent."""
    return int((Decimal(net_cents) * RATES[region]).quantize(Decimal("1"), rounding=ROUND_DOWN))
P
cat > shop/invoice.py <<'P'
"""Invoice totals. Money is kept in whole cents."""
from .tax import tax_cents


def line_total(unit_cents, qty, region):
    net = unit_cents * qty
    return net + tax_cents(net, region)


def invoice_total(lines, region):
    """lines: list of (unit_cents, qty). Each line is taxed on its own."""
    return sum(line_total(u, q, region) for u, q in lines)
P
cat > tests/test_shop.py <<'P'
from shop.tax import tax_cents
from shop.invoice import invoice_total, line_total


def test_tax_rounds_half_up():
    assert tax_cents(250, "EU") == 48          # 47.5 -> 48 (half up)


def test_tax_exact():
    assert tax_cents(1000, "EU") == 190


def test_line_total():
    assert line_total(250, 1, "EU") == 298


def test_invoice_taxes_the_sum_not_each_line():
    # three 1.10 items: tax on 330 at 7.25% = 23.925 -> 24; per line it would be 3 * 8 = 24 or 3 * 7 = 21 depending on rounding
    assert invoice_total([(110, 1), (110, 1), (110, 1)], "US") == 330 + 24


def test_invoice_mixed():
    assert invoice_total([(199, 2), (350, 1)], "EU") == 748 + 142
P
cat > pytest.ini <<'P'
[pytest]
pythonpath = .
P
git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -q -m "fixture"
