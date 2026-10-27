# Outreach: if mail bounces, lands in spam, or the domain is listed (runbook)

From `docs/research/outreach_best_practices_pass2_2026-10-27.md` (items 5 and K). Most steps are vendor-sourced (M/L); the provider
portals are the authority. Check them before relying on a time estimate.

## Pause triggers (stop the whole batch, then follow the steps)
- hard bounces above 2% of a batch, or any spam complaint;
- a bounce naming a blocklist, or a rejection (5xx) that says the domain or IP is listed or the sender is blocked;
- the out-of-office count (`sync-inbox` prints `auto_replies`) stays at zero while mail to the same companies is going out: possible junk placement.

## Steps
1. **Stop sending.** Do not retry rejected addresses (a 5xx means never again to that address).
2. **Date it.** Note when the first problem appeared and what you had sent in the 48 hours before.
3. **Decode the bounce text:**
   - `zen.spamhaus.org`: the sending IP is listed. `dbl.spamhaus.org`: a domain in the message is listed.
   - `b.barracudacentral.org` (Barracuda), `bl.spamcop.net` (SpamCop), SURBL/URIBL: a link domain in the message is listed.
   - Microsoft codes such as `5.7.606`, `5.7.708`, `S3150`: Microsoft's own reputation systems; appeal at sender.office.com.
4. **Diagnose honestly:** unverified addresses? too fast a ramp? a wrong personal detail? a link domain listed? a complaint?
5. **Fix the cause first**, then ask for removal on each list's own portal: Spamhaus (check.spamhaus.org), Barracuda (web form), SpamCop
   (it expires by itself), Microsoft (sender.office.com). Removal times quoted by vendors range from hours to days.
6. **Resume at half the earlier volume for a week**, re-verify every address, and watch bounces daily.
7. **Check lists weekly for a month, then monthly** (MXToolbox or similar).

## If the mailbox is suspended
Stop, read the provider's notice, and appeal through the provider. A separate sending domain means the main domain's mail is not affected.

## If a recipient complains to a regulator
Do not contact them again, record the date, the contact's source and basis (the contact list has both), and put it to a lawyer.
