> **DRAFT TEMPLATE. Not legal advice.** Prepared for review by Indian counsel before use. Text in `[BRACKETS]` is a blank or a choice. Blockquotes marked **Counsel note** explain a choice and are deleted before sending. Several terms reflect founder instructions that a customer is likely to negotiate; they are flagged.

# MASTER SERVICES AGREEMENT

This Master Services Agreement (the **Agreement**) is made on **[DATE]** (the **Effective Date**) between:

1. **[COMPANY LEGAL NAME]**, a **[company incorporated under the Companies Act, 2013 (CIN [●]) / limited liability partnership under the Limited Liability Partnership Act, 2008 (LLPIN [●])]**, registered office **[ADDRESS]** (**Company**); and
2. **[CUSTOMER LEGAL NAME]**, **[a company incorporated under [law] (registration no. [●])]**, registered office **[ADDRESS]** (**Customer**).

## 1. Definitions

- **Affiliate**: an entity that controls, is controlled by or is under common control with a Party.
- **Authorised User**: an employee or contractor of Customer or its Affiliates whom Customer permits to use the Service.
- **Confidential Information**: has the meaning in Section 9.
- **Connected Model**: a third-party or Customer-supplied model or AI agent that the Service calls for Customer, including through a Customer-provided API key (**BYOK**).
- **Customer Content**: all data Customer or its Authorised Users submit to or make available to the Service, including Source Code, repository and ticket contents, prompts, task descriptions, repository facts, and the outputs the Service returns to Customer. It excludes Service Data.
- **Documentation**: the then-current user and technical documentation Company publishes for the Service.
- **DPA**: the data processing agreement between the Parties (Exhibit C).
- **Go-Live Date**: the date stated in an Order Form on which the paid, production phase starts.
- **Order Form**: a document signed by both Parties that refers to this Agreement and describes the Services, term, fees and any pilot (including any statement of work).
- **Pilot Phase**: any period an Order Form designates as a pilot, trial, evaluation or "shadow" period, ending on the Go-Live Date or earlier termination.
- **Service**: Company's hosted software service (including its MCP server, retrieval, procedure library, model-routing recommendations and optional model-calling functions), as described in the Documentation and the Order Form, and any updates.
- **Service Data**: data about the operation and use of the Service that Company generates or collects, such as usage counts, timings, token counts, cost figures, error and performance data, and derived routing statistics, and that contains no Customer Content.
- **Source Code**: as defined in the Mutual Non-Disclosure Agreement between the Parties, if any (the **MNDA**).
- **Third-Party Content**: procedures, "ways", claims, examples and other content in the Service that was contributed by persons other than Company or Customer.

## 2. Structure and order of precedence

**2.1** This Agreement, each Order Form, the DPA and the Exhibits form one agreement. If they conflict, the order of precedence is: (1) the DPA (for personal data only); (2) the Order Form (for the matters it expressly addresses); (3) this Agreement; (4) the Exhibits; (5) the Documentation.

**2.2** No terms in a purchase order or Customer policy apply, even if signed or accepted, unless an Order Form states that they do.

## 3. The Service

**3.1 Access.** Subject to this Agreement and payment of fees, Company grants Customer and its Authorised Users a non-exclusive, non-transferable, non-sublicensable right, during the term, to access and use the Service for Customer's and its Affiliates' internal business purposes, within the limits in the Order Form.

**3.2 Pilot Phase and shadow mode.** During a Pilot Phase the Service may be provided in "shadow mode", in which Company observes and recommends but does not change Customer's systems, models or routing. Pilot Services are provided to evaluate fit, may be incomplete, and carry no service levels. Either Party may end a Pilot Phase on **[7]** days' written notice without liability for ending it.

**3.3 Changes.** Company may improve and change the Service. It will give **[30]** days' written notice before removing or incompatibly changing a documented tool, interface or Documentation feature that Customer uses, except where a change is required by law or to address a security risk.

