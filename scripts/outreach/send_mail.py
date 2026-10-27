"""Send approved outreach drafts over SMTP (Python's smtplib). Draft-only by default: nothing is sent without --really.

  python scripts/outreach/send_mail.py check                          # the settings it found (names only), no connection
  python scripts/outreach/send_mail.py test --to you@example.com      # ONE real message to your own address: proves login and delivery
  python scripts/outreach/send_mail.py send --file outreach/drafts.jsonl              # dry run: what would go, no connection
  python scripts/outreach/send_mail.py send --file outreach/drafts.jsonl --really     # sends the approved drafts
  python scripts/outreach/send_mail.py suppress add someone@example.com               # never write to this address again

Drafts file: one JSON object per line: {"to": "...", "subject": "...", "body": "...", "approved": true, "evidence_url": "https://..."}.
A draft goes out only if approved is true, the address is not suppressed, it has not been sent before (outreach/log.csv) and it passes
the rule check. Settings come from backend/.env (the file wins over the shell; the password is never printed or logged):
SMTP_HOST, SMTP_PORT (587), SMTP_SECURITY (starttls | ssl), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_FROM_NAME,
OUTREACH_POSTAL_ADDRESS, OUTREACH_UNSUBSCRIBE_MAILTO (optional; defaults to SMTP_FROM).

Why the limits: cold email law (CAN-SPAM and others) needs a real sender, a postal address and a working opt-out; see docs/outreach_plan.md.
This tool does not decide whether you may email a person in a given country; it only makes the mechanics safe. Not legal advice.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "backend" / ".env"
OUT_DIR = ROOT / "outreach"
LOG_FILE = OUT_DIR / "log.csv"
SUPPRESS_FILE = OUT_DIR / "suppression.txt"

REQUIRED = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "OUTREACH_POSTAL_ADDRESS")
SECRET = ("SMTP_PASSWORD", "SUPABASE_SERVICE_KEY")
MAX_PER_RUN = 20
DELAY_S = (20, 45)                                   # seconds between sends, random inside this range
MAX_BODY_CHARS = 1500
BOUNCE_PAUSE = 0.02                                  # above this share of hard bounces, no new send until the runbook has been followed
EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
# the copy rules (docs/outreach_plan.md section 4/5) that code can check
FORBIDDEN = (
    (re.compile(r"[$€£₹]\s?\d|\d\s?(?:usd|eur|inr|dollars|rupees)\b", re.I), "a price or amount"),
    (re.compile(r"\bsoc\s?2\b", re.I), "SOC 2 (we have no report)"),
    (re.compile(r"zero[- ]egress|no data leaves", re.I), "a zero-egress claim (our data path is not zero-egress)"),
    (re.compile(r"\d\s?%|\bpercent\b", re.I), "a percentage (any figure needs to be backed; take it out or write the draft without it)"),
)


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def load_settings(environ: dict[str, str], env_file: Path = ENV_FILE) -> dict[str, str]:
    """The SMTP settings; the file wins over the shell. Only names this tool knows are read."""
    names = REQUIRED + ("SMTP_PORT", "SMTP_SECURITY", "SMTP_FROM_NAME", "OUTREACH_UNSUBSCRIBE_MAILTO", "IMAP_HOST",
                        "OUTREACH_STORE", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")
    file_values = read_env_file(env_file)
    return {n: file_values.get(n) or environ.get(n, "") for n in names if file_values.get(n) or environ.get(n)}


def missing(settings: dict[str, str]) -> list[str]:
    return [n for n in REQUIRED if not settings.get(n)]


def scrub(text: str, settings: dict[str, str]) -> str:
    for n in SECRET:
        if settings.get(n):
            text = text.replace(settings[n], "[hidden]")
    return text


def rule_check(draft: dict) -> list[str]:
    """Why a draft may not be sent (empty = fine)."""
    why = []
    to = str(draft.get("to", "")).strip()
    if not EMAIL.match(to):
        why.append("the address is not valid")
    if not str(draft.get("subject", "")).strip():
        why.append("no subject")
    if re.match(r"^\s*(re|fwd?):", str(draft.get("subject", "")), re.I):
        why.append("the subject starts with Re:/Fwd: (a deceptive subject)")
    body = str(draft.get("body", ""))
    if not body.strip():
        why.append("no body")
    if len(body) > MAX_BODY_CHARS:
        why.append(f"the body is over {MAX_BODY_CHARS} characters")
    for pattern, label in FORBIDDEN:
        if pattern.search(body) or pattern.search(str(draft.get("subject", ""))):
            why.append(f"contains {label}")
    if not str(draft.get("evidence_url", "")).startswith(("http://", "https://")):
        why.append("no evidence_url (the public fact the email is built on)")
    if not str(draft.get("evidence_fact", "")).strip():
        why.append("no evidence_fact (the one sentence taken from that page; a wrong personal detail is worse than none, and a person checks it)")
    if not str(draft.get("approved_by", "")).strip():
        why.append("no approved_by (who read and approved it)")
    if not str(draft.get("template_version", "")).strip():
        why.append("no template_version (the record of which wording was used)")
    if len(re.findall(r"https?://", body)) > 1:
        why.append("more than one link in the body")
    if re.search(r"<\s*(img|a)\b|utm_[a-z]+=", body, re.I):
        why.append("HTML or tracking parameters in the body (plain text only)")
    return why


def footer(settings: dict[str, str]) -> str:
    unsub = settings.get("OUTREACH_UNSUBSCRIBE_MAILTO") or settings["SMTP_FROM"]
    name = settings.get("SMTP_FROM_NAME") or settings["SMTP_FROM"]
    return ("\n\n--\n"
            f"{name}, {settings['OUTREACH_POSTAL_ADDRESS']}\n"
            "This is a one-off message from me (not a mailing list). I found your work contact through public sources and a business-data provider.\n"
            f"If you would rather not hear from me, reply \"unsubscribe\" or write to {unsub} and I will not contact you again.\n")


def build_message(draft: dict, settings: dict[str, str]) -> EmailMessage:
    msg = EmailMessage()
    sender = settings["SMTP_FROM"]
    msg["From"] = f'{settings["SMTP_FROM_NAME"]} <{sender}>' if settings.get("SMTP_FROM_NAME") else sender
    msg["To"] = draft["to"].strip()
    msg["Subject"] = draft["subject"].strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    unsub = settings.get("OUTREACH_UNSUBSCRIBE_MAILTO") or sender
    msg["List-Unsubscribe"] = f"<mailto:{unsub}?subject=unsubscribe>"
    msg.set_content(draft["body"].rstrip() + footer(settings))
    return msg


def connect(settings: dict[str, str]):
    host, port = settings["SMTP_HOST"], int(settings.get("SMTP_PORT") or 587)
    if (settings.get("SMTP_SECURITY") or "starttls").lower() == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    server.login(settings["SMTP_USER"], settings["SMTP_PASSWORD"])
    return server


def suppressed(path: Path = SUPPRESS_FILE) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def already_sent(path: Path = LOG_FILE) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {r["to"].lower() for r in csv.DictReader(f) if r.get("status") == "sent"}


def log_row(row: dict, path: Path = LOG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["at", "to", "subject", "evidence_url", "approved_by", "template_version", "status", "detail"])
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in w.fieldnames})


def load_drafts(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


def bounce_counts(summary: dict[str, int]) -> tuple[int, int]:
    """(bounced, everyone we have mailed or tried to) from the contact list's status counts."""
    bounced = summary.get("bounced", 0)
    return bounced, bounced + summary.get("sent", 0) + summary.get("replied", 0)


