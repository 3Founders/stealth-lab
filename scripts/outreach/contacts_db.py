"""The contact list and the do-not-contact list, in one local SQLite file (outreach/contacts.db): free, no server, no account.

Why SQLite and not a hosted database: at tens or hundreds of contacts the file is enough, it cannot leak through a misconfigured
cloud project, and the sender reads it before every send. A hosted free tier (Supabase, Neon) is only needed for a one-click
unsubscribe LINK (a web page has to write to it) or for several people sharing the list; see docs/outreach_plan.md section 11.

A contact's status only moves forward: new -> approved -> sent -> replied, and any status can move to unsubscribed, bounced or
do_not_contact. Those last three are final: nothing in this tool moves a contact out of them, and the sender refuses to write to them.

  python scripts/outreach/contacts_db.py import contacts.csv     # columns: email, name, company, country, source, basis
  python scripts/outreach/contacts_db.py list [--status sent]
  python scripts/outreach/contacts_db.py unsubscribe someone@x.com --reason "asked by phone"
  python scripts/outreach/contacts_db.py sync-inbox --days 14    # reads your mailbox over IMAP: unsubscribe replies and bounces
  python scripts/outreach/contacts_db.py summary
"""
from __future__ import annotations

import argparse
import csv
import email as email_lib
import imaplib
import re
import sqlite3
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_FILE = ROOT / "outreach" / "contacts.db"
FINAL = ("unsubscribed", "bounced", "do_not_contact")
ORDER = ("new", "approved", "sent", "replied")
EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
# a reply that asks to stop: checked only against the text a person wrote, with quoted lines and our own footer removed
STOP = re.compile(r"\b(unsubscribe|remove me|take me off|stop (?:emailing|contacting|sending)|do not (?:contact|email)|don'?t (?:contact|email)|opt[- ]?out)\b", re.I)
NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