**3.4 Support.** Company will provide support as stated in the Order Form or, if none is stated, by email during **[business hours in India, Monday to Friday, excluding public holidays]**.

**3.5 Subcontractors and subprocessors.** Company may use subcontractors and the subprocessors listed in the DPA and remains responsible for their performance of its obligations under this Agreement.

## 4. Customer responsibilities

**4.1** Customer will (a) ensure its Authorised Users comply with this Agreement and Exhibit B; (b) keep credentials and access tokens secure and tell Company promptly of any unauthorised use; (c) ensure it has the rights, licences and consents needed to give Company the Customer Content and to give the instructions it gives; and (d) provide the cooperation and access Company reasonably needs, including technical contacts.

**4.2** Customer will not, and will not permit others to: (a) copy, resell or provide the Service to third parties other than its Affiliates; (b) reverse engineer or attempt to derive the Service's source code, model-routing logic, indexes or ranking methods, except where law permits it despite this restriction; (c) use the Service to build a competing service or to train a model that competes with the Service; (d) circumvent usage limits, access controls or the Service's content screening; (e) submit malware or content that violates Exhibit B; or (f) use the Service in violation of law.

**4.3 AI outputs and agents.** The Service returns information that AI agents may act on. Customer is responsible for deciding what its agents may do, for reviewing outputs before they affect production systems, and for keeping appropriate human approval over destructive, financial, security-sensitive or external-communication actions. Customer will not treat Third-Party Content or any output as instructions from Customer's own users.

**4.4 Customer's models and keys.** Where Customer connects its own model providers or API keys (BYOK), Customer is responsible for those providers' terms, charges and rate limits and for the keys' security. Company is not responsible for the availability or output of a Connected Model that it does not operate.

## 5. Customer Content and data use

**5.1 Ownership.** As between the Parties, Customer owns all Customer Content, including Source Code and the code and other outputs produced for Customer. Nothing in this Agreement transfers Customer's ownership to Company.

**5.2 Licence to Company.** Customer grants Company a non-exclusive, worldwide, royalty-free licence during the term (and afterwards for the periods in Section 16.4) to host, process, transmit and display Customer Content, and to create temporary or derived representations of it (such as abstract syntax tree data, hashes and embeddings), only to provide the Service to Customer, to maintain its security and integrity, and to comply with law.

**5.3 No training.** Company will not use Customer Content to train, fine-tune or evaluate any machine-learning model for the benefit of anyone other than Customer. Company will instruct each model provider to which it sends Customer Content to apply its zero-retention and no-training settings where the provider offers them, and will list those providers in the DPA.

> **Counsel note:** Company controls what it sends, but cannot fully control a third-party provider's own terms. Verify each provider's current retention and training terms before signing, and align this clause with them (BLOCKERS P9). Do not promise more than the provider contracts support.

**5.4 Redaction.** The Service attempts to detect and remove credentials and secrets before storing derived data. This is a safeguard and not a guarantee. Customer will not intentionally submit credentials, special categories of personal data or other data it is not permitted to share.

**5.5 Service Data.** Company owns Service Data and may use it to operate, secure, measure and improve the Service and to compute aggregated, de-identified statistics, provided Service Data contains no Customer Content or Customer Confidential Information and does not identify Customer or any individual.

**5.6 Shared learning.** Customer may choose, in an Order Form or in the Service's settings, to contribute procedures or discoveries from its work to a shared library. Nothing derived from Customer Content is contributed or shown to any other customer unless Customer so chooses for that item in writing or through that setting. Customer's private procedures remain visible only within Customer's organisation.

**5.7 Customer Content location.** Where Customer Content is processed and stored is stated in the DPA. Company will not change the regions listed there for Customer Content without notice under the DPA.

> **Counsel note:** Do not state "India only" unless the deployed infrastructure actually keeps Customer Content in India. Today database and some model calls run outside India.