def send_drafts(drafts: list[dict], settings: dict[str, str], *, really: bool, limit: int = MAX_PER_RUN, out=print,
                connect_fn=connect, sleep=time.sleep, log_path: Path = LOG_FILE, suppress_path: Path = SUPPRESS_FILE, contacts=None,
                show: bool = False, bounce_reviewed: bool = False) -> dict[str, int]:
    """Plan every draft, then (only with really=True) send the plan. Returns counts by outcome."""
    counts = {"to_send": 0, "sent": 0, "skipped": 0, "failed": 0}
    try:
        listed = (contacts.blocked(), contacts.contacted()) if contacts else (set(), set())
    except Exception as e:  # noqa: BLE001 -- fail closed: with no readable do-not-contact list nothing is sent
        out("cannot read the contact list: " + scrub(f"{type(e).__name__}: {e}", settings)[:200])
        if really:
            out("nothing was sent")
            counts["failed"] = len(drafts)
            return counts
        out("warning: this dry run could not check the do-not-contact list; a real send would be refused")
        listed = (set(), set())
    if really and contacts and not bounce_reviewed:
        try:
            bounced, total = bounce_counts(contacts.summary())
        except Exception as e:  # noqa: BLE001 -- fail closed, as for the list itself
            out("cannot read the bounce counts: " + scrub(f"{type(e).__name__}: {e}", settings)[:200] + "; nothing was sent")
            counts["failed"] = len(drafts)
            return counts
        if bounced and bounced / total > BOUNCE_PAUSE:
            out(f"paused: {bounced} of {total} addresses bounced ({bounced / total:.0%}, limit {BOUNCE_PAUSE:.0%}). Follow docs/outreach_blocklist_runbook.md, "
                "then run again with --bounce-reviewed. Nothing was sent")
            counts["failed"] = len(drafts)
            return counts
    skip, sent_before, plan = suppressed(suppress_path) | listed[0], already_sent(log_path) | listed[1], []
    for d in drafts:
        to = str(d.get("to", "")).strip().lower()
        reasons = []
        if not d.get("approved") is True:
            reasons.append("not approved")
        if to in skip:
            reasons.append("on the suppression list")
        if to in sent_before:
            reasons.append("already sent")
        reasons += rule_check(d)
        if reasons:
            counts["skipped"] += 1
            out(f"skip  {to or '?'}: " + "; ".join(reasons))
        elif len(plan) >= limit:
            counts["skipped"] += 1
            out(f"skip  {to}: over the per-run limit of {limit}")
        else:
            plan.append(d)
    counts["to_send"] = len(plan)
    for d in plan:
        out(f"{'send ' if really else 'would send'} {d['to']}: {d['subject']}")
        if show and not really:
            out("-" * 60 + chr(10) + build_message(d, settings).as_string() + "-" * 60)
    if not really or not plan:
        if not really and plan:
            out("dry run: nothing was sent (add --really to send)")
        return counts
    try:
        server = connect_fn(settings)
    except Exception as e:  # noqa: BLE001 -- login/connection failure: stop, say why, never print the password
        out("could not connect or log in: " + scrub(f"{type(e).__name__}: {e}", settings))
        counts["failed"] = len(plan)
        return counts
    try:
        for i, d in enumerate(plan):
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                server.send_message(build_message(d, settings))
                counts["sent"] += 1
                if contacts:
                    try:
                        contacts.add(d["to"], source="draft")
                        contacts.advance(d["to"], "sent")
                    except Exception as e:  # noqa: BLE001 -- the mail is gone; the local log below still stops a second send
                        out("warning: sent, but the contact list was not updated: " + scrub(f"{type(e).__name__}: {e}", settings)[:160])
                log_row({"at": at, "to": d["to"], "subject": d["subject"], "evidence_url": d.get("evidence_url", ""),
                         "approved_by": d.get("approved_by", ""), "template_version": d.get("template_version", ""), "status": "sent"}, log_path)
            except smtplib.SMTPRecipientsRefused as e:
                counts["failed"] += 1
                if contacts:
                    try:
                        contacts.finalize(d["to"], "bounced", "recipient refused by the server")
                    except Exception as e:  # noqa: BLE001
                        out("warning: refused, but the contact list was not updated: " + scrub(f"{type(e).__name__}: {e}", settings)[:160])
                log_row({"at": at, "to": d["to"], "subject": d["subject"], "status": "refused", "detail": scrub(str(e), settings)[:200]}, log_path)
                out(f"refused {d['to']}")
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as e:
                counts["failed"] += len(plan) - i
                out("stopping: " + scrub(f"{type(e).__name__}: {e}", settings)[:200])
                break
            if i < len(plan) - 1:
                sleep(random.uniform(*DELAY_S))
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    return counts


