"""Import an Apollo CSV export into the contact list, with a column check first.

  python scripts/outreach/contacts_db.py import-apollo apollo.csv --check            # only look: which columns were found, what would be skipped
  python scripts/outreach/contacts_db.py import-apollo apollo.csv --basis "..."      # import

Why strict: Apollo's emails are only partly clean (independent tests: roughly 65 to 76%), and a bounce hurts a new domain. So by default only
rows whose "Email Status" is Verified are imported, catch-all addresses are skipped, and a file with no email column, or no status column, is
refused rather than guessed at. Headers are matched loosely (case, spaces and punctuation ignored) because Apollo's export columns vary by
plan and version; --check prints exactly how each of ours was matched so nothing is silent.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

ALIASES = {
    "email": ["email", "emailaddress", "workemail"],
    "first": ["firstname"],
    "last": ["lastname"],
    "name": ["name", "fullname"],
    "company": ["company", "companyname", "companynameforemails", "organization", "organizationname"],
    "country": ["country", "personcountry", "companycountry"],
    "status": ["emailstatus"],
    "catchall": ["primaryemailcatchallstatus", "catchallstatus", "emailcatchall"],
    "title": ["title", "jobtitle"],
}
CATCH_ALL = ("catchall", "catch-all", "catch all", "true", "yes")


def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def map_columns(headers: list[str]) -> dict[str, str | None]:
    """ours -> the file's header (the first alias present), or None."""
    by_norm = {norm(h): h for h in headers}
    return {ours: next((by_norm[a] for a in names if a in by_norm), None) for ours, names in ALIASES.items()}


def import_apollo(store, path: Path, *, allow_status=("verified",), allow_catch_all=False, basis="", check_only=False, out=print) -> dict:
    """-> {"ok": bool, "columns": {...}, "added": n, "skipped": {reason: n}}. Never guesses: a missing email column is an error; a missing status
    column is an error unless allow_status is empty."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    cols = map_columns(headers)
    result = {"ok": True, "columns": cols, "added": 0, "would_add": 0, "skipped": Counter()}
    for ours in ("email", "first", "last", "company", "country", "status", "catchall"):
        out(f"  {ours:<8} <- {cols[ours] or '(not in the file)'}")
    if not cols["email"]:
        out("refused: no email column. The file's headers are: " + ", ".join(headers))
        result["ok"] = False
        return result
    if allow_status and not cols["status"]:
        out("refused: no Email Status column, so nothing says which addresses are verified. Export it from Apollo, or pass --allow-status '' to import without it.")
        result["ok"] = False
        return result
    allowed = {s.strip().lower() for s in allow_status if s.strip()}
    for r in rows:
        email = (r.get(cols["email"]) or "").strip().lower()
        if not email:
            result["skipped"]["no email in the row"] += 1
            continue
        if allowed and (r.get(cols["status"]) or "").strip().lower() not in allowed:
            result["skipped"][f"email status is not one of: {', '.join(sorted(allowed))}"] += 1
            continue
        if not allow_catch_all and cols["catchall"] and (r.get(cols["catchall"]) or "").strip().lower() in CATCH_ALL:
            result["skipped"]["catch-all domain"] += 1
            continue
        name = " ".join(x for x in ((r.get(cols["first"]) or "").strip() if cols["first"] else "", (r.get(cols["last"]) or "").strip() if cols["last"] else "") if x)
        name = name or ((r.get(cols["name"]) or "").strip() if cols["name"] else "")
        company = (r.get(cols["company"]) or "").strip() if cols["company"] else ""
        country = (r.get(cols["country"]) or "").strip() if cols["country"] else ""
        if check_only:
            if store.status(email) is not None:
                result["skipped"]["already in the list (not changed)"] += 1
            else:
                result["would_add"] += 1
            continue
        if store.add(email, name=name, company=company, country=country, source="apollo-csv", basis=basis):
            result["added"] += 1
        else:
            result["skipped"]["invalid address, or already in the list (not changed)"] += 1
    result["skipped"] = dict(result["skipped"])
    out(f"{'would add' if check_only else 'added'}: {result['would_add'] if check_only else result['added']}; skipped: {result['skipped'] or 'none'}")
    return result
