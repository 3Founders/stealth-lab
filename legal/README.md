# Legal documents (drafts)

**Status: draft templates for review by Indian counsel. Not legal advice. Nothing here has been reviewed by a lawyer or sent to anyone.** Text in `[BRACKETS]` is a blank or a choice. `Counsel note` blockquotes explain choices and are removed before a document is sent.

## Layout

```
legal/
  README.md            this file
  b2b/
    mnda.md            Mutual NDA: sign before any access to a customer's repos, staging or tickets
    pilot_agreement.md Short pilot, signable by a founder before incorporation (novation to the entity)
    msa.md             Master Services Agreement, with Exhibits A (Service Levels), B (Acceptable Use),
                       C (DPA placeholder) and D (Order Form fields)
  b2c/                 (not started) Creator Terms, content licence, takedown policy, payout terms
  dpdp/                (not started) DPA, privacy notice, subprocessor list, retention schedule, breach procedure
  tax/                 (not started) GST invoicing, Section 194O TDS, foreign payouts (FEMA/RBI) notes
  corporate/           (not started) founders' agreement, PIIA, IP-assignment deed, licence audit matrix
```

## Order of use with a customer

1. **MNDA** (before any access).
2. **MSA** + **Order Form / Pilot SOW** + **DPA** + security exhibit (before real data or paid fees).
3. Pilot starts in shadow mode; Exhibit A service levels apply only from the Go-Live Date.

## Defaults I chose (change any of them)

| Topic | Draft says | Why |
|---|---|---|
| Governing law, disputes | India; sole-arbitrator arbitration under the 1996 Act; seat **[Bengaluru / Mumbai / New Delhi]**; urgent interim relief from courts | Usual for B2B; interim relief kept open because arbitration is slow to start |
| MNDA term | 2 years of disclosures; 5 years survival; source code and trade secrets for as long as secret | Common market terms |
| Residuals clause | **Omitted** | The company will see customers' source code |
| Subprocessors in the MNDA | Allowed only if listed in Schedule 1, zero-retention / no-training where offered, 10 days' notice before adding one that receives source code | Without it, sending a prompt to a model host could breach the NDA |
| Customer Content ownership | Customer owns it and its outputs; Company holds a narrow licence to run the service | Matches the founders' IP position |
| Company IP | Company owns the service, routing logic, indexes, procedure libraries and data-agnostic "goal signatures" | As instructed |
| Training on customer data | None by Company; model providers told to use zero-retention / no-training settings **where they offer it** | A flat guarantee would exceed what the provider contracts support |
| Liability | **Pilot: nil** (bracketed, or a nominal amount). **Paid: fees paid and payable in the 3 months before the claim.** Carve-outs: fraud, wilful misconduct, gross negligence, personal injury, non-excludable law, payment of fees. Optional separate super-cap for confidentiality, data breach and IP indemnity | The nil and 3-month figures follow the founders' instruction. Expect pushback on the nil cap |
| Service levels | 99.9% monthly availability from the **Go-Live Date only**; measured by external probes; credits are the sole remedy | Not promised during the pilot, and not to be signed until the service is deployed with more than one instance |
| Savings guarantee | **None** (disclaimed). Performance fee only as the Order Form's measurement schedule says | There is no measured real-session saving yet (BLOCKERS X3) |
| Gain-share | Not in the MSA; lives in the Order Form and a Measurement Schedule (rate, baseline, control, floor, cap, audit) | The baseline must be measured in cache-aware dollars with a control |
| Data location | **Not promised.** The MSA points to the DPA | Neon (us-east-2), Vertex (global/us-central1) and Cloudflare R2 are in use; "India only" is false today |
| Incident notice to customer | 48 hours after confirmation, plus statutory reports (CERT-In) | A customer-facing window longer than CERT-In's 6 hours, which is a separate duty |
| Payment | Net 30; interest 1% per month; GST extra; TDS deducted only as lawfully required, with certificate | Standard Indian invoicing |
| Stamp duty | Company bears it; stamped under the applicable state's law | Avoids an unenforceable unstamped agreement |

## Signing before the entity exists

The company or LLP does not exist yet, and a non-existent entity cannot sign. Rule used here:

| Document | Before incorporation | After incorporation |
|---|---|---|
| **MNDA** | May be signed by a founder as promoter using Option B in the parties clause. It carries an advance-consent **novation** to the new entity. The founder is personally exposed until the Novation Date | Use Option A |
| **MSA, Order Form, DPA** | **Do not sign.** They give the customer source-code access, indemnities, liability and fees. A founder signing them is personally liable with no limited-liability shield | Sign in the entity's name |

Practical route: incorporate first. A private limited company via SPICe+ usually takes one to two weeks; an LLP is similar. Sign the MNDA now (Option B) if a customer cannot wait, then the MSA after the novation. Also sign an IP-assignment deed from each founder to the entity at incorporation, or the entity will not own the code. Private limited versus LLP is a funding question: equity investors and employee stock options generally need a private limited company.

## Things I could not decide (need you or counsel)

1. **Entity details** for the Company: legal name, CIN, registered office, signatory. The company is not yet incorporated.
2. **Seat of arbitration** and whether you will ever accept foreign law or an institutional seat (SIAC) for a foreign customer.
3. **Fallback positions** on liability: nominal pilot cap, super-cap multiple, whether confidentiality breach stays inside the cap.
4. **Subprocessor list** for Schedule 1 and the DPA, and the **actual hosting regions**.
5. **Product name** (L1). The drafts use `[COMPANY LEGAL NAME]` and "the Service"; no vendor marks.
6. **Whether any non-Indian customers** are in scope (affects law, tax, GDPR).

## Conflicts with what is deployed today (resolve before signing a customer)

- The pasted brief promises AWS Mumbai / GCP Delhi processing. The stack uses **Neon in us-east-2, Vertex in `global` / `us-central1`, Cloudflare R2**. Either change the infrastructure or describe the true flows in the DPA.
- **24-hour purge** vs **CERT-In 180-day log retention in India** vs gain-share audit evidence: three different retention rules for three kinds of data. Write them into the DPA's retention schedule.
- **99.9% uptime** from a single, undeployed process. Do not sign Exhibit A until it can be met.
- **No training** cannot be promised more strongly than each model provider's terms allow (BLOCKERS P9). Check them first.

## Next documents (in priority order)

1. **Order Form / Pilot SOW** with the **Measurement Schedule** (gain-share formula, baseline, control, floor, cap, audit, dispute).
2. **DPA** (Data Fiduciary / Data Processor under the DPDP Act, 2023), **subprocessor list**, **retention and deletion schedule**.
3. **Security addendum** and incident procedure; **SLA/support** detail if needed beyond Exhibit A.
4. **Terms of Service**, **Privacy Policy**, **cookie notice** (public site).
5. Marketplace: **Creator Terms**, content licence, takedown and grievance process, payout and tax terms.
6. Corporate: founders' agreement, **PIIA**, IP-assignment deed for pre-incorporation work, OSS / model / dataset licence matrix.
