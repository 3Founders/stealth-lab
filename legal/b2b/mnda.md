> **DRAFT TEMPLATE. Not legal advice.** Prepared for review by Indian counsel before use. Text in `[BRACKETS]` is a blank to fill or a choice to make. Blockquotes marked **Counsel note** explain a choice and are deleted before sending.

# MUTUAL NON-DISCLOSURE AGREEMENT

This Mutual Non-Disclosure Agreement (the **Agreement**) is made on **[DATE]** (the **Effective Date**) between:

1. **[OPTION A, after incorporation: [COMPANY LEGAL NAME], a company incorporated under the Companies Act, 2013 (CIN [●]) / a limited liability partnership under the Limited Liability Partnership Act, 2008 (LLPIN [●])]** **[OPTION B, before incorporation: [FOUNDER FULL NAME], of [ADDRESS], acting for himself or herself and as promoter of [PROPOSED COMPANY NAME], a [private limited company / LLP] to be incorporated]**, with its registered office or address at **[ADDRESS]** (**Company**); and
2. **[COUNTERPARTY LEGAL NAME]**, **[a company incorporated under the laws of [●] (registration no. [●])]**, with its registered office at **[ADDRESS]** (**Counterparty**).

Each is a **Party** and together the **Parties**. A Party that discloses Confidential Information is the **Discloser** and a Party that receives it is the **Recipient**.

## 1. Purpose

The Parties wish to evaluate, negotiate and, if they agree, carry out a commercial relationship under which Company provides a software service that helps AI coding agents find proven procedures and choose cost-effective models (the **Purpose**). Each Party will disclose Confidential Information to the other only for the Purpose.

## 2. Confidential Information

**2.1** **Confidential Information** means all information, in any form, that the Discloser (or its Affiliates or representatives) discloses to the Recipient in connection with the Purpose and that is marked confidential or that a reasonable person would understand to be confidential from its nature or the circumstances of disclosure. It includes:

- (a) **Source Code**: source code, object code, scripts, configuration, infrastructure definitions, repository contents, commit history and pull requests, in any repository, staging or development environment;
- (b) **technical information**: database schemas, architecture, data models, abstract syntax tree (AST) data and hashes, procedure graphs, indexes, algorithms, prompts, routing and ranking logic, model-selection logic, and security measures;
- (c) **operational and business information**: issue-tracker contents (for example Jira or Linear tickets), internal performance metrics, usage, token consumption and cost data, savings calculations, pricing, product plans, customer and supplier information; and
- (d) the **existence and terms** of the Parties' discussions, and any analysis or notes made by the Recipient that contain or reflect the above.

**2.2** Confidential Information does not include information that the Recipient can show by written records: (a) is or becomes public through no breach of this Agreement; (b) was lawfully known to it, free of any duty of confidence, before disclosure; (c) was independently developed by it without use of or reference to the Confidential Information; or (d) was lawfully received from a third party who was free to disclose it. Information is not within these exceptions merely because individual elements of it are public.

## 3. Recipient's obligations

The Recipient shall:

- (a) use Confidential Information **only for the Purpose**;
- (b) keep it confidential and protect it with at least the care it uses for its own information of similar sensitivity, and not less than **reasonable care**;
- (c) disclose it only to its and its Affiliates' directors, officers, employees, contractors and professional advisers (**Representatives**) who need to know it for the Purpose and who are bound by written or professional duties of confidentiality at least as protective as this Agreement;
- (d) not copy it except as needed for the Purpose, and keep any copies marked or identifiable as Confidential Information;
- (e) not reverse engineer, decompile or attempt to derive source code, models or trade secrets from anything the Discloser provides; and
- (f) promptly notify the Discloser of any actual or suspected unauthorised access, use or disclosure, and cooperate to contain it.

Each Party is responsible for any breach of this Agreement by its Representatives.

## 4. Source Code and environment access

