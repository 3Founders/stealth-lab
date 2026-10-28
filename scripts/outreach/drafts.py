"""Make drafts from the contact list, a facts file and a versioned template; read them; approve them. No model writes anything here.

Why a template and not a model: each email is built from one fact a person picked from a public page, and the wording is the same file every
time, so what was sent is always known (template_version in the log). A model writing per person adds a source of wrong personal details
(a receiver-side study found most cold emails with wrong facts are filtered) and a question about Apollo's no-AI-training clause.

  python scripts/outreach/drafts.py init                          # writes outreach/template.txt and outreach/facts.csv (examples) if missing
  python scripts/outreach/drafts.py make                          # contacts with a facts row -> outreach/drafts.jsonl (approved: false)
  python scripts/outreach/drafts.py show                          # read them
  python scripts/outreach/drafts.py approve --by "Your Name" --emails a@x.com,b@y.com      # or --all
facts.csv columns: email, evidence_url, evidence_fact (the one sentence from that page), and optionally subject.
Approving marks a draft sendable; it does not send. Sending is send_mail.py send ... --really.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send_mail as sm  # noqa: E402

OUT_DIR = sm.OUT_DIR
TEMPLATE_FILE = OUT_DIR / "template.txt"
FACTS_FILE = OUT_DIR / "facts.csv"
DRAFTS_FILE = OUT_DIR / "drafts.jsonl"

DEFAULT_TEMPLATE = """version: v1
subject: Quick question about {company}
---
Hi {first_name},

I came across this: {fact} ({evidence_url})

I'm building a tool that sits beside coding agents such as Claude Code and Cursor. It tries routine coding tasks on cheaper models first and only accepts a result if checks pass; otherwise the stronger model does the work. I am trying to learn whether coding-agent cost or quality is a real problem at {company}. I am not selling anything yet.

If it is, could I ask you two questions by email, or take 15 minutes on a call?

{sender_first}
"""


def parse_template(text: str) -> tuple[str, str, str]:
    """-> (version, subject, body) from a file: 'version: x' and 'subject: y' lines, a line with ---, then the body."""
    head, _, body = text.partition("\n---\n")
    meta = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    version, subject = meta.get("version", "").strip(), meta.get("subject", "").strip()
    if not version or not subject or not body.strip():
        raise ValueError("template needs 'version:' and 'subject:' lines, a line with ---, and a body")
    return version, subject, body.strip() + "\n"


def load_facts(path: Path = FACTS_FILE) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["email"].strip().lower(): r for r in csv.DictReader(f) if r.get("email")}


def make_drafts(store, template: str, facts: dict[str, dict], sender_name: str, existing: set[str], out=print) -> list[dict]:
    version, subject_t, body_t = parse_template(template)
    made = []
    for r in store.rows("new"):
        email = r["email"]
        f = facts.get(email)
        if email in existing:
            continue
        if not f or not (f.get("evidence_url") or "").strip() or not (f.get("evidence_fact") or "").strip():
            out(f"skip {email}: no facts row with evidence_url and evidence_fact")
            continue
        first = (r["name"] or "").split(" ")[0].strip()
        if not first:
            out(f"skip {email}: no name on the contact")
            continue
        slots = {"first_name": first, "company": r["company"] or "your company", "fact": f["evidence_fact"].strip(),
                 "evidence_url": f["evidence_url"].strip(), "sender_first": sender_name.split(" ")[0]}
        d = {"to": email, "subject": (f.get("subject") or "").strip() or subject_t.format(**slots), "body": body_t.format(**slots),
             "approved": False, "approved_by": "", "template_version": version, "country": r["country"] or "",
             "evidence_url": slots["evidence_url"], "evidence_fact": slots["fact"]}
        problems = [w for w in sm.rule_check({**d, "approved_by": "x"}) if "approved_by" not in w]
        if problems:
            out(f"skip {email}: " + "; ".join(problems))
            continue
        made.append(d)
    return made


def read_drafts(path: Path = DRAFTS_FILE) -> list[dict]:
    return sm.load_drafts(path) if path.exists() else []


def write_drafts(drafts: list[dict], path: Path = DRAFTS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in drafts), encoding="utf-8")


def approve(drafts: list[dict], by: str, emails: set[str] | None, store=None) -> int:
    n = 0
    for d in drafts:
        if (emails is None or d["to"].strip().lower() in emails) and not d.get("approved"):
            d["approved"], d["approved_by"] = True, by.strip()
            n += 1
            if store:
                store.advance(d["to"], "approved")
    return n


def main(argv=None, out=print, environ=None) -> int:
    import os
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("make")
    sub.add_parser("show")
    ap = sub.add_parser("approve")
    ap.add_argument("--by", required=True)
    ap.add_argument("--emails", default="")
    ap.add_argument("--all", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "init":
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for path, text in ((TEMPLATE_FILE, DEFAULT_TEMPLATE), (FACTS_FILE, "email,evidence_url,evidence_fact,subject\n")):
            if path.exists():
                out(f"{path.name} already exists; left alone")
            else:
                path.write_text(text, encoding="utf-8")
                out(f"wrote {path}")
        return 0
    if a.cmd == "show":
        for i, d in enumerate(read_drafts(), 1):
            out(f"[{i}] {'APPROVED by ' + d['approved_by'] if d.get('approved') else 'not approved'}  to {d['to']}  ({d.get('country') or 'no country'})"
                f"\n    subject: {d['subject']}\n" + "\n".join("    " + l for l in d["body"].splitlines()) + "\n")
        return 0
    from contacts_db import open_store
    settings = sm.load_settings(os.environ if environ is None else environ, sm.ENV_FILE)
    store = open_store(settings)
    if a.cmd == "make":
        if not TEMPLATE_FILE.exists():
            out("no outreach/template.txt; run `drafts.py init` first")
            return 2
        existing = read_drafts()
        made = make_drafts(store, TEMPLATE_FILE.read_text(encoding="utf-8"), load_facts(), settings.get("SMTP_FROM_NAME") or "me",
                           {d["to"].lower() for d in existing}, out)
        write_drafts(existing + made)
        out(f"made {len(made)} draft(s); {len(existing) + len(made)} in {DRAFTS_FILE.name}. Read them with `drafts.py show`.")
        return 0
    if a.cmd == "approve":
        if not a.all and not a.emails:
            out("say which: --emails a@x.com,b@y.com, or --all")
            return 2
        drafts = read_drafts()
        n = approve(drafts, a.by, None if a.all else {e.strip().lower() for e in a.emails.split(",") if e.strip()}, store)
        write_drafts(drafts)
        out(f"approved {n} draft(s) as {a.by}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
