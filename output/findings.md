# Findings: Procure-to-Pay Process Mining

Based on 8,108 purchases and 68,778 events in the synthetic 2025 event log.

## Headline numbers

| KPI | Result | Target |
| --- | --- | --- |
| Median end-to-end cycle time | 38.2 days (90th pct 49.2) | ≤ 30 days |
| Median requisition-to-PO time | 2.0 days (90th pct 5.0) | – |
| Happy-path rate | 74.2% | ≥ 70% |
| Rework rate (PO changed) | 9.2% | ≤ 10% |
| Invoice exception rate | 11.2% | – |
| On-time payment rate | 76.4% | ≥ 95% |
| Discount capture rate | 18.7% | ≥ 90% |
| Control violation rate | 11.2% of purchases | ≤ 2% |

## Cash impact

- **Missed early-payment discounts:** USD 231,197 (USD 285,221 was available on 2,849 invoices)
- **Duplicate payments:** USD 231,285 paid twice
- **Late payments:** 1,913 invoices paid after 30 days

## Bottlenecks

- Biggest total wait: **Three-Way Match → Pay Invoice**, median 557 h, 158,622 days of waiting across all cases.
- Median line-manager approval wait: 23 h.
- Approvers waiting 2× or more than peers at the same level (≥ 20 approvals):

    - U0025 (Department head, Marketing): median 72 h vs 26 h for peers
    - U0040 (Line manager, Operations): median 50 h vs 24 h for peers
    - U0013 (Line manager, IT): median 48 h vs 24 h for peers

## Control violations

| Rule | Purchases | Value (USD) |
| --- | --- | --- |
| C1 Missing approval | 79 | 450,663 |
| C2 After-the-fact PO | 302 | 1,502,515 |
| C3 Split purchase | 177 | 671,723 |
| C4 Segregation of duties | 90 | 428,051 |
| C5 Paid before goods receipt | 222 | 1,110,570 |
| C6 Duplicate payment | 47 | 231,285 |

## Departments

| Department | Purchases | Median cycle (days) | Happy path | Violation rate |
| --- | --- | --- | --- | --- |
| Marketing | 1,639 | 38.2 | 71% | 14.6% |
| Facilities | 1,320 | 38.2 | 75% | 13.0% |
| Operations | 1,786 | 38.2 | 74% | 12.8% |
| HR | 771 | 37.3 | 74% | 9.6% |
| IT | 1,798 | 38.2 | 75% | 8.7% |
| Finance | 794 | 38.2 | 77% | 5.3% |

## Top process variants

| # | Variant | Cases | Share |
| --- | --- | --- | --- |
| 1 | Requisition > Approve (LM) > Create PO > Send PO > Goods receipt > Invoice > 3-way match > Pay | 4,363 | 53.8% |
| 2 | Requisition > Approve (LM) > Approve (Head) > Create PO > Send PO > Goods receipt > Invoice > 3-way match > Pay | 1,637 | 20.2% |
| 3 | Requisition > Approve (LM) > Create PO > Send PO > Goods receipt > Invoice > 3-way match > Clear exception > Pay | 544 | 6.7% |
| 4 | Requisition > Approve (LM) > Create PO > Change PO > Send PO > Goods receipt > Invoice > 3-way match > Pay | 439 | 5.4% |
| 5 | Requisition > Approve (LM) > Approve (Head) > Create PO > Send PO > Goods receipt > Invoice > 3-way match > Clear exception > Pay | 216 | 2.7% |

## Data quality

- No data-quality problems found

## Detection accuracy (test data)

| Rule | Planted | Flagged | Correct | Precision | Recall |
| --- | --- | --- | --- | --- | --- |
| C1 | 79 | 79 | 79 | 100.0% | 100.0% |
| C2 | 302 | 302 | 302 | 100.0% | 100.0% |
| C3 | 177 | 177 | 177 | 100.0% | 100.0% |
| C4 | 90 | 90 | 90 | 100.0% | 100.0% |
| C5 | 222 | 222 | 222 | 100.0% | 100.0% |
| C6 | 47 | 47 | 47 | 100.0% | 100.0% |
