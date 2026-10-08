> **DRAFT TEMPLATE. Not legal advice.** For review by Indian counsel. `[BRACKETS]` are blanks or choices. **Counsel note** blocks are deleted before sending. Written for the Digital Personal Data Protection Act, 2023 (the **DPDP Act**) and its rules. Check the rules' current text and phase-in dates before use. If a customer is outside India, GDPR or another law may also apply, which this draft does not cover.

# DATA PROCESSING AGREEMENT

Exhibit C to the Master Services Agreement dated **[DATE]** (the **MSA**) between **[CUSTOMER LEGAL NAME]** (**Customer**) and **[COMPANY LEGAL NAME]** (**Company**).

## 1. Roles and scope

**1.1** For personal data that Company processes on Customer's behalf through the Service (**Customer Personal Data**), Customer is the **Data Fiduciary** and Company is the **Data Processor**, as the DPDP Act uses those terms.

**1.2** Company processes Customer Personal Data **only on Customer's documented instructions**, which are the MSA, this DPA and the Customer's use of the Service, and only for providing the Service. If Company thinks an instruction breaks the law it tells Customer.

**1.3** The parties expect little or no personal data: the Service is designed to receive task descriptions, not personal data. Annexure 1 lists what is expected.

**1.4** For account data of Customer's users (name, work email, sign-in records) that Company needs to run accounts, Company acts as an independent Data Fiduciary for that limited purpose, as described in Company's privacy notice.

## 2. Customer's duties

Customer is responsible for having a lawful basis and any notice and consent the DPDP Act requires for the personal data it puts into the Service, and for telling its users not to put in personal data the Service does not need.

## 3. Company's duties

Company will:

- (a) keep Customer Personal Data confidential and make sure personnel with access are bound by confidentiality;
- (b) apply **reasonable security safeguards** to prevent a personal data breach, at least those in Annexure 2;
- (c) **engage subprocessors** only under a written contract with data-protection duties no less protective than this DPA, and remain responsible for them (section 5);
- (d) help Customer, at reasonable cost, to respond to requests from Data Principals (access, correction, erasure, grievance) and to Customer's duties to the Data Protection Board;
- (e) **notify Customer of a personal data breach** affecting Customer Personal Data without undue delay and within **[48]** hours of confirming it, with the information in section 6;
- (f) at Customer's choice, **return or delete** Customer Personal Data when the MSA ends or when the purpose is served (section 8); and
- (g) allow Customer to check compliance (section 9).

## 4. Data location and transfers

**4.1** Annexure 3 lists where Customer Personal Data is stored and processed and by whom. Company does not promise that processing stays in India. As of the date of this DPA, **[the database and some model calls run outside India, as listed in Annexure 3]**.

**4.2** Company transfers personal data outside India only to countries the Central Government has not restricted under section 16 of the DPDP Act, and tells Customer before moving data to a new region. **[OPTION: Customer may require India-only processing for an agreed extra fee, once Company offers it.]**

> **Counsel note:** Do not state an India-only location unless the infrastructure changes. Today the database, some model calls and object storage run in the United States or globally.

## 5. Subprocessors

**5.1** Customer authorises the subprocessors in Annexure 3. Company gives **[30]** days' written notice before adding or replacing one that receives Customer Personal Data.

**5.2** Customer may object in writing on reasonable data-protection grounds within **[15]** days. If the parties cannot resolve it, Customer may stop using the affected part of the Service, or end the MSA, and receive a refund of prepaid fees for the unused period.

**5.3** For model providers, Company uses zero-retention and no-training settings where the provider offers them, and does not otherwise promise what a provider does with data.

## 6. Personal data breach

**6.1** Company tells Customer within **[48]** hours of confirming a breach, by email to Customer's security contact, with: what happened, what data and how many people it affects if known, likely effects, steps taken, and a contact.