def main(argv=None, environ=None, out=print) -> int:
    import os
    environ = os.environ if environ is None else environ
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    t = sub.add_parser("test")
    t.add_argument("--to", required=True)
    s = sub.add_parser("send")
    s.add_argument("--file", required=True)
    s.add_argument("--really", action="store_true", help="actually send (the default is a dry run)")
    s.add_argument("--limit", type=int, default=MAX_PER_RUN)
    s.add_argument("--bounce-reviewed", action="store_true", help="I have followed the runbook after a bounce pause")
    s.add_argument("--show", action="store_true", help="in a dry run, print each full message (headers, body, footer)")
    sp = sub.add_parser("suppress")
    sp.add_argument("action", choices=["add", "list"])
    sp.add_argument("address", nargs="?")
    a = p.parse_args(argv)

    if a.cmd == "suppress":
        if a.action == "list":
            out("\n".join(sorted(suppressed())) or "(empty)")
            return 0
        if not a.address or not EMAIL.match(a.address):
            out("give a valid address")
            return 2
        SUPPRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SUPPRESS_FILE.open("a", encoding="utf-8") as f:
            f.write(a.address.strip().lower() + "\n")
        out(f"added {a.address}")
        return 0

    settings = load_settings(environ, ENV_FILE)
    if a.cmd == "check":
        out("found: " + (", ".join(sorted(settings)) or "nothing") + "  (names only)")
        miss = missing(settings)
        out("missing: " + ", ".join(miss) if miss else "all required settings are present; no connection was made")
        return 1 if miss else 0
    miss = missing(settings)
    if miss and (a.cmd == "test" or getattr(a, "really", False)):
        out("missing settings: " + ", ".join(miss) + " (put them in backend/.env)")
        return 2
    if a.cmd == "test":
        draft = {"to": a.to, "subject": "Test message from the outreach sender", "body": "This is a test. If you can read this, login and delivery work.",
                 "approved": True, "evidence_url": "https://example.com", "evidence_fact": "test", "approved_by": "me", "template_version": "test"}
        c = send_drafts([draft], settings, really=True, out=out, log_path=OUT_DIR / "test_log.csv")
        return 0 if c["sent"] == 1 else 1
    if miss:                                          # dry run without full settings: still show the plan, with a placeholder footer
        settings = {**{n: "x@example.com" if "FROM" in n else "x" for n in REQUIRED}, **settings}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from contacts_db import open_store
    c = send_drafts(load_drafts(Path(a.file)), settings, really=a.really, limit=a.limit, out=out, contacts=open_store(settings), show=a.show,
                    bounce_reviewed=a.bounce_reviewed)
    out(f"result: {c}")
    return 0 if c["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