## 6. Third-Party Content and AI outputs

**6.1** Third-Party Content is provided "as is". Company screens contributed content for malicious and unsafe material but does not verify that it is correct, safe or suitable for Customer's environment.

**6.2** Company marks content it serves as untrusted data and does not guarantee that any output is accurate, complete, secure or non-infringing. Customer is responsible for evaluating outputs before use.

## 7. Fees, payment and taxes

**7.1 Fees.** Customer will pay the fees in each Order Form. Unless an Order Form says otherwise, fees are in **[Indian rupees]**, are non-cancellable, and are non-refundable except as this Agreement expressly provides.

**7.2 Performance-based fees.** If an Order Form includes a fee calculated from cost savings or other measured results (**Performance Fee**), the Performance Fee is calculated only as the Order Form and its measurement schedule provide, using the baseline method, data sources and cap stated there. Company will make the underlying measurements available to Customer with each invoice, and Customer may, once per **[quarter]** and on **[10]** business days' notice, inspect the measurement records relating to the invoice under reasonable confidentiality terms.

> **Counsel note (gain-share):** Define the baseline in the Order Form in cache-aware dollars (cache reads and writes are billed differently from fresh input), use a matched control or holdout rather than a historical average, and net losses against gains. The 25% rate and baseline method belong in the Order Form, not here.

**7.3 Invoicing and payment.** Company will invoice **[monthly / quarterly in advance or arrears per the Order Form]**. Customer will pay undisputed invoices within **[30]** days of receipt. Overdue amounts bear interest at **[1.0]% per month** (or the maximum lawful rate, if lower) from the due date.

**7.4 Disputed amounts.** Customer will notify Company in writing, with reasons, of any good-faith dispute within **[15]** days of the invoice and pay the undisputed part. The Parties will try to resolve the dispute within **[30]** days before using Section 18.

**7.5 Taxes.** Fees exclude goods and services tax (**GST**) and other indirect taxes, which Customer will pay on a valid tax invoice. If law requires Customer to deduct tax at source (**TDS**), Customer will deduct only the lawfully required amount, pay it to the authorities on time, and give Company the certificate within the statutory period; Customer will pay the balance in full. Each Party bears taxes on its own income.

**7.6 Suspension.** Company may suspend access to the Service for non-payment more than **[15]** days after written notice of overdue amounts, or immediately if needed to stop an imminent security threat or a breach of Exhibit B, and will restore access when the cause is resolved.

## 8. Intellectual property

**8.1** Company and its licensors own the Service, the Documentation, all software, model-routing and ranking logic, vector and other indexes, procedure libraries (other than Customer Content), the "goal signatures" and other data-agnostic structures it derives, and all related intellectual property. Customer receives only the rights in Section 3.1.

**8.2** Customer owns Customer Content and the intellectual property in it.

**8.3 Feedback.** Customer may give suggestions about the Service. Company may use them freely, provided it does not use Customer Content or Confidential Information in doing so.

**8.4** Each Party's open-source and third-party components remain subject to their own licences. Company will make available, on request, a list of third-party notices and attributions that its licences require.

## 9. Confidentiality

**9.1** **Confidential Information** means non-public information that a Party (**Discloser**) discloses to the other (**Recipient**) under this Agreement that is marked confidential or would reasonably be understood to be, including, for Customer, Customer Content and Source Code, and for Company, the Service's non-public technical information, routing logic, indexes, pricing and security measures. It does not include information that the Recipient can show by written records: (a) is or becomes public through no breach of this Agreement; (b) was lawfully known to it, free of any duty of confidence, before disclosure; (c) was independently developed by it without use of or reference to the Discloser's information; or (d) was lawfully received from a third party free to disclose it.

