> **DRAFT TEMPLATE. Not legal advice.** For review by Indian counsel. Text in `[BRACKETS]` is a blank or a choice. **Counsel note** blocks are deleted before sending.
>
> **Use:** a short, unpaid or low-fee, time-boxed trial signed **before the company exists**. It replaces the MSA for this stage. Sign the Mutual NDA (Option B) first. Move to the MSA after the company is incorporated and this agreement has been novated to it.

# PILOT AGREEMENT

Made on **[DATE]** (**Effective Date**) between:

1. **[FOUNDER FULL NAME]**, of **[ADDRESS]**, acting as promoter of **[PROPOSED COMPANY NAME]**, a **[private limited company / LLP]** to be incorporated (**Provider**); and
2. **[CUSTOMER LEGAL NAME]**, **[a company incorporated under [law] (registration no. [●])]**, of **[ADDRESS]** (**Customer**).

## 1. The pilot

**1.1** Provider will let up to **[number]** of Customer's engineers use its hosted MCP service (the **Service**) with their AI coding agents for **[30]** days from **[START DATE]** (the **Pilot Period**), to test whether it helps them find proven procedures and use cost-effective models.

**1.2** The Pilot runs in **shadow mode**: the Service gives suggestions and records what would have been chosen, and Customer's engineers decide whether to follow them. Nothing in Customer's systems depends on the Service.

**1.3** The Pilot has no service levels, no uptime promise and no support commitment, other than **[email]** answered within **[2]** business days.

## 2. What the Provider does not receive

**2.1** Provider will **not** be given access to Customer's source-code repositories, staging environments, production systems or customer personal data.

**2.2** Engineers use the Service from their own machines. The tool `find_ways` receives a task description. Engineers must not paste source code, secrets or personal data into a task description. **[Optional: Customer's security team may restrict the Service to named tools.]**

**2.3** The tool `call_model` is **off** unless Customer turns it on and supplies its own model-provider keys. If on, prompts go from the engineer's machine through the Service to the provider Customer chose. Provider stores token counts, cost and status, not the prompt or output.

## 3. What Provider stores

Provider stores, for each use: a hash of the task description, which procedures were returned, whether the engineer accepted the result, and token, cost and time figures. Provider does not store task text. Provider may use that usage data in aggregated form to improve routing, without naming Customer or any engineer.

## 4. Ownership and confidentiality

**4.1** Customer owns what it puts into the Service and what the Service returns to it for its work. Provider owns the Service, its routing logic and its procedure library.

**4.2** Each party keeps the other's non-public information confidential for **[3]** years. The Mutual NDA between them continues to apply. If this agreement and the NDA conflict, the NDA controls on confidentiality.

**4.3** Provider will not use Customer's information to train any machine-learning model. Provider asks its model providers to use zero-retention and no-training settings where they offer them. Provider does not promise what a third party does.

## 5. Fees

**[OPTION A: No fee during the Pilot Period.]**
**[OPTION B: Fixed fee of ₹[●] plus GST, payable on signing. Provider will invoice under [PAN/GST details]. Customer will deduct TDS only as the law requires and give the certificate.]**

No savings-based or performance fee applies. Any such fee needs a separate written Order Form with a measurement method.

## 6. Disclaimers and liability

**6.1** The Service is provided **as is** for evaluation. Provider does not promise any saving, any quality of result or any availability. Outputs are suggestions that Customer's engineers review before use.

**6.2** Customer is responsible for its engineers' use of the Service and for reviewing every suggestion before acting on it.

**6.3** Provider's total liability under this agreement is **[nil / ₹[nominal]]**. This does not limit liability for fraud, wilful misconduct, or anything that law does not allow to be limited. Neither party is liable for indirect or consequential loss, or loss of profit.

**6.4** No indemnity is given by either party, other than for a claim that Provider's Service itself infringes a third party's intellectual property, up to **[₹[●]]**. **[Delete if the founder will not accept it.]**

> **Counsel note:** Section 6 is the founder's main protection while signing personally. A nil or nominal cap and no indemnity make the pilot a low-risk personal exposure. Customers with procurement rules may insist on more, in which case wait for the company.

## 7. Term and ending

**7.1** The agreement ends when the Pilot Period ends, or earlier if either party gives **[7]** days' written notice. Either party may end it immediately for a material breach of confidentiality.

**7.2** On ending, Provider deletes data that identifies Customer or its engineers within **[30]** days on request, except records it must keep by law and backups until they expire. Procedure content that Customer chose to publish publicly stays public.

**7.3** The Pilot does not oblige either party to buy or sell anything further.

## 8. Incorporation and novation

**8.1** Customer knows that Provider is a promoter acting before incorporation. Provider will tell Customer in writing when the proposed entity is incorporated. On the date the entity signs a short novation notice (**Novation Date**), the entity takes over every right and duty of Provider under this agreement, and Customer agrees in advance to that substitution.

**8.2** From the Novation Date, **[FOUNDER FULL NAME]** is released from duties arising after that date, but not from a breach before it.

**8.3** If the entity is not incorporated within **[90]** days after the Effective Date, either party may end this agreement by notice. Sections 4, 6 and 7.2 continue.

**8.4** The parties expect, but do not promise, to replace this agreement with a Master Services Agreement after the Novation Date.

> **Counsel note:** This follows the Specific Relief Act, 1963, ss. 15(h) and 19(e), but relies on written advance consent and a signed novation, not on the entity's silent adoption. Counsel should confirm that the entity's objects cover the Service and, if the entity will be an LLP, the LLP Act, 2008 rule on pre-incorporation contracts.

## 9. General

- **Law and disputes.** Indian law. Disputes go to a sole arbitrator under the Arbitration and Conciliation Act, 1996, seat **[Bengaluru / Mumbai / New Delhi]**, English language. A party may seek urgent interim relief from the courts at the seat.
- **Entire agreement.** This agreement and the Mutual NDA are the whole agreement for the Pilot.
- **Changes.** In writing, signed by both parties.
- **Assignment.** Only by the novation above, or with the other party's written consent.
- **Electronic signature.** Allowed under the Information Technology Act, 2000.
- **Stamp duty.** **[Provider]** bears it and has the agreement stamped under the law of the applicable state.

## Signatures

| | **[FOUNDER FULL NAME], promoter of [PROPOSED COMPANY NAME]** | **[CUSTOMER LEGAL NAME]** |
|---|---|---|
| Signature | | |
| Name | | |
| Title | Promoter | |
| Date | | |