**6.2** Company also makes any report the law requires of it (for example to CERT-In within 6 hours of noticing a reportable cyber incident). Customer decides about notice to the Data Protection Board and to Data Principals, and Company gives the facts it needs for that.

**6.3** Company keeps a record of each breach and its cause and fix.

## 7. Retention

Company keeps Customer Personal Data only as the retention schedule in Annexure 4 states, and no longer than needed for the Service, to meet a legal duty or to resolve a dispute.

## 8. Return and deletion

**8.1** On Customer's request or within **[30]** days after the MSA ends, Company returns Customer Personal Data in a common format and then deletes it, and confirms in writing.

**8.2** Backups are deleted when they expire in the ordinary course (up to **[35]** days). Company keeps a record it must keep by law (for example billing records) and tells Customer what.

**8.3** Content that Customer chose to make public stays public. Data-agnostic statistics from the Service's routing models may stay, because they identify no person.

> **Counsel note:** Company's design closes and appends records, and a full hard-delete path is not yet built (`final_prod_docs/02_data_inventory_and_retention.md`). Do not promise full erasure until it is.

## 9. Audit

Company provides the information needed to show compliance and, once a year or after a breach, allows an audit by Customer or its auditor on **[20]** days' notice, in business hours, bound by confidentiality, at Customer's cost, without access to other customers' data. Company may satisfy this by giving a recent independent report or completed security questionnaire **[where it has one]**.

## 10. Liability

Liability under this DPA is under the MSA's limits, with the carve-outs listed there. **[OPTION: a separate cap for data protection breaches.]**

## 11. General

Term as the MSA. If this DPA conflicts with the MSA on personal data, this DPA controls. Law and disputes as in the MSA.

---

## Annexure 1: Processing details

| Item | Detail |
|---|---|
| Subject and duration | Providing the Service for the MSA term |
| Nature and purpose | Retrieving procedures, recording outcomes, choosing models, optional model calls |
| Data Principals | Customer's authorised users and contractors **[others]** |
| Categories of personal data | Work name and email, user identifier, usage metadata, IP address in logs. Any personal data that users put into task descriptions or model calls despite instructions |
| Sensitive data | None expected. Not to be put in |

## Annexure 2: Security measures

Authenticated access to every request; tenant isolation by application checks (database row-level security policies are written but not yet in force, because the services do not yet connect with the restricted role they apply to); encryption in transit and at rest by providers **[confirm]**; screening of retrieved content; no storage of `call_model` prompts or outputs; audit ledger of model calls; organisation budgets and kill switch; access limits for staff; incident procedure (`legal/dpdp/incident_procedure.md`); vulnerability handling policy. Planned, not yet in place: database row-level security in force (services connecting as the restricted database role), independent penetration test, single sign-on for customer staff, hard-delete path.

## Annexure 3: Subprocessors and locations

Taken from `docs/legal/SUBPROCESSORS.md` once the blanks there are confirmed.

| Provider | Purpose | Location | Data |
|---|---|---|---|
| **[Neon]** | Database | **[region]** | Stored data |
| **[Supabase]** | Sign-in | **[region]** | Account data |
| **[Google Vertex AI]** | Embeddings and judging | **[region]** | Task text |
| **[Cloudflare R2]** | Object storage | **[jurisdiction]** | Raw artifacts |
| **[Container host]** | Runs the service | **[region]** | Traffic in transit |

## Annexure 4: Retention schedule

| Data | Retention | Basis |
|---|---|---|
| Account data | While the account exists, then deleted within 30 days | Contract |
| Query hash and returned procedure ids | **[180 days]** | Security log duty |
| Routing outcomes, tokens, cost | **[Term of the MSA, then aggregated]** | Service and billing |
| Provider-call ledger | **[8 years]** | Billing and tax records |
| Audit and security events | **[180 days]** | Security log duty |
| Application logs | **[30 days]** | Operations |
| Backups | **[35 days]** | Recovery |