**9.2** The Recipient will use Confidential Information only to perform or exercise rights under this Agreement; protect it with at least reasonable care; disclose it only to personnel, advisers and subprocessors who need it and are bound by equivalent duties; and give prompt notice of any unauthorised disclosure. It may disclose it as required by law after giving the Discloser prompt notice where lawful.

**9.3** These duties continue during the term and for **[five (5)]** years after it ends, and for Source Code and trade secrets for as long as they remain confidential.

**9.4** The MNDA governs information disclosed before the Effective Date. This Section governs information disclosed on or after it. If the Parties have no MNDA, this Section applies in full.

## 10. Data protection and security

**10.1 Roles.** For personal data in Customer Content, Customer is the **Data Fiduciary** and Company is the **Data Processor** under the Digital Personal Data Protection Act, 2023, as set out in the DPA. Company processes that personal data only on Customer's documented instructions and under the DPA.

**10.2 Security measures.** Company will maintain administrative, technical and organisational measures appropriate to the risk, including access control, encryption in transit and at rest, tenant isolation, logging of access to Customer Content, vulnerability management and employee confidentiality, as summarised in the security exhibit the Parties agree in the Order Form or in the DPA (**Security Measures**). Company will not materially reduce the Security Measures during the term.

**10.3 Incident notice.** Company will notify Customer's security contact **without undue delay and within [48] hours** after Company confirms a personal-data breach or an unauthorised access to Customer Content, and will give the information Customer reasonably needs to meet its own legal duties. Company will also make the reports required of it by law (including to CERT-In).

**10.4 Logs.** Company keeps operational metadata and security logs for the periods in the DPA. These logs contain identifiers, counts, timings and hashes and no Customer Content.

**10.5 Customer assessments.** On reasonable notice and no more than once a year (or after a confirmed security incident), Customer may review Company's security documentation and may ask for a summary of any independent security assessment Company holds, under confidentiality.

> **Counsel note:** CERT-In requires incident reporting within six hours of noticing a covered incident and retention of logs for 180 days in India. Reconcile that with any "24-hour purge" promise by separating customer content (purged) from operational logs (retained).

## 11. Service levels

From the Go-Live Date, Company will meet the service levels in **Exhibit A**. There are no service levels during a Pilot Phase.

## 12. Warranties and disclaimers

**12.1** Each Party warrants that it has authority to enter into this Agreement.

**12.2** Company warrants that during the term (a) it will provide the Service with reasonable skill and care, and (b) the Service will, in all material respects, perform as the Documentation describes. For any breach of this warranty Customer's sole remedy is that Company will use reasonable efforts to correct the Service or, failing that, Customer may terminate the affected Order Form and receive a refund of prepaid fees for the period after termination.

**12.3** Customer warrants that it has the rights needed to provide Customer Content and give the access Company needs, and that its use will comply with law.

**12.4** **Except as expressly stated, the Service, Third-Party Content and all outputs are provided "as is" and "as available". Company disclaims all other warranties, express or implied, including merchantability, fitness for a particular purpose, non-infringement, and any warranty that the Service will be uninterrupted or error-free, that outputs will be correct, or that any particular saving will be achieved.**

> **Counsel note:** A savings expectation is not warranted here by design. If the Customer needs a minimum-savings commitment, it must be a separate, measured term in the Order Form.

## 13. Indemnities

**13.1 By Company.** Company will defend Customer against a claim by a third party alleging that the Service, as provided by Company and used under this Agreement, infringes that third party's intellectual property rights, and will pay damages and costs finally awarded or agreed in settlement. Company has no obligation to the extent the claim arises from Customer Content, a Connected Model or Customer-supplied key, Third-Party Content, a combination with items Company did not supply, use in breach of this Agreement, or a modification Company did not make. If the Service is, or may be, held to infringe, Company may modify it, procure the right to continue, or end the affected Service and refund prepaid fees for the unused period.