**4.1** Where the Discloser gives the Recipient access to Source Code, staging environments or tickets, the Recipient shall: (a) access them only through the means and accounts the Discloser provides; (b) keep access credentials secret and not share them; (c) not store Source Code outside the Discloser's environment, or outside the secure environment agreed in writing, except for the transient processing the Purpose requires; and (d) not use Source Code or any content of those environments to **train, fine-tune or evaluate any machine-learning model** other than for performing the Purpose for the Discloser.

**4.2** The Discloser may monitor, restrict and revoke that access at any time. The Recipient will comply with the Discloser's reasonable written security rules notified to it.

## 5. Permitted subprocessors (Company only)

**5.1** Counterparty agrees that Company may share Counterparty's Confidential Information, solely as needed for the Purpose, with the service providers listed in **Schedule 1** (**Permitted Subprocessors**), provided each is bound by written confidentiality and security obligations and, for any provider of machine-learning models, Company uses the provider's zero-retention or no-training setting where one is available.

**5.2** Company will give Counterparty at least **[10]** days' notice before adding a Permitted Subprocessor that will receive Source Code, and Counterparty may object in writing on reasonable grounds, in which case Company will not send it Source Code.

> **Counsel note:** Without this clause, sending a prompt or code snippet to a model host could itself breach the NDA. Check each listed provider's current terms on retention and training before signing (BLOCKERS P9).

## 6. Compelled disclosure

If the Recipient is required by law, court order or a regulator to disclose Confidential Information, it shall (to the extent lawful) give the Discloser prompt written notice, cooperate with any effort to obtain protection, and disclose only the part legally required.

## 7. Personal data

The Parties do not intend to share personal data under this Agreement. If personal data will be processed, the Parties will first enter into a data processing agreement that complies with the Digital Personal Data Protection Act, 2023 and its rules. Nothing in this Agreement authorises either Party to process personal data of the other's personnel or customers beyond what is incidental and unavoidable.

## 8. Ownership, no licence, no warranty

**8.1** All Confidential Information remains the Discloser's property. No licence or right is granted by this Agreement except the limited right to use it for the Purpose.

**8.2** Confidential Information is provided **"as is"** without warranty of accuracy or completeness, except as the Parties later agree in a definitive agreement.

**8.3** Neither Party is obliged to disclose any information or to enter any further agreement. A definitive agreement binds the Parties only when signed.

**8.4** **Feedback.** If the Counterparty gives Company suggestions about the Company's service, Company may use them without restriction, provided they do not include the Counterparty's Confidential Information.

**8.5** **Aggregated data.** Company may compute and use aggregated, de-identified statistics about how its service performs, provided they contain no Confidential Information and cannot be used to identify the Counterparty or any individual.

> **Counsel note (residuals):** This draft **omits** a "residual knowledge" clause (use of information retained in unaided memory). Because the Company will see customer source code, a residuals clause would be a red flag for the counterparty.

## 9. Term and survival

**9.1** This Agreement covers disclosures made during the **[two (2)]** years after the Effective Date, unless ended earlier by either Party on **[30]** days' written notice.

**9.2** Each Recipient's duties continue for **[five (5)]** years after the end of that period. For Source Code and any information that qualifies as a trade secret, the duties continue for as long as the information remains confidential or a trade secret.

## 10. Return and deletion

Within **[30]** days after the Discloser's written request or the end of this Agreement, the Recipient shall return or securely delete the Discloser's Confidential Information and confirm in writing that it has done so. The Recipient may keep (a) copies it must retain by law or regulation and (b) copies in routine backups until they expire in the ordinary course, in each case under this Agreement's duties and not used for any other purpose.

## 11. Remedies

Unauthorised use or disclosure may cause harm for which damages are not an adequate remedy, so the Discloser may seek **injunctive or other equitable relief** without proving actual damage or posting a bond, in addition to any other remedy.

## 12. Governing law and disputes

**12.1** This Agreement is governed by the **laws of India**.

**12.2** Any dispute shall be finally settled by **arbitration** under the Arbitration and Conciliation Act, 1996 by a **sole arbitrator** appointed by mutual agreement (or, failing agreement within 30 days, appointed on application to the court of competent jurisdiction). The **seat and venue** is **[Bengaluru / Mumbai / New Delhi]**. The language is English.

