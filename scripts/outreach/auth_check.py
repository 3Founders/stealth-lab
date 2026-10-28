"""Check the sending domain's mail DNS before a real send: MX, one SPF record, DKIM (if you give the selector), DMARC.

  python scripts/outreach/send_mail.py auth-check           # domain = the part after @ in SMTP_FROM; DKIM selector = OUTREACH_DKIM_SELECTOR

This reads public DNS records only. It cannot see whether the provider actually signs your mail with DKIM; send a test to mail-tester.com
for that. Needs the dnspython package (pip install dnspython); without it the check says so and a real send is not blocked by it.
"""
from __future__ import annotations


def dns_lookup(name: str, kind: str) -> list[str]:
    import dns.resolver  # noqa: PLC0415 -- optional dependency
    try:
        return [b"".join(r.strings).decode() if kind == "TXT" else r.to_text() for r in dns.resolver.resolve(name, kind)]
    except Exception:  # noqa: BLE001 -- no such record (NXDOMAIN, no answer, timeout): reported as missing
        return []


def check_domain(domain: str, dkim_selector: str = "", lookup=dns_lookup) -> dict:
    """-> {"ok": bool, "problems": [...], "notes": [...]}; ok means nothing that blocks a send was found."""
    problems, notes = [], []
    if not lookup(domain, "MX"):
        problems.append(f"{domain} has no MX record (replies and bounces would have nowhere to go)")
    spf = [t for t in lookup(domain, "TXT") if t.lower().startswith("v=spf1")]
    if not spf:
        problems.append("no SPF record (a TXT on the domain that starts v=spf1)")
    elif len(spf) > 1:
        problems.append(f"{len(spf)} SPF records; there must be exactly one")
    else:
        notes.append("SPF: " + spf[0])
    dmarc = [t for t in lookup("_dmarc." + domain, "TXT") if t.lower().startswith("v=dmarc1")]
    if not dmarc:
        problems.append("no DMARC record (a TXT on _dmarc that starts v=DMARC1)")
    else:
        notes.append("DMARC: " + dmarc[0])
    if dkim_selector:
        name = f"{dkim_selector}._domainkey.{domain}"
        if not (lookup(name, "TXT") or lookup(name, "CNAME")):
            problems.append(f"no DKIM record at {name}")
        else:
            notes.append(f"DKIM: a record exists at {name}")
    else:
        notes.append("DKIM not checked: set OUTREACH_DKIM_SELECTOR to the selector GoDaddy shows (the name before ._domainkey)")
    return {"ok": not problems, "problems": problems, "notes": notes}