**13.2 By Customer.** Customer will defend Company against a claim by a third party arising from (a) Customer Content, including an allegation that it infringes rights or that Customer lacked the right to provide it, or (b) Customer's use of the Service in breach of Section 4 or Exhibit B, and will pay damages and costs finally awarded or agreed in settlement.

**13.3 Procedure.** The indemnified Party will give prompt written notice (late notice relieves the indemnifying Party only to the extent it is prejudiced), let the indemnifying Party control the defence and settlement (not settling in a way that admits fault or imposes obligations on the indemnified Party without its consent), and give reasonable help at the indemnifying Party's cost.

**13.4** This Section states each Party's entire liability for third-party intellectual property claims.

## 14. Exclusion of damages

**14.1** To the extent the law allows, neither Party is liable under or in connection with this Agreement for **indirect, incidental, special, consequential or punitive damages**, or for loss of profit, revenue, goodwill or anticipated savings, even if advised of the possibility, whatever the legal theory.

## 15. Limitation of liability

**15.1 Pilot Phase.** During a Pilot Phase, each Party's total aggregate liability arising out of or in connection with this Agreement and the pilot is limited to **[₹0 / a nominal amount of ₹[●]]**.

**15.2 Paid phase.** From the Go-Live Date, each Party's total aggregate liability arising out of or in connection with this Agreement in any claim period is limited to the **fees paid and payable by Customer under the relevant Order Form in the three (3) months before the event giving rise to the claim**.

**15.3 Exceptions.** Sections 14 and 15 do not limit liability for: (a) fraud or fraudulent misrepresentation; (b) wilful misconduct or gross negligence; (c) death or bodily injury caused by negligence; (d) any liability that cannot be limited or excluded by law; and (e) Customer's obligation to pay fees due. **[Optional: (f) breach of Section 9 (Confidentiality) and Company's data-protection breach obligations, which are subject instead to a separate cap of [●] times the annual fees; and (g) the indemnities in Section 13, subject to the same separate cap.]**

**15.4** Customer's payment obligations are not counted toward the cap in Section 15.2.

> **Counsel note (important):** A nil cap during the pilot and a three-month-fees cap afterward follow the founders' instruction. Expect customers to push back, because a nil cap leaves them with no remedy for a confidentiality or data breach involving their source code. Common compromises: a nominal pilot cap, and a separate, higher "super-cap" (option (f)/(g) above) for confidentiality, data breach and IP indemnity. Decide your fallback position before the first negotiation. Indian courts enforce reasonable liability limits between commercial parties, but a limit does not shield fraud or wilful misconduct.

## 16. Term, termination and exit

**16.1 Term.** This Agreement starts on the Effective Date and continues until all Order Forms have expired or ended, unless ended under this Section. Each Order Form states its own term and renewal.

**16.2 Termination for cause.** Either Party may terminate this Agreement or an Order Form by written notice if the other (a) commits a material breach and does not cure it within **[30]** days of written notice (or **[10]** days for non-payment), or (b) becomes insolvent, enters liquidation or insolvency proceedings, or ceases business.

**16.3 Termination for convenience.** After the Go-Live Date, either Party may terminate an Order Form for convenience on **[90]** days' written notice, with no refund of fees paid for the period before termination. A Pilot Phase may be ended under Section 3.2.

**16.4 Effects.** On termination or expiry: (a) Customer's access ends (except as needed for the exit steps below); (b) within **[30]** days after Customer's written request made within **[30]** days after termination, Company will make Customer Content that Company holds available for export in a standard format; (c) Company will then delete Customer Content and derived representations of it from its systems (and require its subprocessors to do so) within **[30]** days, and confirm deletion in writing on request; and (d) Company may keep copies that law requires it to retain and routine backups until they expire, subject to Section 9. Customer will pay all fees accrued up to termination.

**16.5 Survival.** Sections 1, 4.2, 5.1, 5.5, 7 (as to accrued amounts), 8, 9, 12.4, 13 to 15, 16.4 to 16.5 and 18 to 19 survive.

