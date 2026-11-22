"""Unit tests for the P2P control rules, using small hand-made event logs.

Run:  python -m pytest -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze import case_metrics, control_checks, expected_path, variants_and_paths  # noqa: E402

LM = "Approve Requisition (Line Manager)"
DH = "Approve Requisition (Dept Head)"
CFO = "Approve Requisition (CFO)"

HAPPY = ["Create Requisition", LM, "Create PO", "Send PO to Supplier", "Receive Goods",
         "Receive Invoice", "Three-Way Match", "Pay Invoice"]


def make_case(case_id, activities, amount=1000.0, requester="R1", supplier="S1",
              start="2025-03-03 09:00", step_hours=24, users=None, terms="net 30",
              dept="IT", invoice="INV-1"):
    """Build events + one case row. Each activity is `step_hours` after the previous one."""
    t0 = pd.Timestamp(start)
    users = users or {}
    rows = []
    for i, act in enumerate(activities):
        is_money = act in ("Receive Invoice", "Pay Invoice")
        rows.append({
            "case_id": case_id, "activity": act, "timestamp": t0 + pd.Timedelta(hours=step_hours * i),
            "user_id": users.get(act, requester if act == "Create Requisition" else f"U-{act[:6]}"),
            "role": "", "invoice_number": invoice if is_money else None,
            "amount_usd": amount if is_money else None,
        })
    case = {"case_id": case_id, "department": dept, "supplier_id": supplier, "category": "x",
            "po_amount_usd": amount, "payment_terms": terms, "requester_id": requester}
    return rows, case


def run(*built):
    ev = pd.DataFrame([r for rows, _ in built for r in rows]).sort_values(["case_id", "timestamp"])
    cases = pd.DataFrame([c for _, c in built])
    return ev.reset_index(drop=True), cases, control_checks(ev.reset_index(drop=True), cases)


def rules_for(flags, case_id):
    return set(flags.loc[flags.case_id == case_id, "rule"])


# ---------------------------------------------------------------- policy
def test_expected_path_thresholds():
    assert DH not in expected_path(4_999)
    assert DH in expected_path(5_000) and CFO not in expected_path(5_000)
    assert CFO in expected_path(50_001)


def test_clean_case_has_no_flags():
    _, _, flags = run(make_case("A", HAPPY))
    assert flags.empty


# ---------------------------------------------------------------- C1
def test_c1_missing_dept_head_approval():
    _, _, flags = run(make_case("A", HAPPY, amount=12_000))  # needs LM + DH, has LM only
    assert rules_for(flags, "A") == {"C1"}
    assert "Department head" in flags.iloc[0].evidence


def test_c1_approval_after_po_does_not_count():
    acts = ["Create Requisition", LM, "Create PO", DH, "Send PO to Supplier", "Receive Goods",
            "Receive Invoice", "Three-Way Match", "Pay Invoice"]
    _, _, flags = run(make_case("A", acts, amount=12_000))
    assert "C1" in rules_for(flags, "A")


# ---------------------------------------------------------------- C2
def test_c2_invoice_before_po():
    acts = ["Receive Goods", "Receive Invoice", "Create Requisition", LM, "Create PO",
            "Send PO to Supplier", "Three-Way Match", "Pay Invoice"]
    _, _, flags = run(make_case("A", acts))
    assert rules_for(flags, "A") == {"C2"}


# ---------------------------------------------------------------- C3
def test_c3_split_purchase_within_window():
    a = make_case("A", HAPPY, amount=3_000, start="2025-03-03 09:00")
    b = make_case("B", HAPPY, amount=3_000, start="2025-03-06 09:00")  # 3 days later, total 6,000
    _, _, flags = run(a, b)
    assert rules_for(flags, "A") == {"C3"} and rules_for(flags, "B") == {"C3"}


def test_c3_not_flagged_outside_window_or_under_threshold():
    far = (make_case("A", HAPPY, amount=3_000, start="2025-03-03 09:00"),
           make_case("B", HAPPY, amount=3_000, start="2025-03-20 09:00"))
    small = (make_case("C", HAPPY, amount=2_000, requester="R2", start="2025-03-03 09:00"),
             make_case("D", HAPPY, amount=2_000, requester="R2", start="2025-03-04 09:00"))
    _, _, flags = run(*far, *small)
    assert flags.empty


def test_c3_different_supplier_not_split():
    a = make_case("A", HAPPY, amount=3_000, supplier="S1")
    b = make_case("B", HAPPY, amount=3_000, supplier="S2", start="2025-03-04 09:00")
    _, _, flags = run(a, b)
    assert flags.empty


# ---------------------------------------------------------------- C4
def test_c4_requester_approves_own_request():
    _, _, flags = run(make_case("A", HAPPY, requester="U9", users={LM: "U9"}))
    assert rules_for(flags, "A") == {"C4"}


# ---------------------------------------------------------------- C5
def test_c5_paid_before_goods():
    acts = ["Create Requisition", LM, "Create PO", "Send PO to Supplier", "Receive Invoice",
            "Three-Way Match", "Pay Invoice", "Receive Goods"]
    _, _, flags = run(make_case("A", acts))
    assert rules_for(flags, "A") == {"C5"}


def test_c5_paid_with_no_goods_receipt():
    acts = [a for a in HAPPY if a != "Receive Goods"]
    _, _, flags = run(make_case("A", acts))
    assert "C5" in rules_for(flags, "A")


# ---------------------------------------------------------------- C6
def test_c6_duplicate_payment_value_is_extra_amount():
    _, _, flags = run(make_case("A", HAPPY + ["Pay Invoice"], amount=2_500))
    f = flags[flags.rule == "C6"]
    assert len(f) == 1 and f.iloc[0].value_usd == pytest.approx(2_500)


# ---------------------------------------------------------------- metrics
def test_happy_path_and_discount_capture():
    # invoice at step 5, payment at step 7 -> 2 days later with 24h steps: discount captured
    fast = make_case("A", HAPPY, amount=10_000, terms="2/10 net 30")
    slow = make_case("B", HAPPY, amount=10_000, terms="2/10 net 30", step_hours=24 * 8,
                     requester="R2")  # invoice to pay = 16 days: discount missed
    ev, cases, _ = run(fast, slow)
    _, happy = variants_and_paths(ev, cases)
    m = case_metrics(ev, cases, happy).set_index("case_id")
    assert bool(m.at["A", "discount_captured"]) and m.at["A", "discount_missed_usd"] == 0
    assert not bool(m.at["B", "discount_captured"]) and m.at["B", "discount_missed_usd"] == pytest.approx(200)
    assert not happy["A"]  # 10,000 needs a Dept Head approval, so this is not the happy path
