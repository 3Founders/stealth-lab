> **DRAFT TEMPLATE. Not legal advice.** For review by Indian counsel. `[BRACKETS]` are blanks or choices. **Counsel note** blocks are deleted before sending.
>
> **Use:** signed under the Master Services Agreement, after the company exists. It sets price, term and the savings-based fee. The fee formula is in Schedule 1. **Do not offer the percentage-of-savings fee until a pilot has produced a measured baseline** (see `legal/README.md`).

# ORDER FORM No. [●]

Issued under the Master Services Agreement dated **[DATE]** between **[COMPANY LEGAL NAME]** (**Company**) and **[CUSTOMER LEGAL NAME]** (**Customer**) (the **MSA**). Terms defined in the MSA have the same meaning here. If this Order Form and the MSA conflict, this Order Form controls only for the items it lists.

## 1. Order details

| Item | Value |
|---|---|
| Order Form date | **[DATE]** |
| Phase | **[Pilot Phase / Production]** |
| Pilot Period (if Pilot Phase) | **[start]** to **[end]**, shadow mode |
| Go-Live Date (starts service levels) | **[DATE / not applicable]** |
| Initial term | **[12]** months from the Go-Live Date, then renewing for **[12]**-month periods unless either party gives **[60]** days' notice |
| Authorised Users | up to **[●]** named users, adjustable by written notice |
| Services | Hosted MCP service: `find_ways`, `recommend_models`, `report_model_run`, `report_result`, `report_discovery`, `submit_way`; and `call_model` **[included / not included]** |
| `call_model` mode | **[Customer's own keys (BYOK) / platform-supplied models]** |
| Support | **[email, response times, hours]** |
| Service levels | MSA Exhibit A applies from the Go-Live Date |
| Customer contact / security contact | **[names, emails]** |
| Data processing | The Data Processing Agreement dated **[DATE]** applies. Locations and subprocessors are in its schedules |

## 2. Fees

| Fee | Amount | When |
|---|---|---|
| Platform fee | **₹[●] per [user / month]**, or **[none during the Pilot Period]** | **[monthly / annually]** in advance |
| Performance Fee | **[rate]%** of Net Saving, under Schedule 1 | monthly in arrears, after the Calculation Statement |
| Platform-supplied model usage (only if `call_model` is platform-supplied) | At the provider's list price **[plus [●]%]**, shown as a separate line | monthly in arrears |

- Fees exclude GST, which Company adds at the rate in force.
- Payment within **[30]** days of invoice. Late amounts carry interest at **[1]%** per month.
- If the law requires Customer to deduct tax at source, Customer deducts only that amount, pays it to the government on time and gives Company the certificate.
- **[Cap: total Performance Fee in any month does not exceed ₹[●].]**

## 3. Special terms

**[None / list]**

## Signatures

| | **[COMPANY LEGAL NAME]** | **[CUSTOMER LEGAL NAME]** |
|---|---|---|
| Signature, name, title, date | | |

---

# SCHEDULE 1: MEASUREMENT SCHEDULE (Performance Fee)

> **Counsel note:** This is the part most likely to be disputed. The baseline must be measured in dollars that include cache reads and writes. Real Claude Code spend is mostly cache reads, so counting only fresh input and output tokens misstates savings. A control group stops a quiet month from looking like a saving. Counsel should check enforceability of a fee computed from data the customer controls, and the audit clause.

## 1. Definitions

- **Measurement Period:** each calendar month in the Production phase.
- **Eligible Task:** a task an Authorised User runs through a coding agent that has the Service connected, where the Service's `instance_key` or an equivalent identifier lets both groups' tasks be matched by type. Tasks of a kind named in Section 9 are excluded.
- **Treatment Group:** Eligible Tasks where the Service's model plan was available to the user.
- **Control Group:** a randomly chosen **[20]%** of Eligible Tasks (or of Authorised Users, whichever the parties agree in the Order Form) for which the Service gives procedures but **no model plan**, so the user chooses the model as before. The Service assigns the group using a published random method. Customer may check the assignment log.
- **Cost:** the money cost of a task to Customer, computed as the sum of **fresh input tokens × input price + cache-write tokens × cache-write price + cache-read tokens × cache-read price + output tokens × output price**, using the **Price Table**. Cache tokens are counted separately from fresh input, never added to it.
- **Price Table:** the per-model prices agreed at the start of the Production phase (Annexure A), applied to both groups for the whole Measurement Period. Changes to a provider's list price during the term do not count as a saving or a loss. The parties update the Price Table by written agreement.
- **Accepted:** a task result that passes the acceptance check named in Annexure A (for example tests pass, or the user accepts the change).
- **Acceptance Rate:** Accepted tasks divided by Eligible Tasks, for a group.

## 2. Net Saving

For each task type *k* with at least **[30]** tasks in each group in the Measurement Period:

```
mean_cost_control(k)   = total Cost of Control tasks of type k / number of Control tasks of type k
mean_cost_treatment(k) = total Cost of Treatment tasks of type k / number of Treatment tasks of type k
saving(k)              = (mean_cost_control(k) − mean_cost_treatment(k)) × number of Treatment tasks of type k
Gross Saving           = sum of saving(k) over eligible task types, but not below zero
Net Saving             = Gross Saving − platform-supplied model usage billed to Customer that month
                         − any Platform fee already paid for that month
```

## 3. Quality guard

The Performance Fee for a task type *k* is zero if the Treatment Acceptance Rate is lower than the Control Acceptance Rate by more than **[3]** percentage points (non-inferiority margin). Where fewer than **[30]** tasks exist, that type is left out of the calculation, not counted as a saving.

## 4. Performance Fee

```
Performance Fee = rate × Net Saving
```

subject to the monthly cap in the Order Form and to the floor in Section 5. The rate is **[●]%**.

## 5. Floor

No Performance Fee is due for a month in which Net Saving is below **₹[●]**, or in which the Treatment Group's Cost is not lower than the Control Group's at the **[95]%** confidence level using the method in Annexure A.

## 6. Worked example (numbers are illustrative)

For one task type, 100 Control tasks cost $100 in total (mean $1.00) and 400 Treatment tasks cost $240 (mean $0.60). The Acceptance Rate is 70% in both groups.

- saving = ($1.00 − $0.60) × 400 = **$160**
- Platform-supplied model usage billed and platform fee for the month: **$20**
- Net Saving = $160 − $20 = **$140**
- At a rate of 20%, Performance Fee = **$28**. Customer keeps $112 of the $140.

If the Treatment Acceptance Rate had been 65%, which is 5 points lower than the Control's 70% and beyond the 3-point margin, the fee for that type would be **zero**.

## 7. Data and calculation

- Customer provides, or lets Company read through the Service, per-task token counts, model and cost, and acceptance results. Customer may instead supply its provider billing export. Company does not need prompts, outputs or source code for this purpose.
- By the **[10th]** day after each Measurement Period Company sends a **Calculation Statement** with the inputs, group sizes, method and result.
- Customer has **[15]** days to dispute it in writing and say which input it disputes. Undisputed amounts are payable on time.

## 8. Audit and disputes

Customer may, once per quarter and on **[10]** days' notice, have an independent accountant bound by confidentiality re-run the calculation from the same data. If it finds Company overstated the fee by more than **[5]%**, Company bears the audit cost and credits the difference. Unresolved disputes go first to senior representatives for **[15]** days, then to the MSA's dispute clause.

## 9. Exclusions

Tasks where the user overrode the Service's model plan by explicit choice, tasks that failed for reasons outside the Service (for example an outage of the customer's own provider), tasks below the minimum size, and the first **[7]** days after any change in the Price Table.

## 10. No guarantee

Company does not guarantee any saving. If the Net Saving is zero, so is the Performance Fee.

## Annexure A: agreed parameters

| Parameter | Agreed value |
|---|---|
| Price Table | **[per-model prices: fresh input, cache write, cache read, output]** |
| Task types | **[list or the Service's goal categories]** |
| Acceptance check | **[tests / user acceptance / other]** |
| Control share and unit | **[20% of tasks / users]** |
| Minimum tasks per type | **[30]** |
| Non-inferiority margin | **[3]** points |
| Confidence level and method | **[95%, difference of means with bootstrap / other]** |
| Rate, floor, cap | **[●]** |