## 17. Compliance

**17.1** Each Party will comply with laws applicable to its performance, including data-protection, export-control and sanctions laws.

**17.2** Neither Party will offer or accept any bribe or improper payment in connection with this Agreement and each will comply with applicable anti-corruption laws, including the Prevention of Corruption Act, 1988.

**17.3** **Insurance.** Company will maintain commercially reasonable insurance, including **[cyber and professional indemnity cover of at least [●]]**, from the Go-Live Date and give a certificate on request.

## 18. Governing law and dispute resolution

**18.1** This Agreement is governed by the laws of **India**, without regard to conflict-of-laws rules.

**18.2** The Parties will first try to resolve a dispute by good-faith discussion between senior representatives for **[30]** days after written notice.

**18.3** A dispute not so resolved shall be finally settled by **arbitration** under the Arbitration and Conciliation Act, 1996 by a **sole arbitrator** appointed by agreement (failing which by the court of competent jurisdiction on application). The **seat and venue** is **[Bengaluru / Mumbai / New Delhi]** and the language is English. The award is final and binding. Each Party bears its own costs unless the arbitrator orders otherwise.

**18.4** Either Party may apply to the courts at the seat for urgent interim relief, including to protect Confidential Information or intellectual property, without waiving arbitration.

## 19. General

- **Force majeure.** Neither Party is liable for delay or failure (other than payment) caused by events beyond its reasonable control, including natural disaster, war, government action, labour disputes, a failure of public networks or of cloud or model providers it does not control, or widespread internet failure, provided it gives prompt notice and uses reasonable efforts to resume. If a force majeure event lasts more than **[30]** days, the other Party may terminate the affected Order Form.
- **Assignment.** Neither Party may assign this Agreement without the other's prior written consent (not unreasonably withheld), except to an Affiliate or to a successor of all or substantially all of its business who agrees in writing to be bound, with notice to the other Party.
- **Notices.** In writing to the addresses above or to contacts named in the Order Form, by courier or by email with confirmation of sending. Operational and security notices may be sent to designated contacts.
- **Publicity.** Neither Party may use the other's name, logo or trademarks, or announce the relationship, without prior written consent.
- **Entire agreement.** This Agreement, its Order Forms, the DPA and Exhibits are the entire agreement on their subject matter and replace earlier proposals and discussions about it. Neither Party relies on a statement not set out in them.
- **Amendment and waiver.** Amendments must be in writing and signed by both Parties. A failure or delay in enforcing a right is not a waiver.
- **Severability.** An unenforceable provision is limited to the minimum extent necessary, and the rest continues.
- **No third-party rights.** Only the Parties may enforce this Agreement.
- **Relationship.** The Parties are independent contractors. There is no partnership, agency, joint venture or employment relationship, and no exclusivity.
- **Counterparts and electronic signature.** This Agreement may be signed in counterparts and electronically, with the same effect as original signatures under the Information Technology Act, 2000.
- **Stamp duty.** **[Company]** bears the stamp duty on this Agreement and will have it stamped under the law of the applicable state.

## Signatures

| | **[COMPANY LEGAL NAME]** | **[CUSTOMER LEGAL NAME]** |
|---|---|---|
| Signature | | |
| Name | | |
| Title | | |
| Date | | |

---

# EXHIBIT A: SERVICE LEVELS

*Applies from the Go-Live Date. Does not apply to a Pilot Phase.*

**A1. Availability commitment.** Company will make the Service's production MCP endpoint available at least **99.9%** of the time in each calendar month (**Monthly Availability**).

**A2. How it is measured.** Monthly Availability = (total minutes in the month minus Downtime minutes) ÷ total minutes, measured by Company's external health probes from at least **[two]** locations, at intervals of at most **[one minute]**. **Downtime** means a period in which the endpoint fails the health probe for **[three]** consecutive probes, or returns server errors (HTTP 5xx) to **[more than 5%]** of valid requests over **[five]** consecutive minutes.

