"""Validation-framework tasks: 18 record specs, each with a reference validator and hand-labelled samples.

A spec is declarative (fields + cross-field rules); the same declaration renders the TASK.md text the agent reads
and drives the reference validator (`reference(name)`), so the prose and the reference cannot drift apart. The
samples' expected error codes are written BY HAND (a base valid record plus a change, and the codes that change
should produce); they are not computed from the reference, so a reference bug shows up as a failed validation.

Conventions every spec states (see CONVENTIONS):
- validate(record) returns the codes of every broken rule; order and duplicates do not matter (graded as a set).
- A required field that is absent or null -> <F>_MISSING. An optional field that is absent or null is not checked.
- Wrong JSON type -> <F>_TYPE only; the field's other rules (and its children) are then not checked.
- "integer" is a JSON integer (not a bool, not 3.0); "number" is an integer or a float (not a bool).
- A date is a "YYYY-MM-DD" string naming a real calendar date; any other string -> <F>_FORMAT.
- A field's own rules (range, length, format, allowed values) are all checked, and each broken one is reported.
- In a list of objects, a code for an item field is reported once however many items break it.
- A cross-field rule is checked only when every field it uses is present and has no error of its own (for a list or
  object: no error anywhere inside it), and every optional field it mentions is absent or error-free.
"""
from __future__ import annotations

import datetime as _dt
import re

DEL = object()           # in a sample change: remove this key
MISSING = object()


class F:
    def __init__(self, path, type, required=True, min=None, max=None, gt=None, min_len=None, max_len=None,
                 regex=None, enum=None, note=None):
        self.path, self.type, self.required = path, type, required
        self.min, self.max, self.gt = min, max, gt
        self.min_len, self.max_len, self.regex, self.enum, self.note = min_len, max_len, regex, enum, note
        base = path.replace("[]", "_ITEM" if path.endswith("[]") else "").replace(".", "_").upper()
        self.prefix = base

    def code(self, what):
        return f"{self.prefix}_{what}"

    def codes(self):
        out = [self.code("MISSING")] if self.required and not self.path.endswith("[]") else []
        out.append(self.code("TYPE"))
        if self.type == "date":
            out.append(self.code("FORMAT"))
        if self.min is not None or self.max is not None or self.gt is not None:
            out.append(self.code("RANGE"))
        if self.min_len is not None or self.max_len is not None:
            out.append(self.code("LENGTH"))
        if self.regex:
            out.append(self.code("FORMAT"))
        if self.enum:
            out.append(self.code("ENUM"))
        return list(dict.fromkeys(out))

    def describe(self):
        kind = {"string": "string", "int": "integer", "number": "number", "bool": "boolean", "date": "date",
                "object": "object", "list": "list"}[self.type]
        bits = []
        if self.gt is not None:
            bits.append(f"greater than {self.gt}")
        if self.min is not None and self.max is not None:
            bits.append(f"{self.min} to {self.max} inclusive")
        elif self.min is not None:
            bits.append(f"at least {self.min}")
        elif self.max is not None:
            bits.append(f"at most {self.max}")
        if self.min_len is not None or self.max_len is not None:
            what = "items" if self.type == "list" else "characters"
            if self.min_len is not None and self.max_len is not None:
                bits.append(f"{self.min_len} to {self.max_len} {what}")
            elif self.min_len is not None:
                bits.append(f"at least {self.min_len} {what}")
            else:
                bits.append(f"at most {self.max_len} {what}")
        if self.regex:
            bits.append(f"must fully match the regex `{self.regex}`")
        if self.enum:
            bits.append("one of " + ", ".join(f"`{e}`" for e in self.enum))
        if self.note:
            bits.append(self.note)
        where = "each element of " + f"`{self.path[:-2]}`" if self.path.endswith("[]") else f"`{self.path}`"
        req = "" if self.path.endswith("[]") else ("required, " if self.required else "optional, ")
        rule = ("; ".join(bits) + ". ") if bits else ""
        return f"- {where} ({req}{kind}): {rule}Codes: " + ", ".join(self.codes()) + "."


class C:
    def __init__(self, code, text, uses, fn, optional=()):
        self.code, self.text, self.uses, self.fn, self.optional = code, text, list(uses), fn, list(optional)

    def describe(self):
        cond = "Checked only when " + ", ".join(f"`{u}`" for u in self.uses) + " are present and valid"
        if self.optional:
            cond += ", and " + ", ".join(f"`{u}`" for u in self.optional) + " is absent or valid"
        return f"- `{self.code}`: {self.text} ({cond}.)"


class Spec:
    def __init__(self, name, title, intro, fields, cross=(), strict=False, bases=(), samples=()):
        self.name, self.title, self.intro = name, title, intro
        self.fields, self.cross, self.strict = list(fields), list(cross), strict
        self.bases, self.samples = list(bases), list(samples)


# ---------------------------------------------------------------- the reference engine

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool))


