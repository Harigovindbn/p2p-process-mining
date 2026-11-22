# Case study: finding USD 460K a year in a purchasing process

*Procure-to-pay process mining · business analysis portfolio project · synthetic data*

## The problem

A mid-sized company buys about 8,000 indirect goods and services a year: laptops, agency work, cleaning, training. The purchasing policy is clear on paper. Every request is approved by the right people for its amount, a purchase order (PO) is raised, goods are received, and the invoice is paid once it matches the PO.

Nobody could say how closely real purchases followed that policy. Procurement saw approvals and POs, Accounts Payable saw invoices and payments, and Finance saw the bank account. Each team's numbers looked fine on their own.

**The question I set out to answer:** how does purchasing really run, where does it lose time and money, and what should change first?

## Approach

1. **Requirements first.** I wrote a business requirements document before touching data. It sets out stakeholders and a RACI, the target process, seven KPIs, six control rules (C1–C6), user stories with acceptance criteria, and a UAT plan.
2. **Event log.** Every ERP step leaves a record of what happened, to which purchase, when, and by whom. Real logs are confidential, so I generated a realistic one: 8,108 purchases and 68,778 events across 2025, with office hours, weekends, Thursday payment runs, rework, and problems planted at known rates.
3. **Process discovery.** I grouped events by purchase to see every path purchases actually took (42 distinct variants), then built a process map of who hands work to whom and how long each hand-off takes.
4. **Bottlenecks.** I ranked each hand-off by total waiting time and compared every approver only with peers at the same approval level, so nobody is judged against a different kind of work.
5. **Control checks.** I tested all 8,108 purchases against six rules: missing approval, PO raised after the invoice, split purchases, approving your own request, payment before goods arrive, and duplicate payment. Each flag keeps the events that triggered it, so Audit can check it by hand.
6. **Testing.** 13 unit tests cover every rule on small hand-made cases. The checks also found every planted problem with no false alarms. That's expected, because the test data follows the same rule definitions, so it proves the checks are built correctly, not that they're perfect on real data.

## Results

| KPI | Result | Target |
| --- | --- | --- |
| Median cycle time, request to payment | 38.2 days | ≤ 30 days |
| Purchases on the standard path | 74.2% | ≥ 70% |
| Invoices paid on time | 76.4% | ≥ 95% |
| Early-payment discounts captured | 18.7% | ≥ 90% |
| Purchases breaking a control rule | 11.2% | ≤ 2% |

### Cash that leaked

- **USD 231K of early-payment discounts missed.** 2,849 invoices offered 2% off for paying within 10 days. Accounts Payable pays on Thursdays only and pays nearly everything close to day 30, whatever the terms.
- **USD 231K paid twice.** 47 supplier invoices went into two payment runs, and nothing stopped the second one.

Together that's **about USD 460K a year**, with no change to what the company buys.

### Where time went

Three approvers took 48–72 hours against a peer median of 24–26 hours. About 19% of purchases waited on them.

The longest wait overall, from invoice matched to payment (median 23 days), was **not** the problem it first appeared to be. It is mostly the company deliberately holding cash until the due date. Cutting it would hurt cash flow. The real issue is that the same holding pattern is applied to invoices where paying early earns a discount.

### Where controls broke

Violations were concentrated, not random:

| Problem | Department | Rate | Company average |
| --- | --- | --- | --- |
| PO raised after the invoice | Marketing | 8.1% | 3.7% |
| Split purchases under the USD 5K limit | Facilities | 5.2% | 2.2% |
| Paid before goods received | Operations | 5.4% | 2.7% |

## Root causes

- **Payment timing:** one payment run a week and one default rule, "pay near the due date". The ERP holds the discount terms, but the payment run ignores them.
- **Duplicate payments:** no check on supplier plus invoice number at payment time.
- **Marketing after-the-fact POs:** events and agency work are often committed by phone before a PO exists. The process has no fast route for urgent spend.
- **Facilities split purchases:** the USD 5K department-head threshold sits right where routine maintenance jobs fall, so splitting avoids a 1–2 day approval wait.
- **Operations paying before goods arrive:** the three-way match warning can be overridden, and in Operations it often is.

## Recommendations

| # | Recommendation | Owner | Value |
| --- | --- | --- | --- |
| 1 | Second weekly payment run; pay 2/10 invoices by discount deadline, not due date | AP / Treasury | Up to USD 231K a year |
| 2 | Block payment when supplier + invoice number has already been paid | AP / ERP team | USD 231K a year |
| 3 | 48-hour approval SLA with auto-escalation and absence delegation | Head of Procurement | Faster approvals for 19% of purchases |
| 4 | Blanket POs for Marketing events and recurring agency spend | Marketing + Procurement | 132 purchases a year brought under control |
| 5 | Review the USD 5K threshold; catalogue buying for routine Facilities spend | Procurement | Removes the reason for 68 split purchases |
| 6 | Make the three-way match a hard block, with exceptions approved by Finance | Finance / ERP team | Stops ~96 early payments a year in Operations |

Recommendations 1 and 2 are low-effort configuration changes with clear cash value, so they should go first. Recommendations 4 and 5 need agreement with the departments involved, which is where stakeholder work matters more than analysis.

## What I'd do with real data

- **Agree exceptions before reporting.** Emergency purchases or framework contracts may legitimately break a rule. Without an agreed exception list, the violation rate would be overstated.
- **Review people-level findings privately first.** An approver may be slow because requests reach them incomplete. Wait-time rankings should go to department heads before they go in any report.
- **Validate a sample by hand.** Have Internal Audit check 10–20 flags against the ERP to confirm the rules match how the business actually works.
- **Monitor, don't just report.** Phase 2 would run the checks weekly so problems are caught before payment, not found a year later.

## Limitations

The data is synthetic, so the patterns were designed in. The figures show what the method finds, not what any real company would find. Real event logs also bring problems this project avoids: missing timestamps, activities recorded in batches, and one purchase spread across several systems.

---

*Code, data, requirements document and full findings: see the [README](../README.md).*