class Contacts:
    def __init__(self, path: Path | str = DB_FILE):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS contacts(
              email TEXT PRIMARY KEY, name TEXT, company TEXT, country TEXT, source TEXT, basis TEXT,
              status TEXT NOT NULL DEFAULT 'new', created_at TEXT, last_contacted_at TEXT, final_at TEXT, final_reason TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, email TEXT, kind TEXT, detail TEXT);""")

    def event(self, email: str, kind: str, detail: str = "") -> None:
        self._event(email, kind, detail)
        self.db.commit()

    def _event(self, email: str, kind: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO events(at,email,kind,detail) VALUES(?,?,?,?)", (NOW(), email, kind, detail[:300]))

    def add(self, email: str, name="", company="", country="", source="", basis="") -> bool:
        """Add a contact; False if the address is invalid or already known (a known address is never reset)."""
        email = email.strip().lower()
        if not EMAIL.match(email):
            return False
        cur = self.db.execute("INSERT OR IGNORE INTO contacts(email,name,company,country,source,basis,created_at) VALUES(?,?,?,?,?,?,?)",
                              (email, name, company, country, source, basis, NOW()))
        if cur.rowcount:
            self._event(email, "added", source)
        self.db.commit()
        return bool(cur.rowcount)

    def status(self, email: str) -> str | None:
        r = self.db.execute("SELECT status FROM contacts WHERE email=?", (email.strip().lower(),)).fetchone()
        return r["status"] if r else None

    def country(self, email: str) -> str:
        r = self.db.execute("SELECT country FROM contacts WHERE email=?", (email.strip().lower(),)).fetchone()
        return (r["country"] if r else "") or ""

    def is_blocked(self, email: str) -> bool:
        return self.status(email) in FINAL

    def blocked(self) -> set[str]:
        return {r["email"] for r in self.db.execute("SELECT email FROM contacts WHERE status IN (?,?,?)", FINAL)}

    def contacted(self) -> set[str]:
        return {r["email"] for r in self.db.execute("SELECT email FROM contacts WHERE status IN ('sent','replied')")}

    def finalize(self, email: str, status: str, reason: str = "") -> None:
        """unsubscribed / bounced / do_not_contact. Works even for an address we never added (it is then recorded, so it can never be added back)."""
        assert status in FINAL
        email = email.strip().lower()
        self.add(email, source="finalized")
        cur = self.status(email)
        if cur in FINAL and cur != status and status != "unsubscribed":
            return                                    # an unsubscribe outranks a bounce; otherwise keep the first final status
        self.db.execute("UPDATE contacts SET status=?, final_at=?, final_reason=? WHERE email=?", (status, NOW(), reason[:200], email))
        self._event(email, status, reason)
        self.db.commit()

    def advance(self, email: str, status: str) -> None:
        email = email.strip().lower()
        cur = self.status(email)
        if cur is None or cur in FINAL or ORDER.index(status) <= ORDER.index(cur):
            return                                    # never moves back, never out of a final status
        sent = NOW() if status == "sent" else None
        self.db.execute("UPDATE contacts SET status=?, last_contacted_at=COALESCE(?,last_contacted_at) WHERE email=?", (status, sent, email))
        self._event(email, status)
        self.db.commit()

    def rows(self, status: str | None = None) -> list[sqlite3.Row]:
        q, a = ("SELECT * FROM contacts WHERE status=? ORDER BY created_at", (status,)) if status else ("SELECT * FROM contacts ORDER BY created_at", ())
        return list(self.db.execute(q, a))

    def summary(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM contacts GROUP BY status")}

    def import_csv(self, path: Path) -> dict[str, int]:
        out = {"added": 0, "skipped": 0}
        with Path(path).open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                ok = self.add(r.get("email", ""), r.get("name", ""), r.get("company", ""), r.get("country", ""), r.get("source", ""), r.get("basis", ""))
                out["added" if ok else "skipped"] += 1
        return out


def open_store(settings: dict[str, str]):
    """The contact list the settings ask for. OUTREACH_STORE=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY and never falls back to
    the local file: a second, older list could mean writing to someone who already unsubscribed."""
    if (settings.get("OUTREACH_STORE") or "local").lower() == "supabase":
        if not (settings.get("SUPABASE_URL") and settings.get("SUPABASE_SERVICE_KEY")):
            raise SystemExit("OUTREACH_STORE=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY in backend/.env")
        from contacts_supabase import SupabaseContacts
        return SupabaseContacts(settings["SUPABASE_URL"], settings["SUPABASE_SERVICE_KEY"])
    return Contacts()


def written_text(msg) -> str:
    """What the person typed: plain-text parts, minus quoted lines (> ...) and everything from our own footer or an 'On ... wrote:' line down."""
    parts = []
    for p in msg.walk() if msg.is_multipart() else [msg]:
        if p.get_content_type() == "text/plain":
            parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace"))
    keep = []
    for line in "\n".join(parts).splitlines():
        if re.match(r"^\s*On .+wrote:\s*$", line) or line.strip() == "--":
            break
        if not line.lstrip().startswith(">"):
            keep.append(line)
    return "\n".join(keep)


AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto[- ]?reply|autoreply|out of (the )?office|ooo)\b|\bout of (the )?office\b", re.I)


def is_auto_reply(msg) -> bool:
    return (msg.get("Auto-Submitted", "no").strip().lower() != "no" or bool(msg.get("X-Autoreply")) or bool(msg.get("X-Autorespond"))
            or bool(AUTO_SUBJECT.search(msg.get("Subject", ""))))


def sync_inbox(contacts: Contacts, imap, *, days: int = 14, out=print) -> dict[str, int]:
    """Read recent mail: a known contact whose reply asks to stop becomes unsubscribed; a delivery-failure notice marks the failed
    address bounced. Anything else from a known contact is marked replied. `imap` is an imaplib.IMAP4 connection (a fake in tests)."""
    imap.select("INBOX", readonly=True)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    _, data = imap.search(None, "SINCE", since)
    counts = {"unsubscribed": 0, "bounced": 0, "replied": 0, "auto_replies": 0}
    for num in (data[0] or b"").split():
        _, parts = imap.fetch(num, "(RFC822)")
        msg = email_lib.message_from_bytes(parts[0][1])
        sender = email_lib.utils.parseaddr(msg.get("From", ""))[1].lower()
        if "mailer-daemon" in sender or "postmaster" in sender:
            blob = msg.as_string()
            for failed in set(re.findall(r"Final-Recipient:\s*rfc822;\s*([^\s;]+)", blob, re.I)):
                if contacts.status(failed) not in (None, *FINAL):
                    contacts.finalize(failed, "bounced", "delivery failure notice")
                    counts["bounced"] += 1
            continue
        if contacts.status(sender) is None:
            continue
        if is_auto_reply(msg):                      # an out-of-office is sent by the recipient's INBOX, never from junk: a free placement signal
            contacts.event(sender, "auto_reply", msg.get("Subject", ""))
            counts["auto_replies"] += 1
            continue
        if STOP.search(written_text(msg)):
            if contacts.status(sender) != "unsubscribed":
                contacts.finalize(sender, "unsubscribed", "asked to stop by email")
                counts["unsubscribed"] += 1
        elif contacts.status(sender) == "sent":
            contacts.advance(sender, "replied")
            counts["replied"] += 1
    out(f"inbox since {since}: {counts}")
    return counts


def main(argv=None, out=print, environ=None) -> int:
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import send_mail as sm
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    sub.add_parser("import").add_argument("file")
    ia = sub.add_parser("import-apollo", help="import an Apollo CSV export (verified emails only by default)")
    ia.add_argument("file")
    ia.add_argument("--check", action="store_true", help="only report the column match and what would be skipped; write nothing")
    ia.add_argument("--allow-status", default="verified", help="comma list of Email Status values to accept ('' = do not filter)")
    ia.add_argument("--allow-catch-all", action="store_true")
    ia.add_argument("--basis", default="", help="why these people are being contacted (kept per contact)")
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    un = sub.add_parser("unsubscribe")
    un.add_argument("address")
    un.add_argument("--reason", default="asked to be removed")
    sy = sub.add_parser("sync-inbox")
    sy.add_argument("--days", type=int, default=14)
    a = p.parse_args(argv)
    c = open_store(sm.load_settings(os.environ if environ is None else environ, sm.ENV_FILE))
    if a.cmd == "summary":
        out(str(c.summary() or "(empty)"))
    elif a.cmd == "import":
        out(str(c.import_csv(Path(a.file))))
    elif a.cmd == "import-apollo":
        from apollo_csv import import_apollo
        r = import_apollo(c, Path(a.file), allow_status=tuple(a.allow_status.split(",")), allow_catch_all=a.allow_catch_all, basis=a.basis,
                          check_only=a.check, out=out)
        return 0 if r["ok"] else 2
    elif a.cmd == "list":
        for r in c.rows(a.status):
            out(f"{r['status']:<15} {r['email']:<35} {r['company'] or ''}")
    elif a.cmd == "unsubscribe":
        c.finalize(a.address, "unsubscribed", a.reason)
        out(f"{a.address}: unsubscribed")
    elif a.cmd == "sync-inbox":
        s = sm.load_settings(os.environ if environ is None else environ)
        host = s.get("IMAP_HOST") or (s.get("SMTP_HOST", "").replace("smtp.", "imap.") if s.get("SMTP_HOST") else "")
        if not host or not s.get("SMTP_USER") or not s.get("SMTP_PASSWORD"):
            out("needs SMTP_USER, SMTP_PASSWORD and an IMAP host (IMAP_HOST, or SMTP_HOST starting with smtp.) in backend/.env")
            return 2
        imap = imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context())
        imap.login(s["SMTP_USER"], s["SMTP_PASSWORD"])
        try:
            sync_inbox(c, imap, days=a.days, out=out)
        finally:
            imap.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