**12.3** Either Party may apply to the courts at the seat for urgent interim relief to protect Confidential Information, and that application is not a waiver of arbitration.

> **Counsel note:** Arbitration is usual for B2B contracts but slower to start than a court application, which is why interim relief is carved out. For a foreign counterparty, decide in advance whether you will accept foreign law or a neutral institutional seat (for example SIAC).

## 13. General

- **Entire agreement.** This is the Parties' entire agreement on its subject matter and replaces earlier discussions on it. A later written definitive agreement may replace this Agreement for information disclosed after its date, as that agreement states.
- **Amendment and waiver.** Changes must be in writing and signed by both Parties. Delay in exercising a right is not a waiver.
- **Assignment.** Neither Party may assign this Agreement without the other's written consent, except to a successor of all or substantially all of its business who agrees in writing to be bound.
- **Incorporation and novation (use only with Option B).** The Counterparty knows that the Company is a promoter acting before incorporation. When the proposed entity is incorporated, the Company will give the Counterparty written notice, and from the date of a short novation notice signed by the entity (**Novation Date**) the entity takes over every right and duty of the Company under this Agreement, including for information already disclosed. The Counterparty consents in advance to that substitution. From the Novation Date the individual signatory is released from future obligations, but not from any breach before it. If the entity is not incorporated within **[90]** days of the Effective Date, either Party may end this Agreement by written notice and sections 3, 4 and 10 continue to apply to information already disclosed.

> **Counsel note (pre-incorporation):** A company or LLP that does not exist cannot sign. A person who signs "for a company to be formed" is usually personally liable, and the new entity is not bound by the contract merely because it was made for it. Indian law lets a company adopt a pre-incorporation contract only in limited cases (Specific Relief Act, 1963, ss. 15(h) and 19(e)), and the LLP Act, 2008 has its own rule on contracts made before incorporation. Counsel should confirm which applies, so the cleaner route is the advance consent and novation above, not adoption by silence.
- **No publicity.** Neither Party may announce the discussions or use the other's name or logo without written consent.
- **Notices.** In writing to the addresses above (or as notified), and by email with confirmation of sending.
- **Severability.** If a provision is unenforceable, the rest remains in effect and the provision is limited to the minimum extent needed.
- **Relationship.** The Parties are independent contractors. There is no partnership, agency or exclusivity.
- **Counterparts and electronic signature.** This Agreement may be signed in counterparts and electronically, which has the same effect as an original signature under the Information Technology Act, 2000.
- **Stamp duty.** **[Company]** shall bear the stamp duty payable on this Agreement and shall have it stamped in accordance with the law of the applicable state before or promptly after signing.

> **Counsel note:** Confirm the stamp-duty amount and e-stamping route for the state where you sign. An unstamped agreement can be hard to enforce.

## Signatures

| | **[COMPANY LEGAL NAME]** | **[COUNTERPARTY LEGAL NAME]** |
|---|---|---|
| Signature | | |
| Name | | |
| Title | | |
| Date | | |

---

## Schedule 1: Permitted Subprocessors

| Provider | Service | Location of processing | Retention / training settings |
|---|---|---|---|
| **[Cloud hosting provider]** | Application hosting | **[region]** | **[●]** |
| **[Database provider]** | Database storage | **[region]** | **[●]** |
| **[Object storage provider]** | Object storage | **[region]** | **[●]** |
| **[Model provider(s), for example Google Vertex AI / model host]** | Model inference (only where the Purpose needs it) | **[region]** | **[zero-retention / no-training: yes/no, with reference to terms]** |
| **[Authentication provider]** | User sign-in | **[region]** | **[●]** |
| **[Error-monitoring provider]** | Error reports (no Source Code) | **[region]** | **[●]** |

> **Counsel note:** Fill the *region* column from what is actually deployed. Today the database and some model calls run outside India, so do not promise India-only processing here.
