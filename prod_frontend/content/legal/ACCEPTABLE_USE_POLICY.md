# StealthLab Acceptable Use Policy

**STATUS: DRAFT — REQUIRES LEGAL REVIEW. NOT YET PUBLISHED OR IN EFFECT.**

## 1. Purpose

This policy tells you what you may not do with StealthLab, and describes — honestly, matching
what the code actually does — how we try to catch violations before content becomes public. It
does not replace the [Terms of Service](./TERMS_OF_SERVICE.md), which it supplements.

## 2. Prohibited activities

You may not use StealthLab to create, request, execute, publish, or ingest a procedure that:

- **Steals or harvests credentials** — passwords, API keys, tokens, SSH/private keys, session
  cookies, browser-saved credentials, or similar.
- **Gains unauthorized access** to a system, account, or repository you do not own or have
  explicit permission to access.
- **Exfiltrates data** to an external destination without authorization — including reading a
  known credential store or secrets file and sending its contents elsewhere.
- **Is destructive or malware-shaped** — e.g. mass filesystem deletion, disk wiping, database
  drops, fork bombs, disabling security tooling (firewall/antivirus), or ransomware/keylogger
  behavior.
- **Establishes unauthorized persistence or escalates privilege** — e.g. adding passwordless
  sudo, planting SSH `authorized_keys`, disabling UAC, or creating hidden admin accounts —
  without the explicit, legitimate context that would make this a normal admin/devops procedure.
- **Deploys reverse/bind shells, known offensive-security tooling, or malware by name** (e.g.
  Mimikatz, Cobalt Strike, Meterpreter, Metasploit, ransomware/keylogger tooling), outside of
  clearly-scoped, authorized security-research contexts you disclose.
- **Abuses third-party systems** — scraping, spamming, or attacking systems you don't control.
- **Uploads secrets** you did not intend to share — even though we run automated redaction (see
  §4), you should never rely on it as your only safeguard.
- **Abuses Global Commons publication** — e.g. publishing content you don't have rights to,
  publishing spam/duplicate/low-effort content at scale, or manipulating Candidate/Verified
  status.
- **Is illegal, or facilitates illegal activity,** in a jurisdiction relevant to you or to
  StealthLab.

This list is illustrative, not exhaustive. **[LEGAL: confirm enforceability and add any
jurisdiction-specific prohibited-use language.]**

## 3. How this maps to what's actually implemented

Internet-sourced procedures pass through an **automated admission gate**
(`backend/app/services/ingestion_admission.py`) before they are visible in search or retrieval.
This is the real mechanism behind the promises above for ingested content — read this section
carefully, because it describes both what is checked and, just as importantly, what is not.

The gate makes a **safety/hygiene decision, never a correctness decision.** It never marks
anything "verified"; it only decides whether a candidate is structurally sane, free of obvious
secrets, and free of obviously malicious intent. Three outcomes:

- **Admit** — no reject/review-worthy signal found (or an optional LLM risk-classification call,
  used only for ambiguous cases when a model is configured, explicitly clears it). The procedure
  becomes visible immediately.
- **Review (quarantine)** — an ambiguous signal fired (e.g. a bare secret-shaped literal, a
  persistence/privilege-escalation mention with no exfiltration/destructive combination, or a
  prompt-injection signal). The row is captured for inspection but excluded from normal
  retrieval. It stays in this state unless and until an optional LLM escalation call explicitly
  clears or rejects it — with no model configured, or on any call failure, it stays quarantined
  rather than auto-clearing.
- **Reject** — an unambiguous signal fired (explicit credential-theft/harvesting language, a
  destructive/malware-shaped command match, a known-malicious tool name, or a structurally
  malformed payload), or a credential-access mention combined with an exfiltration signal.
  **Nothing is stored** in this case except an audit trail of *why* (reason codes only, never the
  raw matched secret/malicious text).

Checks are deterministic pattern/keyword matching plus, only for ambiguous review-tier findings
and only when a model client is configured, one narrowly-scoped LLM risk classification call —
the bulk ingestion path makes **zero** LLM calls per procedure otherwise. Any secret-shaped
literal found is **always redacted** before capture, reusing the same redaction primitive used
elsewhere in the codebase (`app/services/trace_redaction.py`); a private-key block is never
admitted even redacted.

**What this gate does NOT do**, stated plainly because overclaiming here would be false:

- It does not execute, fetch, or run anything it inspects — it only reads declared names/text.
- It is a **narrow, deterministic-first heuristic screen**, not a general-purpose malware
  detector. It is explicitly scoped to a fixed set of pattern families (credential access,
  exfiltration verbs, destructive commands, persistence/privesc language, known malicious tool
  names). Genuinely novel obfuscated attacks, attacks phrased outside these patterns, or
  attacks split across multiple procedures may not be caught.
- The optional LLM escalation is **not a guarantee** either — it fails toward the conservative
  "review" state on any parse failure, timeout, or uncertain verdict, but a model call can still
  be wrong.
- This gate covers the **internet/public-source ingestion path only**
  (`skill_ingestion.compile_skill_artifact` / `ingest_skill_md`). The separate, explicit
  Local → Global user-publish path (`backend/app/services/publish.py`) has its own,
  independently-shipped redaction/dedup/provenance discipline, which this policy also relies on
  but which is a different code path.

**Automated screening reduces risk. It does not eliminate it, and it is not a substitute for your
own judgment before running any procedure.** See the [Verification Disclaimer](./VERIFICATION_DISCLAIMER.md).

## 4. Reporting abuse

**[FOUNDER: define an actual abuse-reporting channel/contact. Placeholder: report suspected
Acceptable Use violations to CONTACT EMAIL.]** See also `SECURITY.md`'s vulnerability-reporting
process (GitHub private vulnerability reporting) for security-specific issues.

## 5. Enforcement

We may remove content, quarantine a submission, suspend or terminate accounts, and/or report
unlawful activity to authorities, at our discretion, for violations of this policy.
**[LEGAL: confirm enforcement language is consistent with the Terms of Service.]**

---
*Generated from repository state on 2026-09-09. Cross-references:
`backend/app/services/ingestion_admission.py`, `backend/app/services/skill_ingestion.py`,
`backend/app/services/publish.py`, `backend/app/services/trace_redaction.py`.*
