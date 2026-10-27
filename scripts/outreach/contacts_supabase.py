"""The contact list on Supabase (Postgres through its REST interface), with the same methods as contacts_db.Contacts.

Why it exists: the do-not-contact list must be ONE list that every place that sends reads. The sender therefore treats this store as
the source of truth when OUTREACH_STORE=supabase, and REFUSES to send when it cannot read it (see send_mail.send_drafts): a stale
or missing list could mean mailing someone who unsubscribed.

Settings (backend/.env, never printed): OUTREACH_STORE=supabase, SUPABASE_URL, SUPABASE_SERVICE_KEY (the service-role key; the tables
have row-level security on and no policies, so only this key can read or write them). Schema: scripts/outreach/supabase_schema.sql.
No third-party package: plain urllib against the PostgREST endpoint.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from contacts_db import EMAIL, FINAL, ORDER, Contacts

NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


class StoreUnavailable(RuntimeError):
    """The list could not be read or written; the sender must not send."""


def urllib_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 15.0):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- the URL is the user's own Supabase project
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class SupabaseContacts:
    def __init__(self, url: str, service_key: str, http=urllib_http):
        if not url.startswith("https://"):
            raise StoreUnavailable("SUPABASE_URL must be an https address")
        self.base, self.key, self.http = url.rstrip("/") + "/rest/v1/", service_key, http

    def _call(self, method: str, table: str, query: dict | None = None, body=None, prefer: str | None = None):
        url = self.base + table + ("?" + urllib.parse.urlencode(query or {}, safe="(),.*") if query else "")
        headers = {"apikey": self.key, "Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        try:
            status, raw = self.http(method, url, headers, json.dumps(body).encode() if body is not None else None)
        except Exception as e:  # noqa: BLE001 -- network failure: say what kind, never include the key
            raise StoreUnavailable(f"could not reach the contact list ({type(e).__name__})") from None
        if status >= 300:
            raise StoreUnavailable(f"the contact list answered {status}: {raw[:160].decode('utf-8', 'replace').replace(self.key, '[hidden]')}")
        return json.loads(raw) if raw else []

    def event(self, email: str, kind: str, detail: str = "") -> None:
        self._event(email, kind, detail)

    def _event(self, email: str, kind: str, detail: str = "") -> None:
        self._call("POST", "outreach_events", body={"at": NOW(), "email": email, "kind": kind, "detail": detail[:300]})

    def add(self, email: str, name="", company="", country="", source="", basis="") -> bool:
        email = email.strip().lower()
        if not EMAIL.match(email):
            return False
        made = self._call("POST", "outreach_contacts", {"on_conflict": "email"},
                          {"email": email, "name": name, "company": company, "country": country, "source": source, "basis": basis, "created_at": NOW()},
                          prefer="resolution=ignore-duplicates,return=representation")
        if made:
            self._event(email, "added", source)
        return bool(made)

    def status(self, email: str) -> str | None:
        rows = self._call("GET", "outreach_contacts", {"select": "status", "email": "eq." + email.strip().lower()})
        return rows[0]["status"] if rows else None

    def is_blocked(self, email: str) -> bool:
        return self.status(email) in FINAL

    def blocked(self) -> set[str]:
        rows = self._call("GET", "outreach_contacts", {"select": "email", "status": "in.(" + ",".join(FINAL) + ")"})
        return {r["email"] for r in rows}

    def contacted(self) -> set[str]:
        rows = self._call("GET", "outreach_contacts", {"select": "email", "status": "in.(sent,replied)"})
        return {r["email"] for r in rows}

    def finalize(self, email: str, status: str, reason: str = "") -> None:
        assert status in FINAL
        email = email.strip().lower()
        self.add(email, source="finalized")
        cur = self.status(email)
        if cur in FINAL and cur != status and status != "unsubscribed":
            return
        self._call("PATCH", "outreach_contacts", {"email": "eq." + email}, {"status": status, "final_at": NOW(), "final_reason": reason[:200]})
        self._event(email, status, reason)

    def advance(self, email: str, status: str) -> None:
        email = email.strip().lower()
        cur = self.status(email)
        if cur is None or cur in FINAL or ORDER.index(status) <= ORDER.index(cur):
            return
        patch = {"status": status}
        if status == "sent":
            patch["last_contacted_at"] = NOW()
        self._call("PATCH", "outreach_contacts", {"email": "eq." + email}, patch)
        self._event(email, status)

    def rows(self, status: str | None = None) -> list[dict]:
        q = {"select": "*", "order": "created_at"}
        if status:
            q["status"] = "eq." + status
        return self._call("GET", "outreach_contacts", q)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._call("GET", "outreach_contacts", {"select": "status"}):
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out

    import_csv = Contacts.import_csv