**A3. Exclusions.** Downtime does not include time attributable to: (a) scheduled maintenance of up to **[eight]** hours per month announced at least **[48]** hours in advance; (b) force majeure; (c) Customer's systems, network, credentials, BYOK keys or configuration, or Customer's breach of this Agreement; (d) the unavailability, rate limits, errors or output of a Connected Model or other third-party service not operated by Company; (e) features Company labels beta, preview or pilot; (f) requests refused by policy (for example denied for missing authorisation, exceeding a budget, or failing a data-class check); or (g) a suspension permitted by this Agreement.

**A4. Service credits.** If Monthly Availability is below 99.9%, Customer may claim a credit against the next invoice for the affected monthly fee as follows:

| Monthly Availability | Credit |
|---|---|
| below 99.9% but at least 99.0% | **[5]%** |
| below 99.0% but at least 95.0% | **[15]%** |
| below 95.0% | **[30]%** |

**A5. Claims and sole remedy.** Customer must claim a credit in writing within **[30]** days after the end of the month, with reasonable detail. Credits do not exceed **[30]%** of that month's fee. **Credits are Customer's sole and exclusive remedy for failing to meet this Exhibit**, except the right to terminate under Section 16.2 if Monthly Availability is below 99.0% in **[three]** months out of any **[six]**.

**A6. Reports.** Company will make Monthly Availability figures available to Customer on request.

> **Counsel note:** Do not sign this Exhibit until the service is deployed with more than one instance, health probes and a status page. A promise of 99.9% from a single process is a promise you cannot reliably keep.

---

# EXHIBIT B: ACCEPTABLE USE

Customer and its Authorised Users will not use the Service to:

1. violate any law or third-party right, or submit content they have no right to submit;
2. submit malware, or content designed to damage, disrupt or gain unauthorised access to systems, or to hide a harmful action from the user of an AI agent;
3. attempt to access another customer's data or to circumvent isolation, authentication, rate limits, content screening or usage limits;
4. contribute to any shared library content that contains links, hidden or invisible characters, instructions aimed at an AI agent's own instructions, credentials, personal data, or content that is malicious, sexually explicit, hateful or that promotes self-harm;
5. probe, scan or test the Service's security without Company's prior written consent (except under a coordinated vulnerability-disclosure policy Company publishes);
6. send traffic that unreasonably burdens the Service, or use automated means to exceed documented limits; or
7. use the Service for any purpose that Company's model providers prohibit, where Company has told Customer of the restriction.

Company may remove content and suspend access that violates this Exhibit, as Section 7.6 permits.

---

# EXHIBIT C: DATA PROCESSING AGREEMENT

*Attach the Parties' signed DPA here. It must define: roles (Data Fiduciary and Data Processor), processing instructions and purposes, categories of data and data principals, the list of subprocessors, the regions of processing, cross-border transfer terms, retention and deletion schedules (customer content, operational logs, billing evidence), security measures, breach notification, assistance with data principals' requests, audits, and return or deletion at the end. Draft separately.*

---

# EXHIBIT D: ORDER FORM (TEMPLATE FIELDS)

| Field | Content |
|---|---|
| Order Form no. and date | **[●]** |
| Services and plan | **[●]** |
| Pilot Phase: start, end, scope, success criteria | **[●]** |
| Go-Live Date | **[●]** |
| Term and renewal | **[●]** |
| Authorised User / seat / usage limits | **[●]** |
| Fixed fees and invoicing schedule | **[●]** |
| Performance Fee: rate, baseline method, control or holdout, floor, cap, true-up, audit | **[●] (attach Measurement Schedule)** |
| Hosting regions and subprocessors | **See DPA** |
| Service levels | **Exhibit A / none during Pilot Phase** |
| Customer security and data-protection contacts | **[●]** |
| Special terms | **[●]** |
