# Review plan: what to read, and what needs counsel

**All of these are drafts written by an AI assistant, not by a lawyer.** "You can review" means a founder can check the facts and commercial choices. It does not mean it is safe to sign without counsel. The column "Counsel" says how much a lawyer should do.

## Reading order (about 2 hours for the first five)

| # | Document | File | Read to decide |
|---|---|---|---|
| 1 | Reading guide and defaults | `legal/README.md` | The defaults I chose and the open decisions |
| 2 | Pilot Agreement | `b2b/pilot_agreement.md` | Fee option, liability, length |
| 3 | Mutual NDA | `b2b/mnda.md` | Term, seat, subprocessors |
| 4 | Master Services Agreement | `b2b/msa.md` | Liability, SLA, data location, fees |
| 5 | Order Form and Measurement Schedule | `b2b/order_form_and_measurement_schedule.md` | The savings formula, control share, margin |
| 6 | Data Processing Agreement | `dpdp/dpa.md` | Breach window, locations, retention |
| 7 | Security Addendum, Incident procedure | `b2b/security_addendum.md`, `dpdp/incident_procedure.md` | Whether every "in place" claim is true |
| 8 | IP assignment deed, Founders' agreement | `corporate/` | Equity, vesting, institute claims |
| 9 | Novation notice, PIIA | `corporate/` | Forms, mostly standard |
| 10 | Creator Terms, Tax notes, Licence matrix | `b2c/`, `tax/`, `corporate/` | Only if relevant now |

## Which need counsel

| Document | Counsel | Why | Before when |
|---|---|---|---|
| **IP assignment deed** | **Required** | Employer or institute claims can defeat it; the Copyright Act term and territory rule; stamp duty | Incorporation |
| **Founders' agreement** (and Articles, shareholders' agreement) | **Required** | Vesting, buy-back and share forfeiture must fit the Companies Act and Articles; non-compete limits | Incorporation |
| **MSA** | **Required** | Liability, indemnity, data location, arbitration, governing law | First paid contract |
| **Order Form and Measurement Schedule** | **Required**, plus a statistician or accountant for the method | A fee computed from data the customer controls; enforceability; audit | First savings-based fee |
| **DPA** | **Required** | DPDP Act roles, section 16 transfers, breach duties, rules and phase-in dates | First customer personal data |
| **Pilot Agreement** | **Required** (short review) | You will sign it personally; the novation and the nil cap | First pilot |
| **Mutual NDA** | Recommended (short) | Standard, but you will sign it personally; stamp duty | First NDA |
| **Novation notice** | Recommended | Release of the founder, consent form, stamp duty | Incorporation |
| **Creator Terms** | **Required** if used | Intermediary rules, grievance officer, takedown timing, payout tax | Opening contributions |
| **Security Addendum, Incident procedure** | Light review | Mostly factual; counsel checks CERT-In and DPDP deadlines | First enterprise |
| **PIIA** | Recommended | Enforceability, moral rights, stamp duty | First hire |
| **Tax notes** | **Chartered accountant** | Not a legal document | First invoice |
| **Licence matrix** | Counsel after a scanner run | Copyleft and dataset licences | Before a sale or a customer review |
| **Existing `docs/legal/` drafts** (Terms of Service, Privacy Policy, cookies, acceptable use, Global Commons terms) | **Required** before publishing | Already drafted earlier; not rechecked in this pass | Public launch |

## Questions to take to counsel (copy into an email)

1. **Pre-incorporation signing.** Is the Option B NDA with advance consent and novation enough, or should a founder sign nothing until the company exists? Does the LLP Act matter if we choose an LLP?
2. **IP.** Does any employer, institute or sponsor have a claim on the code? What is the stamp duty on the assignment, and should the consideration be shares or a nominal sum?
3. **MSA.** Is a nil pilot cap and a three-month paid cap realistic with Indian enterprise buyers? What super-cap would you propose for confidentiality and data breach?
4. **Data location.** The database is in the US and some model calls are global. What must the DPA say, and what do we need to do under section 16 of the DPDP Act?
5. **Breach windows.** We say 48 hours to customers, with CERT-In's 6 hours separate. Are those right for us?
6. **Deletion.** Our design closes records and appends new ones, with no hard delete yet. What must we be able to erase on request, and what may we keep for audit?
7. **Performance fee.** Is the control-group, non-inferiority method in Schedule 1 enforceable? Who decides a dispute over the data?
8. **Marketplace.** Are we an intermediary? What must the grievance process contain? Does Section 194O apply to payouts?
9. **Seat of arbitration** and whether to accept a foreign customer's law or SIAC.
10. **Foreign customers.** Do GDPR, export-of-services GST or US terms apply to the first deals?
11. **Datasets.** May we show procedures derived from SWE-rebench and GitHub to paying customers?

## Decisions only you can make (blank in the drafts)

Entity names and addresses; arbitration seat; pilot fee and length; liability fallbacks; savings rate, control share and margin; equity split and vesting; retention numbers; whether `call_model` is platform-supplied; whether to open contributions; the product name.