def _date(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        return _dt.date.fromisoformat(v)
    except ValueError:
        return None


def date(v):
    """A date string as a datetime.date (for cross rules)."""
    return _dt.date.fromisoformat(v)


_TYPES = {"string": lambda v: isinstance(v, str), "int": _is_int, "number": _is_num,
          "bool": lambda v: isinstance(v, bool), "date": lambda v: isinstance(v, str),
          "object": lambda v: isinstance(v, dict), "list": lambda v: isinstance(v, list)}


def _locate(rec, path):
    """Values at a path: a list of (value or MISSING), only where every parent is the right container."""
    nodes = [rec]
    segs = path.split(".")
    for i, seg in enumerate(segs):
        last = i == len(segs) - 1
        name, each = (seg[:-2], True) if seg.endswith("[]") else (seg, False)
        nxt = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            v = n.get(name)
            if each:
                if isinstance(v, list):
                    nxt.extend(v)
            elif v is None:
                if last:
                    nxt.append(MISSING)
            else:
                nxt.append(v)
        nodes = nxt
    return nodes


def _check(f, v):
    if not _TYPES[f.type](v):
        return [f.code("TYPE")]
    errs = []
    val = v
    if f.type == "date":
        val = _date(v)
        if val is None:
            return [f.code("FORMAT")]
    if f.min is not None or f.max is not None or f.gt is not None:
        lo = _date(f.min) if f.type == "date" and f.min is not None else f.min
        hi = _date(f.max) if f.type == "date" and f.max is not None else f.max
        if (lo is not None and val < lo) or (hi is not None and val > hi) or (f.gt is not None and not val > f.gt):
            errs.append(f.code("RANGE"))
    if f.min_len is not None or f.max_len is not None:
        if (f.min_len is not None and len(v) < f.min_len) or (f.max_len is not None and len(v) > f.max_len):
            errs.append(f.code("LENGTH"))
    if f.regex and not re.fullmatch(f.regex, v):
        errs.append(f.code("FORMAT"))
    if f.enum and v not in f.enum:
        errs.append(f.code("ENUM"))
    return errs


def run(spec, rec):
    errors = []                                     # (path, code)
    if not isinstance(rec, dict):
        return ["RECORD_TYPE"]
    for f in spec.fields:
        for v in _locate(rec, f.path):
            if v is MISSING:
                if f.required:
                    errors.append((f.path, f.code("MISSING")))
                continue
            errors += [(f.path, c) for c in _check(f, v)]
    if spec.strict:
        known = {f.path.split(".")[0].replace("[]", "") for f in spec.fields}
        if any(k not in known for k in rec):
            errors.append(("", "UNKNOWN_FIELD"))

    def ok(path):
        return not any(p == path or p.startswith(path + ".") or p.startswith(path + "[]") for p, _ in errors)

    def present(path):
        return any(v is not MISSING for v in _locate(rec, path))

    cross = []
    for c in spec.cross:
        if all(present(u) and ok(u) for u in c.uses) and all(ok(u) for u in c.optional):
            if not c.fn(rec):
                cross.append(c.code)
    return sorted(set(code for _, code in errors) | set(cross))


def reference(name):
    spec = SPECS[name]
    return lambda rec: run(spec, rec)


# ---------------------------------------------------------------- samples

def apply(base, change):
    import copy
    rec = copy.deepcopy(base)
    for path, val in change.items():
        node = rec
        keys = [int(k) if k.isdigit() else k for k in path.split(".")]
        for k in keys[:-1]:
            node = node[k]
        if val is DEL:
            del node[keys[-1]]
        else:
            node[keys[-1]] = val
    return rec


def S(i, change, *codes):
    return {"base": i, "change": change, "expect": sorted(codes)}


def R(record, *codes):
    return {"record": record, "expect": sorted(codes)}


def samples(name):
    """The labelled samples as plain records: [{"record": ..., "expect": [...]}], in the fixed order."""
    spec = SPECS[name]
    out = []
    for i in range(len(spec.bases)):
        out.append({"record": spec.bases[i], "expect": []})
    for s in spec.samples:
        rec = s["record"] if "record" in s else apply(spec.bases[s["base"]], s["change"])
        out.append({"record": rec, "expect": s["expect"]})
    return out


CONVENTIONS = """\
## Conventions

- `validate(record)` gets one record (a dict parsed from JSON) and returns a list of error codes: the code of every
  rule the record breaks. An empty list means the record is valid. Order and duplicates do not matter.
- A **required** field that is absent or `null` -> its `_MISSING` code. An **optional** field that is absent or
  `null` is not checked at all.
- A field with the wrong JSON type -> its `_TYPE` code only; none of that field's other rules (and nothing inside
  it, for an object or a list) is checked.
- "integer" means a JSON integer: not a boolean and not a float such as `3.0`. "number" means an integer or a float
  (not a boolean). "date" means a string `YYYY-MM-DD` that names a real calendar date; any other string -> the
  field's `_FORMAT` code (date ranges are then not checked). Date ranges compare calendar dates.
- Once the type (and, for a date, the format) is right, all of the field's own rules are checked -- range, length,
  regex, allowed values -- and each broken one is reported. Regexes must match the WHOLE value.
- A field inside an object (`a.b`) or inside list items (`a[].b`) is checked only when its parent is present and of
  the right type; "required" then means required within that parent.
- In a list of objects, an item field's code is reported once, however many items break it; an item that is not an
  object -> the list's `_ITEM_TYPE` code (where the spec lists one) and its fields are not checked.
- A cross-field rule is checked only when every field it uses is present and has no error of its own (for a list or
  an object: no error anywhere inside it), and every optional field it mentions is absent or error-free.
"""


def task_md(name):
    spec = SPECS[name]
    import json
    lines = [f"# {spec.title}", "", spec.intro, "",
             "Implement `validate(record) -> list[str]` in `validator.py` (standard library only).", "",
             CONVENTIONS, "## Fields", ""]
    lines += [f.describe() for f in spec.fields]
    if spec.strict:
        lines += ["- Any top-level key not listed above -> `UNKNOWN_FIELD` (once)."]
    if spec.cross:
        lines += ["", "## Cross-field rules", ""] + [c.describe() for c in spec.cross]
    lines += ["", "## A valid record", "", "```json", json.dumps(spec.bases[0], indent=2), "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- the 18 specs

EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
SPECS: dict[str, Spec] = {}


def add(spec):
    SPECS[spec.name] = spec


def _order_total(r):
    sub = sum(i["qty"] * i["unit_price"] for i in r["items"])
    disc = r.get("discount_pct") or 0
    return abs(round(sub * (1 - disc / 100), 2) - r["total"]) <= 0.01 + 1e-9


add(Spec(
    "orders", "Order records", "Records are web-shop orders submitted by a checkout service.",
    [F("order_id", "string", regex=r"ORD-\d{6}"),
     F("customer_email", "string", regex=EMAIL),
     F("currency", "string", enum=["USD", "EUR", "GBP"]),
     F("items", "list", min_len=1, max_len=20),
     F("items[]", "object"),
     F("items[].sku", "string", regex=r"[A-Z]{3}-\d{4}"),
     F("items[].qty", "int", min=1, max=999),
     F("items[].unit_price", "number", min=0.01, max=10000),
     F("discount_pct", "number", required=False, min=0, max=50),
     F("total", "number", min=0)],
    [C("TOTAL_MISMATCH", "`total` must equal the sum of `qty * unit_price` over the items, reduced by "
       "`discount_pct` percent (0 when absent), rounded to 2 decimals; a difference of up to 0.01 is accepted.",
       ["items", "total"], _order_total, optional=["discount_pct"])],
    bases=[{"order_id": "ORD-000123", "customer_email": "ana.lima@example.com", "currency": "EUR",
            "items": [{"sku": "ABC-0001", "qty": 2, "unit_price": 9.99}, {"sku": "XYZ-1234", "qty": 1, "unit_price": 25}],
            "total": 44.98},
           {"order_id": "ORD-999999", "customer_email": "b@shop.co.uk", "currency": "GBP",
            "items": [{"sku": "QQQ-0420", "qty": 10, "unit_price": 1.5}], "discount_pct": 10, "total": 13.5},
           {"order_id": "ORD-000001", "customer_email": "x_y+z@mail.example.org", "currency": "USD",
            "items": [{"sku": "AAA-0000", "qty": 999, "unit_price": 0.01}], "discount_pct": 0, "total": 9.99}],
    samples=[S(0, {"order_id": "ORD-12345"}, "ORDER_ID_FORMAT"),
             S(0, {"order_id": 123456}, "ORDER_ID_TYPE"),
             S(0, {"order_id": DEL}, "ORDER_ID_MISSING"),
             S(0, {"customer_email": "ana.lima@example"}, "CUSTOMER_EMAIL_FORMAT"),
             S(0, {"customer_email": None}, "CUSTOMER_EMAIL_MISSING"),
             S(0, {"currency": "eur"}, "CURRENCY_ENUM"),
             S(0, {"items": []}, "ITEMS_LENGTH"),
             S(0, {"items": "ABC-0001"}, "ITEMS_TYPE"),
             S(0, {"items.0.qty": 0}, "ITEMS_QTY_RANGE"),
             S(0, {"items.0.qty": 1000}, "ITEMS_QTY_RANGE"),
             S(0, {"items.0.qty": 2.0}, "ITEMS_QTY_TYPE"),
             S(0, {"items.1.qty": True}, "ITEMS_QTY_TYPE"),
             S(0, {"items.0.sku": "abc-0001", "items.1.sku": "XY-1234"}, "ITEMS_SKU_FORMAT"),
             S(0, {"items.0.unit_price": 0}, "ITEMS_UNIT_PRICE_RANGE"),
             S(0, {"items.1": "XYZ-1234"}, "ITEMS_ITEM_TYPE"),
             S(0, {"items.1.unit_price": DEL}, "ITEMS_UNIT_PRICE_MISSING"),
             S(0, {"total": 45.10}, "TOTAL_MISMATCH"),
             S(0, {"total": 44.975}),
             S(0, {"total": -1}, "TOTAL_RANGE"),
             S(0, {"total": "44.98"}, "TOTAL_TYPE"),
             S(1, {"discount_pct": 51}, "DISCOUNT_PCT_RANGE"),
             S(1, {"discount_pct": None, "total": 15}),
             S(1, {"discount_pct": DEL}, "TOTAL_MISMATCH"),
             S(1, {"items.0.qty": 0, "total": 999}, "ITEMS_QTY_RANGE"),
             S(2, {"currency": "JPY", "order_id": "ord-000001", "total": 10.5}, "CURRENCY_ENUM", "ORDER_ID_FORMAT",
               "TOTAL_MISMATCH"),
             R({}, "ORDER_ID_MISSING", "CUSTOMER_EMAIL_MISSING", "CURRENCY_MISSING", "ITEMS_MISSING", "TOTAL_MISSING")]))


add(Spec(
    "users", "User accounts", "Records are user accounts exported from an admin console. Unknown keys are not allowed.",
    [F("username", "string", regex=r"[a-z][a-z0-9_]{2,15}"),
     F("email", "string", regex=EMAIL),
     F("age", "int", required=False, min=13, max=120),
     F("roles", "list", min_len=1, max_len=3),
     F("roles[]", "string", enum=["admin", "editor", "viewer"]),
     F("created", "date", min="2000-01-01"),
     F("password_changed", "date", required=False)],
    [C("ROLES_DUPLICATE", "`roles` must not contain the same role twice.", ["roles"],
       lambda r: len(set(r["roles"])) == len(r["roles"])),
     C("PASSWORD_BEFORE_CREATED", "`password_changed` must not be earlier than `created`.",
       ["created", "password_changed"], lambda r: date(r["password_changed"]) >= date(r["created"]))],
    strict=True,
    bases=[{"username": "jdoe", "email": "jdoe@example.com", "age": 34, "roles": ["editor", "viewer"],
            "created": "2021-03-14", "password_changed": "2023-01-02"},
           {"username": "a_b", "email": "ab@corp.example.net", "roles": ["admin"], "created": "2000-01-01"},
           {"username": "zeta_user_123456", "email": "z@q.io", "age": 13, "roles": ["viewer"],
            "created": "2024-02-29", "password_changed": "2024-02-29"}],
    samples=[S(0, {"username": "JDoe"}, "USERNAME_FORMAT"),
             S(0, {"username": "jd"}, "USERNAME_FORMAT"),
             S(0, {"username": "1abc"}, "USERNAME_FORMAT"),
             S(0, {"username": "a_very_long_username_x"}, "USERNAME_FORMAT"),
             S(0, {"email": "jdoe@@example.com"}, "EMAIL_FORMAT"),
             S(0, {"age": 12}, "AGE_RANGE"),
             S(0, {"age": 121}, "AGE_RANGE"),
             S(0, {"age": "34"}, "AGE_TYPE"),
             S(0, {"age": None}),
             S(0, {"roles": []}, "ROLES_LENGTH"),
             S(0, {"roles": ["admin", "editor", "viewer", "viewer"]}, "ROLES_LENGTH"),
             S(0, {"roles": ["editor", "editor"]}, "ROLES_DUPLICATE"),
             S(0, {"roles": ["owner", "viewer"]}, "ROLES_ITEM_ENUM"),
             S(0, {"roles": ["owner", "owner"]}, "ROLES_ITEM_ENUM"),
             S(0, {"roles": "admin"}, "ROLES_TYPE"),
             S(0, {"roles": [1]}, "ROLES_ITEM_TYPE"),
             S(0, {"created": "2021-02-30"}, "CREATED_FORMAT"),
             S(0, {"created": "1999-12-31", "password_changed": "1999-01-01"}, "CREATED_RANGE"),
             S(0, {"created": "2021/03/14"}, "CREATED_FORMAT"),
             S(0, {"password_changed": "2021-03-13"}, "PASSWORD_BEFORE_CREATED"),
             S(0, {"password_changed": "2021-03-14"}),
             S(0, {"nickname": "jd"}, "UNKNOWN_FIELD"),
             S(1, {"extra1": 1, "extra2": 2}, "UNKNOWN_FIELD"),
             S(1, {"created": DEL, "password_changed": "1990-01-01"}, "CREATED_MISSING"),
             S(2, {"age": 13.0}, "AGE_TYPE"),
             S(2, {"created": "2023-02-29", "password_changed": "2020-01-01"}, "CREATED_FORMAT")]))


def _inv_total(r):
    sub = sum(l["amount"] for l in r["lines"])
    return abs(round(sub * (1 + r["tax_rate"]), 2) - r["total"]) <= 0.005 + 1e-9


add(Spec(
    "invoices", "Invoices", "Records are invoices produced by a billing system.",
    [F("invoice_no", "string", regex=r"INV-\d{4}-\d{5}"),
     F("issue_date", "date"),
     F("due_date", "date"),
     F("lines", "list", min_len=1, max_len=50),
     F("lines[]", "object"),
     F("lines[].description", "string", min_len=1, max_len=200),
     F("lines[].amount", "number"),
     F("tax_rate", "number", min=0, max=0.3),
     F("total", "number")],
    [C("DUE_BEFORE_ISSUE", "`due_date` must not be earlier than `issue_date`.", ["issue_date", "due_date"],
       lambda r: date(r["due_date"]) >= date(r["issue_date"])),
     C("DUE_TOO_LATE", "`due_date` must be at most 90 days after `issue_date`.", ["issue_date", "due_date"],
       lambda r: (date(r["due_date"]) - date(r["issue_date"])).days <= 90),
     C("TOTAL_MISMATCH", "`total` must equal the sum of the lines' `amount` times `(1 + tax_rate)`, rounded to 2 "
       "decimals (a difference of up to 0.005 is accepted). Line amounts may be negative (credits).",
       ["lines", "tax_rate", "total"], _inv_total)],
    bases=[{"invoice_no": "INV-2024-00042", "issue_date": "2024-05-01", "due_date": "2024-05-31",
            "lines": [{"description": "Consulting, May", "amount": 1200.0}, {"description": "Travel", "amount": 300}],
            "tax_rate": 0.2, "total": 1800.0},
           {"invoice_no": "INV-2023-99999", "issue_date": "2023-12-15", "due_date": "2024-03-14",
            "lines": [{"description": "Licence", "amount": 99.99}, {"description": "Credit note", "amount": -9.99}],
            "tax_rate": 0, "total": 90},
           {"invoice_no": "INV-2025-00001", "issue_date": "2025-01-31", "due_date": "2025-01-31",
            "lines": [{"description": "x", "amount": 10}], "tax_rate": 0.3, "total": 13}],
    samples=[S(0, {"invoice_no": "INV-24-00042"}, "INVOICE_NO_FORMAT"),
             S(0, {"issue_date": "2024-13-01"}, "ISSUE_DATE_FORMAT"),
             S(0, {"issue_date": 20240501}, "ISSUE_DATE_TYPE"),
             S(0, {"due_date": "2024-04-30"}, "DUE_BEFORE_ISSUE"),
             S(0, {"due_date": "2024-07-31"}, "DUE_TOO_LATE"),
             S(0, {"due_date": "2024-07-30"}),
             S(0, {"due_date": DEL}, "DUE_DATE_MISSING"),
             S(0, {"lines": []}, "LINES_LENGTH"),
             S(0, {"lines.0.description": ""}, "LINES_DESCRIPTION_LENGTH"),
             S(0, {"lines.0.description": "d" * 201}, "LINES_DESCRIPTION_LENGTH"),
             S(0, {"lines.1.amount": "300"}, "LINES_AMOUNT_TYPE"),
             S(0, {"lines.1.amount": None}, "LINES_AMOUNT_MISSING"),
             S(0, {"lines.0": 1200}, "LINES_ITEM_TYPE"),
             S(0, {"tax_rate": 0.31, "total": 1965}, "TAX_RATE_RANGE"),
             S(0, {"tax_rate": -0.1}, "TAX_RATE_RANGE"),
             S(0, {"total": 1800.01}, "TOTAL_MISMATCH"),
             S(0, {"total": 1800.004}),
             S(0, {"total": False}, "TOTAL_TYPE"),
             S(1, {"tax_rate": 0.1}, "TOTAL_MISMATCH"),
             S(1, {"tax_rate": 0.1, "total": 99}),
             S(1, {"due_date": "2024-03-15"}, "DUE_TOO_LATE"),
             S(2, {"due_date": "2025-02-29"}, "DUE_DATE_FORMAT"),
             S(2, {"lines.0.amount": 10.5}, "TOTAL_MISMATCH"),
             S(2, {"issue_date": "2025-02-01", "lines": "x"}, "DUE_BEFORE_ISSUE", "LINES_TYPE")]))


def _plausible(r):
    lo, hi = {"C": (-50, 150), "F": (-58, 302), "K": (223.15, 423.15)}[r["unit"]]
    return lo <= r["value"] <= hi


add(Spec(
    "sensor_readings", "Sensor readings", "Records are temperature readings posted by field sensors.",
    [F("sensor_id", "string", regex=r"S\d{3}"),
     F("ts", "string", regex=r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", note="a UTC timestamp"),
     F("unit", "string", enum=["C", "F", "K"]),
     F("value", "number"),
     F("battery", "int", required=False, min=0, max=100),
     F("tags", "list", required=False, max_len=5),
     F("tags[]", "string", regex=r"[a-z]{1,10}")],
    [C("VALUE_IMPLAUSIBLE", "`value` must lie in the plausible range for its unit, inclusive: C -50 to 150, "
       "F -58 to 302, K 223.15 to 423.15.", ["unit", "value"], _plausible)],
    bases=[{"sensor_id": "S001", "ts": "2024-06-01T12:00:00Z", "unit": "C", "value": 21.5, "battery": 87},
           {"sensor_id": "S999", "ts": "2023-01-01T00:00:59Z", "unit": "K", "value": 223.15, "tags": ["roof", "north"]},
           {"sensor_id": "S042", "ts": "2024-02-29T23:59:59Z", "unit": "F", "value": 302, "battery": 0, "tags": []}],
    samples=[S(0, {"sensor_id": "S01"}, "SENSOR_ID_FORMAT"),
             S(0, {"sensor_id": "s001"}, "SENSOR_ID_FORMAT"),
             S(0, {"ts": "2024-06-01 12:00:00"}, "TS_FORMAT"),
             S(0, {"ts": "2024-06-01T12:00:00+00:00"}, "TS_FORMAT"),
             S(0, {"ts": DEL}, "TS_MISSING"),
             S(0, {"unit": "c"}, "UNIT_ENUM"),
             S(0, {"unit": "R", "value": 1000}, "UNIT_ENUM"),
             S(0, {"value": 150.0}),
             S(0, {"value": 150.01}, "VALUE_IMPLAUSIBLE"),
             S(0, {"value": -50}),
             S(0, {"value": "21.5"}, "VALUE_TYPE"),
             S(0, {"value": None}, "VALUE_MISSING"),
             S(0, {"unit": "F", "value": -58.5}, "VALUE_IMPLAUSIBLE"),
             S(0, {"unit": "K", "value": 21.5}, "VALUE_IMPLAUSIBLE"),
             S(0, {"battery": 101}, "BATTERY_RANGE"),
             S(0, {"battery": -1}, "BATTERY_RANGE"),
             S(0, {"battery": 50.5}, "BATTERY_TYPE"),
             S(0, {"battery": None}),
             S(1, {"tags": ["a", "b", "c", "d", "e", "f"]}, "TAGS_LENGTH"),
             S(1, {"tags": ["Roof"]}, "TAGS_ITEM_FORMAT"),
             S(1, {"tags": ["roof", 7]}, "TAGS_ITEM_TYPE"),
             S(1, {"tags": "roof"}, "TAGS_TYPE"),
             S(1, {"tags": ["abcdefghijk"]}, "TAGS_ITEM_FORMAT"),
             S(1, {"value": 223.14}, "VALUE_IMPLAUSIBLE"),
             S(2, {"value": True}, "VALUE_TYPE"),
             R({"sensor_id": "S100", "unit": "C", "value": 0}, "TS_MISSING")]))


_POST = {"US": r"\d{5}(-\d{4})?", "GB": r"[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}", "DE": r"\d{5}", "FR": r"\d{5}",
         "CA": r"[A-Z]\d[A-Z] \d[A-Z]\d"}

add(Spec(
    "shipping_addresses", "Shipping addresses", "Records are shipping addresses entered at checkout.",
    [F("name", "string", min_len=1, max_len=70),
     F("phone", "string", required=False, regex=r"\+\d{7,15}"),
     F("address", "object"),
     F("address.line1", "string", min_len=1, max_len=100),
     F("address.line2", "string", required=False, max_len=100),
     F("address.city", "string", min_len=1, max_len=50),
     F("address.postcode", "string"),
     F("address.country", "string", enum=["US", "GB", "DE", "FR", "CA"])],
    [C("POSTCODE_FORMAT", "`address.postcode` must fully match the pattern for `address.country`: US "
       "`\\d{5}(-\\d{4})?`, GB `[A-Z]{1,2}\\d[A-Z\\d]? \\d[A-Z]{2}`, DE `\\d{5}`, FR `\\d{5}`, CA "
       "`[A-Z]\\d[A-Z] \\d[A-Z]\\d`.", ["address.postcode", "address.country"],
       lambda r: re.fullmatch(_POST[r["address"]["country"]], r["address"]["postcode"]) is not None)],
    bases=[{"name": "Grace Hopper", "phone": "+15551234567",
            "address": {"line1": "1 Navy Way", "city": "Arlington", "postcode": "22202-1234", "country": "US"}},
           {"name": "Ada L", "address": {"line1": "10 Downing St", "line2": "Flat 2", "city": "London",
                                          "postcode": "SW1A 2AA", "country": "GB"}},
           {"name": "Émile Zola", "address": {"line1": "5 rue X", "city": "Paris", "postcode": "75001",
                                              "country": "FR"}, "phone": "+3312345"}],
    samples=[S(0, {"name": ""}, "NAME_LENGTH"),
             S(0, {"name": "x" * 71}, "NAME_LENGTH"),
             S(0, {"phone": "5551234567"}, "PHONE_FORMAT"),
             S(0, {"phone": "+123456"}, "PHONE_FORMAT"),
             S(0, {"phone": None}),
             S(0, {"address": DEL}, "ADDRESS_MISSING"),
             S(0, {"address": "1 Navy Way, Arlington"}, "ADDRESS_TYPE"),
             S(0, {"address.line1": DEL}, "ADDRESS_LINE1_MISSING"),
             S(0, {"address.line2": 5}, "ADDRESS_LINE2_TYPE"),
             S(0, {"address.city": ""}, "ADDRESS_CITY_LENGTH"),
             S(0, {"address.postcode": "2220"}, "POSTCODE_FORMAT"),
             S(0, {"address.postcode": 22202}, "ADDRESS_POSTCODE_TYPE"),
             S(0, {"address.postcode": "22202"}),
             S(0, {"address.country": "USA"}, "ADDRESS_COUNTRY_ENUM"),
             S(0, {"address.country": "DE"}, "POSTCODE_FORMAT"),
             S(0, {"address.country": "DE", "address.postcode": "10115"}),
             S(1, {"address.postcode": "sw1a 2aa"}, "POSTCODE_FORMAT"),
             S(1, {"address.postcode": "M1 1AE"}),
             S(1, {"address.line2": "y" * 101}, "ADDRESS_LINE2_LENGTH"),
             S(1, {"address.country": "CA"}, "POSTCODE_FORMAT"),
             S(1, {"address.country": "CA", "address.postcode": "K1A 0B1"}),
             S(1, {"address.country": None, "address.postcode": "anything"}, "ADDRESS_COUNTRY_MISSING"),
             S(2, {"address.postcode": "7500"}, "POSTCODE_FORMAT"),
             S(2, {"name": None, "address.city": None}, "NAME_MISSING", "ADDRESS_CITY_MISSING"),
             R({"name": "N"}, "ADDRESS_MISSING")]))


_ROOMS = {"A": 20, "B": 50, "HALL": 500}

add(Spec(
    "room_bookings", "Room bookings", "Records are meeting-room bookings from an office booking tool.",
    [F("booking_id", "int", min=1),
     F("room", "string", enum=["A", "B", "HALL"]),
     F("start", "date"),
     F("end", "date"),
     F("attendees", "int", min=1, max=500),
     F("catering", "bool"),
     F("organizer", "string", regex=EMAIL)],
    [C("END_BEFORE_START", "`end` must not be earlier than `start`.", ["start", "end"],
       lambda r: date(r["end"]) >= date(r["start"])),
     C("OVER_CAPACITY", "`attendees` must not exceed the room's capacity: A 20, B 50, HALL 500.",
       ["room", "attendees"], lambda r: r["attendees"] <= _ROOMS[r["room"]]),
     C("CATERING_MIN", "When `catering` is true, `attendees` must be at least 10.", ["catering", "attendees"],
       lambda r: not r["catering"] or r["attendees"] >= 10)],
    bases=[{"booking_id": 17, "room": "B", "start": "2024-09-02", "end": "2024-09-03", "attendees": 12,
            "catering": True, "organizer": "kim@example.com"},
           {"booking_id": 1, "room": "A", "start": "2024-01-01", "end": "2024-01-01", "attendees": 20,
            "catering": False, "organizer": "a@b.cd"},
           {"booking_id": 99999, "room": "HALL", "start": "2024-12-31", "end": "2025-01-02", "attendees": 500,
            "catering": True, "organizer": "events@corp.example.com"}],
    samples=[S(0, {"booking_id": 0}, "BOOKING_ID_RANGE"),
             S(0, {"booking_id": "17"}, "BOOKING_ID_TYPE"),
             S(0, {"room": "C"}, "ROOM_ENUM"),
             S(0, {"room": "a"}, "ROOM_ENUM"),
             S(0, {"end": "2024-09-01"}, "END_BEFORE_START"),
             S(0, {"end": "2024-09-31"}, "END_FORMAT"),
             S(0, {"start": None}, "START_MISSING"),
             S(0, {"attendees": 51}, "OVER_CAPACITY"),
             S(0, {"attendees": 50}),
             S(0, {"attendees": 9}, "CATERING_MIN"),
             S(0, {"attendees": 9, "catering": False}),
             S(0, {"attendees": 0}, "ATTENDEES_RANGE"),
             S(0, {"attendees": 600}, "ATTENDEES_RANGE"),
             S(0, {"catering": "yes"}, "CATERING_TYPE"),
             S(0, {"catering": 1}, "CATERING_TYPE"),
             S(0, {"organizer": "kim"}, "ORGANIZER_FORMAT"),
             S(1, {"attendees": 21}, "OVER_CAPACITY"),
             S(1, {"attendees": 21, "room": "HALL"}),
             S(1, {"catering": True}),
             S(1, {"catering": True, "attendees": 5}, "CATERING_MIN"),
             S(2, {"attendees": 501}, "ATTENDEES_RANGE"),
             S(2, {"room": None, "attendees": 501}, "ROOM_MISSING", "ATTENDEES_RANGE"),
             S(2, {"start": "2025-01-03", "room": "B"}, "END_BEFORE_START", "OVER_CAPACITY"),
             S(2, {"attendees": 500.0}, "ATTENDEES_TYPE")]))


_SEPA = ("DE", "FR", "ES", "IT", "NL")

add(Spec(
    "transfers", "Bank transfers", "Records are outgoing bank transfer instructions.",
    [F("amount", "number", gt=0, max=1000000),
     F("currency", "string", enum=["EUR", "USD", "GBP", "CHF"]),
     F("from_iban", "string", regex=r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}"),
     F("to_iban", "string", regex=r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}"),
     F("reference", "string", required=False, max_len=35),
     F("execute_on", "date", required=False, min="2024-01-01")],
    [C("SAME_ACCOUNT", "`from_iban` and `to_iban` must differ.", ["from_iban", "to_iban"],
       lambda r: r["from_iban"] != r["to_iban"]),
     C("SEPA_CURRENCY", "When both IBANs start with one of DE, FR, ES, IT, NL (the first two letters), "
       "`currency` must be EUR.", ["from_iban", "to_iban", "currency"],
       lambda r: not (r["from_iban"][:2] in _SEPA and r["to_iban"][:2] in _SEPA) or r["currency"] == "EUR")],
    bases=[{"amount": 250.0, "currency": "EUR", "from_iban": "DE89370400440532013000",
            "to_iban": "FR1420041010050500013M02606", "reference": "Rent June"},
           {"amount": 0.01, "currency": "USD", "from_iban": "GB29NWBK60161331926819",
            "to_iban": "DE89370400440532013000", "execute_on": "2024-01-01"},
           {"amount": 1000000, "currency": "CHF", "from_iban": "CH9300762011623852957",
            "to_iban": "CH5604835012345678009", "reference": ""}],
    samples=[S(0, {"amount": 0}, "AMOUNT_RANGE"),
             S(0, {"amount": -5}, "AMOUNT_RANGE"),
             S(0, {"amount": "250"}, "AMOUNT_TYPE"),
             S(2, {"amount": 1000000.01}, "AMOUNT_RANGE"),
             S(0, {"currency": "JPY"}, "CURRENCY_ENUM"),
             S(0, {"currency": "USD"}, "SEPA_CURRENCY"),
             S(0, {"currency": "JPY", "to_iban": "DE89370400440532013000"}, "CURRENCY_ENUM", "SAME_ACCOUNT"),
             S(0, {"from_iban": "de89370400440532013000"}, "FROM_IBAN_FORMAT"),
             S(0, {"from_iban": "DE8937040044"}, "FROM_IBAN_FORMAT"),
             S(0, {"from_iban": "DE89370400"}, "FROM_IBAN_FORMAT"),
             S(0, {"to_iban": None}, "TO_IBAN_MISSING"),
             S(0, {"to_iban": "DE89370400440532013000"}, "SAME_ACCOUNT"),
             S(0, {"reference": "r" * 36}, "REFERENCE_LENGTH"),
             S(0, {"reference": "r" * 35}),
             S(0, {"reference": 12}, "REFERENCE_TYPE"),
             S(0, {"execute_on": "2023-12-31"}, "EXECUTE_ON_RANGE"),
             S(0, {"execute_on": "2024-1-5"}, "EXECUTE_ON_FORMAT"),
             S(1, {"currency": "EUR"}),
             S(1, {"from_iban": "NL91ABNA0417164300"}, "SEPA_CURRENCY"),
             S(1, {"from_iban": "NL91ABNA0417164300", "currency": "GBP"}, "SEPA_CURRENCY"),
             S(2, {"to_iban": "CH9300762011623852957"}, "SAME_ACCOUNT"),
             S(2, {"amount": None, "currency": None}, "AMOUNT_MISSING", "CURRENCY_MISSING"),
             S(0, {"from_iban": "DE89370400440532013000X", "currency": "USD"}, "SEPA_CURRENCY")]))


add(Spec(
    "products", "Catalogue products", "Records are products in a shop catalogue.",
    [F("sku", "string", regex=r"[A-Z0-9]{8}"),
     F("name", "string", min_len=1, max_len=80),
     F("price", "number", min=0),
     F("sale_price", "number", required=False, min=0),
     F("stock", "int", min=0),
     F("tags", "list", max_len=10),
     F("tags[]", "string", regex=r"[a-z0-9-]{1,20}"),
     F("dimensions", "object", required=False),
     F("dimensions.w", "number", min=0.1, max=500),
     F("dimensions.h", "number", min=0.1, max=500),
     F("dimensions.d", "number", min=0.1, max=500)],
    [C("SALE_NOT_LOWER", "`sale_price` must be lower than `price`.", ["price", "sale_price"],
       lambda r: r["sale_price"] < r["price"]),
     C("TAGS_DUPLICATE", "`tags` must not repeat a tag.", ["tags"], lambda r: len(set(r["tags"])) == len(r["tags"]))],
    bases=[{"sku": "AB12CD34", "name": "Desk lamp", "price": 39.9, "sale_price": 29.9, "stock": 12,
            "tags": ["lighting", "desk"], "dimensions": {"w": 15, "h": 45.5, "d": 15}},
           {"sku": "00000000", "name": "Gift card", "price": 0, "stock": 0, "tags": []},
           {"sku": "ZZZZZZZZ", "name": "Rug", "price": 120, "stock": 3, "tags": ["home"],
            "dimensions": {"w": 500, "h": 0.1, "d": 300}}],
    samples=[S(0, {"sku": "ab12cd34"}, "SKU_FORMAT"),
             S(0, {"sku": "AB12CD3"}, "SKU_FORMAT"),
             S(0, {"name": ""}, "NAME_LENGTH"),
             S(0, {"price": -0.01}, "PRICE_RANGE"),
             S(0, {"price": "39.90"}, "PRICE_TYPE"),
             S(0, {"sale_price": 39.9}, "SALE_NOT_LOWER"),
             S(0, {"sale_price": 45}, "SALE_NOT_LOWER"),
             S(0, {"sale_price": -1}, "SALE_PRICE_RANGE"),
             S(0, {"sale_price": None}),
             S(0, {"stock": -1}, "STOCK_RANGE"),
             S(0, {"stock": 1.5}, "STOCK_TYPE"),
             S(0, {"tags": ["Lighting"]}, "TAGS_ITEM_FORMAT"),
             S(0, {"tags": ["desk", "desk"]}, "TAGS_DUPLICATE"),
             S(0, {"tags": [f"t{i}" for i in range(11)]}, "TAGS_LENGTH"),
             S(0, {"tags": ["a b"]}, "TAGS_ITEM_FORMAT"),
             S(0, {"tags": DEL}, "TAGS_MISSING"),
             S(0, {"dimensions.w": 0}, "DIMENSIONS_W_RANGE"),
             S(0, {"dimensions.h": 501, "dimensions.d": "15"}, "DIMENSIONS_H_RANGE", "DIMENSIONS_D_TYPE"),
             S(0, {"dimensions.d": DEL}, "DIMENSIONS_D_MISSING"),
             S(0, {"dimensions": [15, 45.5, 15]}, "DIMENSIONS_TYPE"),
             S(0, {"dimensions": None}),
             S(1, {"sale_price": 0}, "SALE_NOT_LOWER"),
             S(1, {"price": None, "sale_price": 5}, "PRICE_MISSING"),
             S(2, {"dimensions": {}}, "DIMENSIONS_W_MISSING", "DIMENSIONS_H_MISSING", "DIMENSIONS_D_MISSING"),
             S(2, {"tags": [5, 5]}, "TAGS_ITEM_TYPE")]))


add(Spec(
    "employees", "Employee records", "Records are employee rows synced from an HR system.",
    [F("emp_id", "int", min=1, max=999999),
     F("first_name", "string", min_len=1, max_len=40),
     F("last_name", "string", min_len=1, max_len=40),
     F("dept", "string", enum=["ENG", "SALES", "OPS", "HR", "FIN"]),
     F("salary", "int", min=20000, max=500000),
     F("manager_id", "int", required=False, min=1, max=999999),
     F("hire_date", "date", min="1990-01-01"),
     F("end_date", "date", required=False)],
    [C("SELF_MANAGER", "`manager_id` must differ from `emp_id`.", ["emp_id", "manager_id"],
       lambda r: r["manager_id"] != r["emp_id"]),
     C("END_BEFORE_HIRE", "`end_date` must be later than `hire_date` (the same day is not allowed).",
       ["hire_date", "end_date"], lambda r: date(r["end_date"]) > date(r["hire_date"])),
     C("SALARY_BAND", "Salaries in `HR` and `OPS` must not exceed 200000.", ["dept", "salary"],
       lambda r: r["dept"] not in ("HR", "OPS") or r["salary"] <= 200000)],
    bases=[{"emp_id": 1042, "first_name": "Lin", "last_name": "Wei", "dept": "ENG", "salary": 145000,
            "manager_id": 17, "hire_date": "2019-04-15"},
           {"emp_id": 1, "first_name": "A", "last_name": "B", "dept": "HR", "salary": 200000,
            "hire_date": "1990-01-01", "end_date": "1990-01-02"},
           {"emp_id": 999999, "first_name": "Sam", "last_name": "O'Neil", "dept": "OPS", "salary": 20000,
            "manager_id": 999998, "hire_date": "2024-02-29", "end_date": "2025-02-28"}],
    samples=[S(0, {"emp_id": 0}, "EMP_ID_RANGE"),
             S(0, {"emp_id": 1000000}, "EMP_ID_RANGE"),
             S(0, {"emp_id": "1042"}, "EMP_ID_TYPE"),
             S(0, {"first_name": ""}, "FIRST_NAME_LENGTH"),
             S(0, {"last_name": None}, "LAST_NAME_MISSING"),
             S(0, {"dept": "Eng"}, "DEPT_ENUM"),
             S(0, {"salary": 19999}, "SALARY_RANGE"),
             S(0, {"salary": 145000.0}, "SALARY_TYPE"),
             S(0, {"manager_id": 1042}, "SELF_MANAGER"),
             S(0, {"manager_id": 0}, "MANAGER_ID_RANGE"),
             S(0, {"manager_id": None}),
             S(0, {"hire_date": "1989-12-31"}, "HIRE_DATE_RANGE"),
             S(0, {"hire_date": "2019-4-15"}, "HIRE_DATE_FORMAT"),
             S(0, {"end_date": "2019-04-15"}, "END_BEFORE_HIRE"),
             S(0, {"end_date": "2019-04-16"}),
             S(0, {"end_date": "2019-04-31"}, "END_DATE_FORMAT"),
             S(0, {"dept": "HR", "salary": 200001}, "SALARY_BAND"),
             S(0, {"dept": "FIN", "salary": 300000}),
             S(1, {"salary": 200001}, "SALARY_BAND"),
             S(1, {"end_date": "1989-12-31"}, "END_BEFORE_HIRE"),
             S(1, {"dept": "LEGAL", "salary": 400000}, "DEPT_ENUM"),
             S(2, {"manager_id": 999999}, "SELF_MANAGER"),
             S(2, {"emp_id": 1000000, "manager_id": 1000000}, "EMP_ID_RANGE", "MANAGER_ID_RANGE"),
             S(2, {"hire_date": None, "end_date": "1900-01-01"}, "HIRE_DATE_MISSING")]))


def _infant_ok(r):
    ages = [p["age"] for p in r["passengers"]]
    return not any(a < 2 for a in ages) or any(a >= 18 for a in ages)


def _seats_ok(r):
    seats = [p["seat"] for p in r["passengers"] if p.get("seat") is not None]
    return len(seats) == len(set(seats))


add(Spec(
    "flight_bookings", "Flight bookings", "Records are flight bookings from a travel site.",
    [F("flight", "string", regex=r"[A-Z]{2}\d{1,4}"),
     F("origin", "string", regex=r"[A-Z]{3}"),
     F("destination", "string", regex=r"[A-Z]{3}"),
     F("depart", "date"),
     F("return", "date", required=False),
     F("passengers", "list", min_len=1, max_len=9),
     F("passengers[]", "object"),
     F("passengers[].name", "string", min_len=1, max_len=60),
     F("passengers[].age", "int", min=0, max=120),
     F("passengers[].seat", "string", required=False, regex=r"\d{1,2}[A-F]")],
    [C("SAME_AIRPORT", "`origin` and `destination` must differ.", ["origin", "destination"],
       lambda r: r["origin"] != r["destination"]),
     C("RETURN_BEFORE_DEPART", "`return` must not be earlier than `depart`.", ["depart", "return"],
       lambda r: date(r["return"]) >= date(r["depart"])),
     C("INFANT_WITHOUT_ADULT", "If any passenger is younger than 2, at least one passenger must be 18 or older.",
       ["passengers"], _infant_ok),
     C("SEAT_TAKEN", "No two passengers may have the same `seat`.", ["passengers"], _seats_ok)],
    bases=[{"flight": "LH123", "origin": "FRA", "destination": "JFK", "depart": "2024-07-01", "return": "2024-07-15",
            "passengers": [{"name": "Max", "age": 40, "seat": "12A"}, {"name": "Mia", "age": 1}]},
           {"flight": "BA1", "origin": "LHR", "destination": "SIN", "depart": "2024-01-10",
            "passengers": [{"name": "Solo", "age": 18, "seat": "1F"}]},
           {"flight": "UA9999", "origin": "SFO", "destination": "ORD", "depart": "2024-03-03", "return": "2024-03-03",
            "passengers": [{"name": "Kid", "age": 2}, {"name": "Kid2", "age": 17, "seat": "30B"}]}],
    samples=[S(0, {"flight": "LH12345"}, "FLIGHT_FORMAT"),
             S(0, {"flight": "lh123"}, "FLIGHT_FORMAT"),
             S(0, {"origin": "FR"}, "ORIGIN_FORMAT"),
             S(0, {"destination": "FRA"}, "SAME_AIRPORT"),
             S(0, {"destination": "fra"}, "DESTINATION_FORMAT"),
             S(0, {"return": "2024-06-30"}, "RETURN_BEFORE_DEPART"),
             S(0, {"return": None}),
             S(0, {"return": "2024-07-32"}, "RETURN_FORMAT"),
             S(0, {"depart": DEL}, "DEPART_MISSING"),
             S(0, {"passengers": []}, "PASSENGERS_LENGTH"),
             S(0, {"passengers.0.age": 17}, "INFANT_WITHOUT_ADULT"),
             S(0, {"passengers.0.age": 121}, "PASSENGERS_AGE_RANGE"),
             S(0, {"passengers.1.age": "1"}, "PASSENGERS_AGE_TYPE"),
             S(0, {"passengers.1.seat": "12A"}, "SEAT_TAKEN"),
             S(0, {"passengers.1.seat": "12G"}, "PASSENGERS_SEAT_FORMAT"),
             S(0, {"passengers.0.seat": "123A"}, "PASSENGERS_SEAT_FORMAT"),
             S(0, {"passengers.0.name": ""}, "PASSENGERS_NAME_LENGTH"),
             S(0, {"passengers.0.age": 17, "passengers.1.seat": "12A"}, "INFANT_WITHOUT_ADULT", "SEAT_TAKEN"),
             S(0, {"passengers.0.age": 17, "passengers.1.seat": "99Z"}, "PASSENGERS_SEAT_FORMAT"),
             S(1, {"passengers": [{"name": f"P{i}", "age": 30} for i in range(10)]}, "PASSENGERS_LENGTH"),
             S(1, {"passengers.0": "Solo"}, "PASSENGERS_ITEM_TYPE"),
             S(1, {"passengers.0.age": DEL}, "PASSENGERS_AGE_MISSING"),
             S(2, {"passengers.0.age": 1}, "INFANT_WITHOUT_ADULT"),
             S(2, {"passengers.0.seat": "30B"}, "SEAT_TAKEN"),
             S(2, {"passengers.1.seat": None, "passengers.0.seat": None})]))


add(Spec(
    "server_configs", "Server configuration", "Records are server configuration files (parsed from JSON). "
    "Unknown keys are not allowed (they are usually typos).",
    [F("host", "string", regex=r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"),
     F("port", "int", min=1, max=65535),
     F("tls", "bool"),
     F("cert_path", "string", required=False, regex=r"/[^\s]+\.pem"),
     F("workers", "int", min=1, max=64),
     F("log_level", "string", enum=["debug", "info", "warning", "error"]),
     F("timeout_s", "number", min=0.1, max=300)],
    [C("TLS_NEEDS_CERT", "When `tls` is true, `cert_path` must be present.", ["tls"],
       lambda r: not r["tls"] or r.get("cert_path") is not None, optional=["cert_path"]),
     C("PRIVILEGED_PORT", "`port` below 1024 is only allowed when `tls` is true and `port` is 443.",
       ["port", "tls"], lambda r: r["port"] >= 1024 or (r["tls"] and r["port"] == 443))],
    strict=True,
    bases=[{"host": "api.example.com", "port": 8443, "tls": True, "cert_path": "/etc/ssl/api.pem", "workers": 8,
            "log_level": "info", "timeout_s": 30},
           {"host": "localhost", "port": 1024, "tls": False, "workers": 1, "log_level": "debug", "timeout_s": 0.1},
           {"host": "a-1.b-2.internal", "port": 443, "tls": True, "cert_path": "/c.pem", "workers": 64,
            "log_level": "error", "timeout_s": 300}],
    samples=[S(0, {"host": "API.example.com"}, "HOST_FORMAT"),
             S(0, {"host": "-api.example.com"}, "HOST_FORMAT"),
             S(0, {"host": "api..example.com"}, "HOST_FORMAT"),
             S(0, {"port": 0}, "PORT_RANGE"),
             S(0, {"port": 65536}, "PORT_RANGE"),
             S(0, {"port": "8443"}, "PORT_TYPE"),
             S(0, {"port": 80}, "PRIVILEGED_PORT"),
             S(0, {"port": 443}),
             S(0, {"tls": "true"}, "TLS_TYPE"),
             S(0, {"cert_path": DEL}, "TLS_NEEDS_CERT"),
             S(0, {"cert_path": "etc/ssl/api.pem"}, "CERT_PATH_FORMAT"),
             S(0, {"cert_path": "/etc/ssl/api.crt"}, "CERT_PATH_FORMAT"),
             S(0, {"workers": 0}, "WORKERS_RANGE"),
             S(0, {"workers": 65}, "WORKERS_RANGE"),
             S(0, {"log_level": "INFO"}, "LOG_LEVEL_ENUM"),
             S(0, {"timeout_s": 0}, "TIMEOUT_S_RANGE"),
             S(0, {"timeout_s": "30"}, "TIMEOUT_S_TYPE"),
             S(0, {"timeout": 30}, "UNKNOWN_FIELD"),
             S(1, {"tls": True}, "TLS_NEEDS_CERT"),
             S(1, {"port": 443}, "PRIVILEGED_PORT"),
             S(1, {"port": 1023, "tls": None}, "TLS_MISSING"),
             S(1, {"wrkers": 2, "workers": DEL}, "UNKNOWN_FIELD", "WORKERS_MISSING"),
             S(2, {"cert_path": None}, "TLS_NEEDS_CERT"),
             S(2, {"timeout_s": 300.5, "workers": 64.0}, "TIMEOUT_S_RANGE", "WORKERS_TYPE")]))


def _no_dup_q(r):
    qs = [a["q"] for a in r["answers"]]
    return len(qs) == len(set(qs))


add(Spec(
    "survey_responses", "Survey responses", "Records are responses to a 20-question customer survey.",
    [F("respondent_id", "string", regex=r"R-[0-9a-f]{8}"),
     F("submitted", "date", min="2024-01-01", max="2024-12-31"),
     F("answers", "list", min_len=1, max_len=20),
     F("answers[]", "object"),
     F("answers[].q", "int", min=1, max=20),
     F("answers[].score", "int", min=1, max=5),
     F("nps", "int", required=False, min=0, max=10),
     F("comment", "string", required=False, max_len=500)],
    [C("DUPLICATE_QUESTION", "No question number `q` may be answered twice.", ["answers"], _no_dup_q),
     C("COMMENT_REQUIRED", "When `nps` is 6 or lower, `comment` must be present and not empty.", ["nps"],
       lambda r: r["nps"] > 6 or bool(r.get("comment")), optional=["comment"])],
    bases=[{"respondent_id": "R-0a1b2c3d", "submitted": "2024-05-20",
            "answers": [{"q": 1, "score": 5}, {"q": 2, "score": 4}, {"q": 7, "score": 3}], "nps": 9},
           {"respondent_id": "R-ffffffff", "submitted": "2024-01-01", "answers": [{"q": 20, "score": 1}],
            "nps": 3, "comment": "Delivery was slow."},
           {"respondent_id": "R-00000000", "submitted": "2024-12-31",
            "answers": [{"q": q, "score": 1 + q % 5} for q in range(1, 21)]}],
    samples=[S(0, {"respondent_id": "R-0A1B2C3D"}, "RESPONDENT_ID_FORMAT"),
             S(0, {"respondent_id": "R-0a1b2c3"}, "RESPONDENT_ID_FORMAT"),
             S(0, {"submitted": "2023-12-31"}, "SUBMITTED_RANGE"),
             S(0, {"submitted": "2025-01-01"}, "SUBMITTED_RANGE"),
             S(0, {"submitted": "20-05-2024"}, "SUBMITTED_FORMAT"),
             S(0, {"answers": []}, "ANSWERS_LENGTH"),
             S(0, {"answers.0.q": 0}, "ANSWERS_Q_RANGE"),
             S(0, {"answers.0.q": 21}, "ANSWERS_Q_RANGE"),
             S(0, {"answers.1.q": 1}, "DUPLICATE_QUESTION"),
             S(0, {"answers.1.q": 1, "answers.2.score": 6}, "ANSWERS_SCORE_RANGE"),
             S(0, {"answers.2.score": 0}, "ANSWERS_SCORE_RANGE"),
             S(0, {"answers.2.score": "3"}, "ANSWERS_SCORE_TYPE"),
             S(0, {"answers.0": [1, 5]}, "ANSWERS_ITEM_TYPE"),
             S(0, {"nps": 11}, "NPS_RANGE"),
             S(0, {"nps": 6}, "COMMENT_REQUIRED"),
             S(0, {"nps": 7}),
             S(0, {"nps": None}),
             S(0, {"nps": 0, "comment": ""}, "COMMENT_REQUIRED"),
             S(1, {"comment": "x" * 501}, "COMMENT_LENGTH"),
             S(1, {"comment": DEL}, "COMMENT_REQUIRED"),
             S(1, {"comment": 42}, "COMMENT_TYPE"),
             S(2, {"answers": [{"q": q, "score": 3} for q in range(1, 22)]}, "ANSWERS_LENGTH", "ANSWERS_Q_RANGE"),
             S(2, {"answers.19.q": 1}, "DUPLICATE_QUESTION"),
             R({"respondent_id": "R-12345678", "submitted": "2024-06-06", "answers": [{"q": 3}]},
               "ANSWERS_SCORE_MISSING")]))


SEMVER = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?"


def _deps_unique(r):
    names = [d["name"] for d in r["dependencies"]]
    return len(names) == len(set(names))


add(Spec(
    "package_manifests", "Package manifests", "Records are package manifests for an internal package registry.",
    [F("name", "string", regex=r"[a-z0-9][a-z0-9-]{0,39}"),
     F("version", "string", regex=SEMVER),
     F("license", "string", enum=["MIT", "Apache-2.0", "BSD-3-Clause", "UNLICENSED"]),
     F("private", "bool"),
     F("dependencies", "list", max_len=30),
     F("dependencies[]", "object"),
     F("dependencies[].name", "string", regex=r"[a-z0-9][a-z0-9-]{0,39}"),
     F("dependencies[].range", "string", regex=r"[\^~]?\d+\.\d+\.\d+")],
    [C("SELF_DEPENDENCY", "No dependency may have the package's own `name`.", ["name", "dependencies"],
       lambda r: all(d["name"] != r["name"] for d in r["dependencies"])),
     C("DUPLICATE_DEPENDENCY", "No dependency name may appear twice.", ["dependencies"], _deps_unique),
     C("UNLICENSED_PUBLIC", "`license` `UNLICENSED` is only allowed when `private` is true.", ["license", "private"],
       lambda r: r["license"] != "UNLICENSED" or r["private"])],
    bases=[{"name": "billing-core", "version": "2.4.1", "license": "MIT", "private": False,
            "dependencies": [{"name": "money-utils", "range": "^1.2.0"}, {"name": "tz", "range": "3.0.0"}]},
           {"name": "x", "version": "0.0.1-beta.2", "license": "UNLICENSED", "private": True, "dependencies": []},
           {"name": "a1-b2", "version": "10.20.30", "license": "Apache-2.0", "private": True,
            "dependencies": [{"name": "billing-core", "range": "~2.4.0"}]}],
    samples=[S(0, {"name": "Billing-Core"}, "NAME_FORMAT"),
             S(0, {"name": "-billing"}, "NAME_FORMAT"),
             S(0, {"name": "b" * 41}, "NAME_FORMAT"),
             S(0, {"version": "2.4"}, "VERSION_FORMAT"),
             S(0, {"version": "02.4.1"}, "VERSION_FORMAT"),
             S(0, {"version": "v2.4.1"}, "VERSION_FORMAT"),
             S(0, {"version": 2.4}, "VERSION_TYPE"),
             S(0, {"license": "GPL-3.0"}, "LICENSE_ENUM"),
             S(0, {"license": "UNLICENSED"}, "UNLICENSED_PUBLIC"),
             S(0, {"private": "no"}, "PRIVATE_TYPE"),
             S(0, {"private": None, "license": "UNLICENSED"}, "PRIVATE_MISSING"),
             S(0, {"dependencies.1.name": "money-utils"}, "DUPLICATE_DEPENDENCY"),
             S(0, {"dependencies.1.name": "billing-core"}, "SELF_DEPENDENCY"),
             S(0, {"dependencies.0.range": ">=1.2.0"}, "DEPENDENCIES_RANGE_FORMAT"),
             S(0, {"dependencies.0.range": "^1.2"}, "DEPENDENCIES_RANGE_FORMAT"),
             S(0, {"dependencies.0.range": DEL}, "DEPENDENCIES_RANGE_MISSING"),
             S(0, {"dependencies.0.name": "Money"}, "DEPENDENCIES_NAME_FORMAT"),
             S(0, {"dependencies": {"tz": "3.0.0"}}, "DEPENDENCIES_TYPE"),
             S(0, {"dependencies": [{"name": f"d{i}", "range": "1.0.0"} for i in range(31)]}, "DEPENDENCIES_LENGTH"),
             S(1, {"private": False}, "UNLICENSED_PUBLIC"),
             S(1, {"dependencies": DEL}, "DEPENDENCIES_MISSING"),
             S(2, {"name": "billing-core"}, "SELF_DEPENDENCY"),
             S(2, {"name": "billing-core", "dependencies.0.range": "2.4"}, "DEPENDENCIES_RANGE_FORMAT"),
             S(2, {"dependencies.0": "billing-core"}, "DEPENDENCIES_ITEM_TYPE")]))


def _within_week(r):
    start = date(r["week_start"])
    return all(0 <= (date(e["day"]) - start).days <= 6 for e in r["entries"])


def _days_unique(r):
    days = [e["day"] for e in r["entries"]]
    return len(days) == len(set(days))


add(Spec(
    "timesheets", "Weekly timesheets", "Records are weekly timesheets submitted by contractors.",
    [F("employee", "string", regex=r"E\d{5}"),
     F("week_start", "date"),
     F("entries", "list", min_len=1, max_len=7),
     F("entries[]", "object"),
     F("entries[].day", "date"),
     F("entries[].hours", "number", min=0, max=24),
     F("entries[].project", "string", regex=r"P\d{3}"),
     F("approved", "bool", required=False)],
    [C("WEEK_START_NOT_MONDAY", "`week_start` must be a Monday.", ["week_start"],
       lambda r: date(r["week_start"]).weekday() == 0),
     C("ENTRY_OUTSIDE_WEEK", "Every entry's `day` must fall within `week_start` .. `week_start` + 6 days.",
       ["week_start", "entries"], _within_week),
     C("DUPLICATE_DAY", "No two entries may have the same `day`.", ["entries"], _days_unique),
     C("OVERTIME", "The entries' `hours` must sum to at most 60.", ["entries"],
       lambda r: sum(e["hours"] for e in r["entries"]) <= 60)],
    bases=[{"employee": "E00042", "week_start": "2024-06-03",
            "entries": [{"day": "2024-06-03", "hours": 8, "project": "P101"},
                        {"day": "2024-06-04", "hours": 7.5, "project": "P102"}], "approved": False},
           {"employee": "E99999", "week_start": "2024-12-30",
            "entries": [{"day": "2025-01-05", "hours": 0, "project": "P000"}]},
           {"employee": "E00001", "week_start": "2024-02-26",
            "entries": [{"day": f"2024-0{2 if d < 4 else 3}-{(26 + d) if d < 4 else (d - 3):02d}", "hours": 8.5,
                         "project": "P007"} for d in range(7)]}],
    samples=[S(0, {"employee": "E42"}, "EMPLOYEE_FORMAT"),
             S(0, {"employee": 42}, "EMPLOYEE_TYPE"),
             S(0, {"week_start": "2024-06-04", "entries.0.day": "2024-06-05"}, "WEEK_START_NOT_MONDAY"),
             S(0, {"week_start": "2024-06-04"}, "WEEK_START_NOT_MONDAY", "ENTRY_OUTSIDE_WEEK"),
             S(0, {"week_start": "2024-06-31"}, "WEEK_START_FORMAT"),
             S(0, {"entries.1.day": "2024-06-10"}, "ENTRY_OUTSIDE_WEEK"),
             S(0, {"entries.1.day": "2024-06-09"}),
             S(0, {"entries.1.day": "2024-06-02"}, "ENTRY_OUTSIDE_WEEK"),
             S(0, {"entries.1.day": "2024-06-03"}, "DUPLICATE_DAY"),
             S(0, {"entries.1.day": "June 4"}, "ENTRIES_DAY_FORMAT"),
             S(0, {"entries.0.hours": 24.5}, "ENTRIES_HOURS_RANGE"),
             S(0, {"entries.0.hours": -1}, "ENTRIES_HOURS_RANGE"),
             S(0, {"entries.0.hours": "8"}, "ENTRIES_HOURS_TYPE"),
             S(0, {"entries.0.project": "P1010"}, "ENTRIES_PROJECT_FORMAT"),
             S(0, {"entries": []}, "ENTRIES_LENGTH"),
             S(0, {"approved": "false"}, "APPROVED_TYPE"),
             S(0, {"approved": None}),
             S(1, {"week_start": "2024-12-31"}, "WEEK_START_NOT_MONDAY"),
             S(1, {"entries.0.hours": 24}),
             S(2, {"approved": True}),
             S(2, {"entries.6.hours": 9.1}, "OVERTIME"),
             S(2, {"entries.6.hours": 9}, ),
             S(2, {"entries.6.day": "2024-02-26", "entries.5.hours": 30}, "ENTRIES_HOURS_RANGE"),
             S(2, {"entries.6.day": "2024-02-29", "entries.5.hours": 20}, "DUPLICATE_DAY", "OVERTIME")]))


add(Spec(
    "coupons", "Discount coupons", "Records are discount coupons configured in a promotions tool.",
    [F("code", "string", regex=r"[A-Z0-9]{4,12}"),
     F("kind", "string", enum=["percent", "fixed"]),
     F("value", "number", gt=0),
     F("min_order", "number", required=False, min=0),
     F("valid_from", "date"),
     F("valid_to", "date"),
     F("max_uses", "int", required=False, min=1),
     F("channels", "list", min_len=1),
     F("channels[]", "string", enum=["web", "app", "store"])],
    [C("PERCENT_OVER_100", "When `kind` is `percent`, `value` must be at most 100.", ["kind", "value"],
       lambda r: r["kind"] != "percent" or r["value"] <= 100),
     C("WINDOW_INVALID", "`valid_to` must be on or after `valid_from`.", ["valid_from", "valid_to"],
       lambda r: date(r["valid_to"]) >= date(r["valid_from"])),
     C("FIXED_EXCEEDS_MIN_ORDER", "When `kind` is `fixed` and `min_order` is present, `value` must not exceed "
       "`min_order`.", ["kind", "value", "min_order"], lambda r: r["kind"] != "fixed" or r["value"] <= r["min_order"])],
    bases=[{"code": "SUMMER24", "kind": "percent", "value": 15, "valid_from": "2024-06-01", "valid_to": "2024-08-31",
            "channels": ["web", "app"]},
           {"code": "TENOFF", "kind": "fixed", "value": 10, "min_order": 50, "valid_from": "2024-01-01",
            "valid_to": "2024-01-01", "max_uses": 1, "channels": ["store"]},
           {"code": "ABCDEFGH1234", "kind": "percent", "value": 100, "min_order": 0, "valid_from": "2024-02-29",
            "valid_to": "2025-02-28", "channels": ["web", "web"]}],
    samples=[S(0, {"code": "summer24"}, "CODE_FORMAT"),
             S(0, {"code": "ABC"}, "CODE_FORMAT"),
             S(0, {"code": "ABCDEFGHIJKLM"}, "CODE_FORMAT"),
             S(0, {"kind": "bogo"}, "KIND_ENUM"),
             S(0, {"value": 0}, "VALUE_RANGE"),
             S(0, {"value": 100.5}, "PERCENT_OVER_100"),
             S(0, {"value": "15"}, "VALUE_TYPE"),
             S(0, {"valid_to": "2024-05-31"}, "WINDOW_INVALID"),
             S(0, {"valid_to": "2024-06-01"}),
             S(0, {"valid_from": "2024-06-1"}, "VALID_FROM_FORMAT"),
             S(0, {"max_uses": 0}, "MAX_USES_RANGE"),
             S(0, {"max_uses": 1.0}, "MAX_USES_TYPE"),
             S(0, {"channels": []}, "CHANNELS_LENGTH"),
             S(0, {"channels": ["email"]}, "CHANNELS_ITEM_ENUM"),
             S(0, {"channels": "web"}, "CHANNELS_TYPE"),
             S(0, {"min_order": -1}, "MIN_ORDER_RANGE"),
             S(0, {"kind": "fixed", "value": 500, "min_order": 20}, "FIXED_EXCEEDS_MIN_ORDER"),
             S(1, {"value": 50}),
             S(1, {"value": 50.01}, "FIXED_EXCEEDS_MIN_ORDER"),
             S(1, {"value": 500, "min_order": DEL}),
             S(1, {"value": 500, "min_order": "50"}, "MIN_ORDER_TYPE"),
             S(1, {"kind": "percent", "value": 150}, "PERCENT_OVER_100"),
             S(2, {"kind": None, "value": 1000}, "KIND_MISSING"),
             S(2, {"valid_to": "2024-02-28", "max_uses": -3}, "WINDOW_INVALID", "MAX_USES_RANGE")]))


add(Spec(
    "weather_daily", "Daily weather observations", "Records are daily summaries from automatic weather stations.",
    [F("station", "string", regex=r"[A-Z]{4}"),
     F("date", "date", min="1950-01-01"),
     F("t_min", "number", min=-90, max=60),
     F("t_max", "number", min=-90, max=60),
     F("precip_mm", "number", min=0, max=500),
     F("wind", "object", required=False),
     F("wind.speed_kmh", "number", min=0, max=400),
     F("wind.dir_deg", "int", min=0, max=359),
     F("wind.gust_kmh", "number", required=False, min=0, max=500)],
    [C("TMIN_ABOVE_TMAX", "`t_min` must not exceed `t_max`.", ["t_min", "t_max"], lambda r: r["t_min"] <= r["t_max"]),
     C("GUST_BELOW_SPEED", "`wind.gust_kmh`, when present, must be at least `wind.speed_kmh`.",
       ["wind.speed_kmh", "wind.gust_kmh"], lambda r: r["wind"]["gust_kmh"] >= r["wind"]["speed_kmh"])],
    bases=[{"station": "EGLL", "date": "2024-07-19", "t_min": 14.2, "t_max": 27.9, "precip_mm": 0,
            "wind": {"speed_kmh": 12, "dir_deg": 240, "gust_kmh": 30}},
           {"station": "KJFK", "date": "1950-01-01", "t_min": -5, "t_max": -5, "precip_mm": 12.7},
           {"station": "NZSP", "date": "2023-08-01", "t_min": -90, "t_max": -60, "precip_mm": 0,
            "wind": {"speed_kmh": 0, "dir_deg": 0}}],
    samples=[S(0, {"station": "EGL"}, "STATION_FORMAT"),
             S(0, {"station": "egll"}, "STATION_FORMAT"),
             S(0, {"date": "1949-12-31"}, "DATE_RANGE"),
             S(0, {"date": "2024-07-19T00:00"}, "DATE_FORMAT"),
             S(0, {"t_min": 28}, "TMIN_ABOVE_TMAX"),
             S(0, {"t_min": 27.9}),
             S(0, {"t_min": 61, "t_max": 62}, "T_MIN_RANGE", "T_MAX_RANGE"),
             S(0, {"t_min": 61}, "T_MIN_RANGE"),
             S(0, {"t_max": "27.9"}, "T_MAX_TYPE"),
             S(0, {"t_max": None}, "T_MAX_MISSING"),
             S(0, {"precip_mm": -0.1}, "PRECIP_MM_RANGE"),
             S(0, {"precip_mm": 500.1}, "PRECIP_MM_RANGE"),
             S(0, {"wind.speed_kmh": 31}, "GUST_BELOW_SPEED"),
             S(0, {"wind.gust_kmh": 12}),
             S(0, {"wind.gust_kmh": None, "wind.speed_kmh": 300}),
             S(0, {"wind.dir_deg": 360}, "WIND_DIR_DEG_RANGE"),
             S(0, {"wind.dir_deg": 240.0}, "WIND_DIR_DEG_TYPE"),
             S(0, {"wind.speed_kmh": -1}, "WIND_SPEED_KMH_RANGE"),
             S(0, {"wind": "12 km/h SW"}, "WIND_TYPE"),
             S(0, {"wind.dir_deg": DEL}, "WIND_DIR_DEG_MISSING"),
             S(1, {"wind": {"speed_kmh": 5}}, "WIND_DIR_DEG_MISSING"),
             S(1, {"wind": {}}, "WIND_SPEED_KMH_MISSING", "WIND_DIR_DEG_MISSING"),
             S(2, {"t_min": -90.5}, "T_MIN_RANGE"),
             S(2, {"t_max": -91, "t_min": -50}, "T_MAX_RANGE")]))


def _isbn_ok(r):
    d = [int(c) for c in r["isbn"]]
    return sum(x * (1 if i % 2 == 0 else 3) for i, x in enumerate(d)) % 10 == 0


add(Spec(
    "library_loans", "Library loans", "Records are book loans from a public library's circulation system.",
    [F("loan_id", "int", min=1),
     F("isbn", "string", regex=r"97[89]\d{10}"),
     F("member_id", "string", regex=r"M-\d{6}"),
     F("borrowed", "date"),
     F("due", "date"),
     F("returned", "date", required=False),
     F("renewals", "int", min=0, max=3)],
    [C("ISBN_CHECKSUM", "`isbn` must have a valid ISBN-13 check digit: weighting the 13 digits 1,3,1,3,... from "
       "the left, the weighted sum must be divisible by 10.", ["isbn"], _isbn_ok),
     C("DUE_BEFORE_BORROWED", "`due` must be after `borrowed` (not the same day).", ["borrowed", "due"],
       lambda r: date(r["due"]) > date(r["borrowed"])),
     C("LOAN_TOO_LONG", "`due` may be at most 21 days after `borrowed`, plus 14 days per renewal.",
       ["borrowed", "due", "renewals"],
       lambda r: (date(r["due"]) - date(r["borrowed"])).days <= 21 + 14 * r["renewals"]),
     C("RETURNED_BEFORE_BORROWED", "`returned` must not be earlier than `borrowed`.", ["borrowed", "returned"],
       lambda r: date(r["returned"]) >= date(r["borrowed"]))],
    bases=[{"loan_id": 5001, "isbn": "9780306406157", "member_id": "M-000123", "borrowed": "2024-03-01",
            "due": "2024-03-22", "renewals": 0},
           {"loan_id": 1, "isbn": "9781861972712", "member_id": "M-999999", "borrowed": "2024-01-31",
            "due": "2024-04-03", "returned": "2024-01-31", "renewals": 3},
           {"loan_id": 77, "isbn": "9790000000001", "member_id": "M-000001", "borrowed": "2024-02-28",
            "due": "2024-03-01", "returned": "2024-03-10", "renewals": 1}],
    samples=[S(0, {"loan_id": 0}, "LOAN_ID_RANGE"),
             S(0, {"isbn": "9780306406158"}, "ISBN_CHECKSUM"),
             S(0, {"isbn": "0306406152"}, "ISBN_FORMAT"),
             S(0, {"isbn": "978-0306406157"}, "ISBN_FORMAT"),
             S(0, {"isbn": 9780306406157}, "ISBN_TYPE"),
             S(0, {"member_id": "M-12345"}, "MEMBER_ID_FORMAT"),
             S(0, {"due": "2024-03-01"}, "DUE_BEFORE_BORROWED"),
             S(0, {"due": "2024-03-23"}, "LOAN_TOO_LONG"),
             S(0, {"due": "2024-03-23", "renewals": 1}),
             S(0, {"renewals": 4}, "RENEWALS_RANGE"),
             S(0, {"renewals": 4, "due": "2024-06-01"}, "RENEWALS_RANGE"),
             S(0, {"renewals": None}, "RENEWALS_MISSING"),
             S(0, {"returned": "2024-02-29"}, "RETURNED_BEFORE_BORROWED"),
             S(0, {"returned": "2024-03-01"}),
             S(0, {"returned": "2024-02-30"}, "RETURNED_FORMAT"),
             S(0, {"borrowed": "2024-3-1"}, "BORROWED_FORMAT"),
             S(1, {"due": "2024-04-04"}, "LOAN_TOO_LONG"),
             S(1, {"renewals": 2}, "LOAN_TOO_LONG"),
             S(1, {"isbn": "9781861972713"}, "ISBN_CHECKSUM"),
             S(2, {"due": "2024-02-28"}, "DUE_BEFORE_BORROWED"),
             S(2, {"isbn": "9800000000001"}, "ISBN_FORMAT"),
             S(2, {"borrowed": DEL, "returned": "1999-01-01"}, "BORROWED_MISSING"),
             S(2, {"loan_id": True}, "LOAN_ID_TYPE")]))


add(Spec(
    "course_grades", "Course grade rows", "Records are rows of a course-grade import for a student registry.",
    [F("student_id", "string", regex=r"\d{8}"),
     F("course", "string", regex=r"[A-Z]{4}\d{3}"),
     F("term", "string", regex=r"(SP|SU|FA)\d{4}"),
     F("grade", "string", enum=["A", "B", "C", "D", "F", "P", "NP", "I"]),
     F("credits", "int", min=1, max=6),
     F("pass_fail", "bool"),
     F("incomplete_deadline", "date", required=False)],
    [C("PF_GRADE_MISMATCH", "When `pass_fail` is true, `grade` must be P, NP or I; when false, it must be A, B, C, "
       "D, F or I.", ["pass_fail", "grade"],
       lambda r: r["grade"] == "I" or ((r["grade"] in ("P", "NP")) == r["pass_fail"])),
     C("INCOMPLETE_DEADLINE", "`incomplete_deadline` must be present when `grade` is I, and absent otherwise.",
       ["grade"], lambda r: (r["grade"] == "I") == (r.get("incomplete_deadline") is not None),
       optional=["incomplete_deadline"]),
     C("TERM_YEAR", "The year in `term` (its last 4 digits) must be between 2000 and 2030.", ["term"],
       lambda r: 2000 <= int(r["term"][2:]) <= 2030)],
    bases=[{"student_id": "20231234", "course": "MATH101", "term": "FA2023", "grade": "B", "credits": 4,
            "pass_fail": False},
           {"student_id": "00000001", "course": "ARTS210", "term": "SU2030", "grade": "P", "credits": 1,
            "pass_fail": True},
           {"student_id": "99999999", "course": "CHEM330", "term": "SP2000", "grade": "I", "credits": 6,
            "pass_fail": False, "incomplete_deadline": "2000-09-01"}],
    samples=[S(0, {"student_id": "2023123"}, "STUDENT_ID_FORMAT"),
             S(0, {"student_id": 20231234}, "STUDENT_ID_TYPE"),
             S(0, {"course": "MAT101"}, "COURSE_FORMAT"),
             S(0, {"course": "math101"}, "COURSE_FORMAT"),
             S(0, {"term": "WI2023"}, "TERM_FORMAT"),
             S(0, {"term": "FA1999"}, "TERM_YEAR"),
             S(0, {"term": "FA2031"}, "TERM_YEAR"),
             S(0, {"grade": "A+"}, "GRADE_ENUM"),
             S(0, {"grade": "P"}, "PF_GRADE_MISMATCH"),
             S(0, {"grade": "b"}, "GRADE_ENUM"),
             S(0, {"credits": 0}, "CREDITS_RANGE"),
             S(0, {"credits": 7}, "CREDITS_RANGE"),
             S(0, {"credits": "4"}, "CREDITS_TYPE"),
             S(0, {"pass_fail": "false"}, "PASS_FAIL_TYPE"),
             S(0, {"pass_fail": True}, "PF_GRADE_MISMATCH"),
             S(0, {"incomplete_deadline": "2024-01-15"}, "INCOMPLETE_DEADLINE"),
             S(0, {"incomplete_deadline": None}),
             S(1, {"grade": "NP"}),
             S(1, {"grade": "F"}, "PF_GRADE_MISMATCH"),
             S(1, {"pass_fail": None, "grade": "A"}, "PASS_FAIL_MISSING"),
             S(2, {"incomplete_deadline": DEL}, "INCOMPLETE_DEADLINE"),
             S(2, {"pass_fail": True}),
             S(2, {"incomplete_deadline": "2000-02-30"}, "INCOMPLETE_DEADLINE_FORMAT"),
             S(2, {"grade": "Z"}, "GRADE_ENUM")]))
