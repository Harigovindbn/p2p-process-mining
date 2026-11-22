"""
Procure-to-Pay Process Mining - synthetic event log generator.

Builds a realistic ERP event log for one year of indirect purchasing, with
process problems planted at known rates and recorded in an answer key.

Outputs (in ./data):
  events.csv           one row per process step (the event log)
  cases.csv            one row per purchase (case attributes)
  users.csv            user directory (IDs and roles only, no names)
  suppliers.csv        supplier master with payment terms
  planted_issues.csv   answer key: which cases break which control rule

Control rules planted (see BRD):
  C1 missing approval            C2 after-the-fact PO (maverick buying)
  C3 split purchase              C4 segregation of duties
  C5 payment before goods receipt  C6 duplicate payment

Also planted (measured, not rule violations): slow approvers, Thursday-only
payment runs that miss early-payment discounts, invoice exceptions, PO rework.

Usage:  python generate_event_log.py [--seed 7] [--cases 8000] [--out data]
"""

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

YEAR = 2025
DEPARTMENTS = {
    # name: (share of purchases, typical spend multiplier)
    "IT": (0.22, 1.6), "Marketing": (0.20, 1.0), "Operations": (0.22, 1.3),
    "HR": (0.10, 0.6), "Finance": (0.10, 0.7), "Facilities": (0.16, 1.2),
}
CATEGORIES = {
    "IT": ["IT hardware", "Software licences", "IT services"],
    "Marketing": ["Advertising", "Events", "Agency services"],
    "Operations": ["Logistics", "Equipment", "Maintenance"],
    "HR": ["Recruitment", "Training", "Benefits admin"],
    "Finance": ["Consulting", "Audit services", "Software licences"],
    "Facilities": ["Cleaning", "Office supplies", "Maintenance"],
}
THRESHOLD_DH = 5_000     # department head approval from this amount
THRESHOLD_CFO = 50_000   # CFO approval above this amount

# planted rates
P_C1 = 0.015     # missing approval
P_C2 = 0.030     # after-the-fact PO
N_C3_GROUPS = 45 # split-purchase groups (2-3 POs each)
P_C4 = 0.010     # segregation of duties
P_C5 = 0.020     # paid before goods receipt
P_C6 = 0.008     # duplicate payment
P_REWORK = 0.09  # PO changed after creation
P_EXCEPTION = 0.12  # three-way match fails, needs clearing
P_FAST_PAY_DISCOUNT = 0.35  # AP manages to pay 2/10 invoices by day 10
# department-level risk: some teams bypass specific controls more often
DEPT_RISK = {"Marketing": {"C2": 3.0}, "Operations": {"C5": 2.5}, "Finance": {"C1": 0.3, "C2": 0.3}}
SPLIT_DEPT_WEIGHTS = {"Facilities": 0.45, "Operations": 0.2, "IT": 0.15, "Marketing": 0.1, "HR": 0.05, "Finance": 0.05}

ACT = {
    "req": "Create Requisition",
    "lm": "Approve Requisition (Line Manager)",
    "dh": "Approve Requisition (Dept Head)",
    "cfo": "Approve Requisition (CFO)",
    "po": "Create PO",
    "chg": "Change PO",
    "send": "Send PO to Supplier",
    "gr": "Receive Goods",
    "inv": "Receive Invoice",
    "match": "Three-Way Match",
    "exc": "Clear Invoice Exception",
    "pay": "Pay Invoice",
}


class Gen:
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.events, self.cases, self.issues = [], [], []
        self.users = []
        self.inv_counter = 10_000

    # ---------- helpers ----------
    def hours(self, mean, shape=2.0):
        """Gamma-distributed waiting time in hours."""
        return float(self.rng.gamma(shape, mean / shape))

    def to_business_time(self, t):
        """Push a timestamp into office hours (Mon-Fri 08:00-18:00).
        Work that spills over starts at a random time the next morning."""
        def morning(d):
            return d.replace(hour=8, minute=0) + timedelta(minutes=int(self.rng.integers(0, 180)))
        if t.hour >= 18:
            t = morning(t + timedelta(days=1))
        elif t.hour < 8:
            t = morning(t)
        while t.weekday() >= 5:
            t = morning(t + timedelta(days=1))
        return t

    def after(self, t, hours):
        t2 = t + timedelta(hours=hours, minutes=int(self.rng.integers(0, 60)))
        return self.to_business_time(t2)

    def next_thursday(self, t):
        """AP runs payments on Thursdays only."""
        days = (3 - t.weekday()) % 7
        return (t + timedelta(days=days)).replace(hour=15, minute=int(self.rng.integers(0, 59)))

    def pick(self, seq):
        return seq[int(self.rng.integers(0, len(seq)))]

    # ---------- master data ----------
    def build_users(self):
        uid = 0

        def add(role, dept, speed=1.0):
            nonlocal uid
            uid += 1
            u = {"user_id": f"U{uid:04d}", "role": role, "department": dept, "speed": speed}
            self.users.append(u)
            return u

        self.cfo = add("CFO", "Finance", 1.0)
        self.buyers = [add("Buyer", "Procurement") for _ in range(5)]
        self.ap = [add("AP clerk", "Finance") for _ in range(4)]
        self.org = {}
        for dept in DEPARTMENTS:
            # Marketing's department head is the planted slow approver
            dh = add("Department head", dept, 3.2 if dept == "Marketing" else 1.0)
            lms = [add("Line manager", dept) for _ in range(3)]
            requesters = [add("Requester", dept) for _ in range(10)]
            self.org[dept] = {"dh": dh, "lms": lms, "req": requesters}
        # two planted slow line managers
        self.org["Operations"]["lms"][0]["speed"] = 3.5
        self.org["IT"]["lms"][1]["speed"] = 2.8

    def build_suppliers(self, n=80):
        self.suppliers = []
        for i in range(n):
            terms = "2/10 net 30" if self.rng.random() < 0.33 else "net 30"
            self.suppliers.append({"supplier_id": f"S{i + 1:03d}", "payment_terms": terms})

    # ---------- one purchase ----------
    def ev(self, case_id, act, t, user, invoice=None, amount=None):
        self.events.append({
            "case_id": case_id, "activity": ACT[act], "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user["user_id"], "role": user["role"],
            "invoice_number": invoice or "", "amount_usd": round(amount, 2) if amount else "",
        })

    def flag(self, case_id, rule, note):
        self.issues.append({"case_id": case_id, "rule": rule, "note": note})

    def purchase(self, case_id, start, dept, requester, supplier, amount, issue=None):
        o = self.org[dept]
        lm = self.pick(o["lms"])
        if issue == "C4":
            requester = lm  # line manager raises and approves their own request
        buyer, payer = self.pick(self.buyers), self.pick(self.ap)
        inv_no = f"INV-{self.inv_counter}"
        self.inv_counter += 1
        terms = supplier["payment_terms"]
        self.cases.append({
            "case_id": case_id, "department": dept, "supplier_id": supplier["supplier_id"],
            "category": self.pick(CATEGORIES[dept]), "po_amount_usd": round(amount, 2),
            "payment_terms": terms, "requester_id": requester["user_id"],
        })

        # approvals required by policy
        needed = ["lm"]
        if amount >= THRESHOLD_DH:
            needed.append("dh")
        if amount > THRESHOLD_CFO:
            needed.append("cfo")
        approvals = list(needed)
        if issue == "C1":
            skip = needed[-1] if len(needed) > 1 else "lm"
            approvals.remove(skip)
            self.flag(case_id, "C1", f"skipped {ACT[skip]}")
        approver = {"lm": lm, "dh": o["dh"], "cfo": self.cfo}
        base_wait = {"lm": 14, "dh": 22, "cfo": 40}

        t = self.to_business_time(start)

        if issue == "C2":
            # goods and invoice arrive first, paperwork is raised afterwards
            t_gr = t
            t_inv = self.after(t_gr, self.hours(30))
            self.ev(case_id, "gr", t_gr, requester)
            self.ev(case_id, "inv", t_inv, payer, inv_no, amount)
            t = self.after(t_inv, self.hours(48))
            self.flag(case_id, "C2", "invoice received before PO")

        self.ev(case_id, "req", t, requester)
        for a in approvals:
            t = self.after(t, self.hours(base_wait[a] * approver[a]["speed"]))
            self.ev(case_id, a, t, approver[a])
        if issue == "C4":
            self.flag(case_id, "C4", "requester approved own requisition")

        t = self.after(t, self.hours(10))
        self.ev(case_id, "po", t, buyer)
        if self.rng.random() < P_REWORK:
            t = self.after(t, self.hours(30))
            self.ev(case_id, "chg", t, buyer)
        t = self.after(t, self.hours(2, 1.5))
        self.ev(case_id, "send", t, buyer)

        if issue == "C2":
            t_inv_event = t_inv
            t = self.after(t, self.hours(24))
        else:
            t_gr = self.after(t, self.hours(24 * 7, 3))
            t_inv_event = self.after(t_gr, self.hours(24 * 2.5, 1.5))
            if issue != "C5":
                self.ev(case_id, "gr", t_gr, requester)
            self.ev(case_id, "inv", t_inv_event, payer, inv_no, amount)
            t = t_inv_event

        t = self.after(max(t, t_inv_event), self.hours(30))
        self.ev(case_id, "match", t, payer)
        if self.rng.random() < P_EXCEPTION:
            t = self.after(t, self.hours(24 * 5, 2))
            self.ev(case_id, "exc", t, payer)

        # payment timing
        pay_payer = payer
        if terms == "2/10 net 30" and self.rng.random() < P_FAST_PAY_DISCOUNT:
            target = t_inv_event + timedelta(days=float(self.rng.uniform(3, 9)))
        else:
            target = t_inv_event + timedelta(days=float(self.rng.normal(23, 5)))
        target = max(target, t + timedelta(hours=2))
        t_pay = self.next_thursday(target)
        self.ev(case_id, "pay", t_pay, pay_payer, inv_no, amount)

        if issue == "C5":
            t_gr_late = self.after(t_pay, self.hours(24 * 6))
            self.ev(case_id, "gr", t_gr_late, requester)
            self.flag(case_id, "C5", "paid before goods received")
        if issue == "C6":
            t_dup = self.next_thursday(t_pay + timedelta(days=float(self.rng.uniform(5, 30))))
            self.ev(case_id, "pay", t_dup, self.pick(self.ap), inv_no, amount)
            self.flag(case_id, "C6", "invoice paid twice")

    # ---------- whole log ----------
    def run(self, n_cases):
        self.build_users()
        self.build_suppliers()
        depts = list(DEPARTMENTS)
        shares = np.array([DEPARTMENTS[d][0] for d in depts])
        year_start = datetime(YEAR, 1, 1, 8)
        case_no = 0

        def new_id():
            nonlocal case_no
            case_no += 1
            return f"PO-{YEAR}-{case_no:05d}"

        for _ in range(n_cases):
            dept = depts[int(self.rng.choice(len(depts), p=shares / shares.sum()))]
            mult = DEPARTMENTS[dept][1]
            amount = float(np.clip(self.rng.lognormal(np.log(2200 * mult), 1.15), 80, 400_000))
            start = year_start + timedelta(days=float(self.rng.uniform(0, 330)),
                                           hours=float(self.rng.uniform(0, 9)))
            r = self.rng.random()
            issue = None
            risk = DEPT_RISK.get(dept, {})
            for rule, p in (("C1", P_C1), ("C2", P_C2), ("C4", P_C4), ("C5", P_C5), ("C6", P_C6)):
                p *= risk.get(rule, 1.0)
                if r < p:
                    issue = rule
                    break
                r -= p
            self.purchase(new_id(), start, dept, self.pick(self.org[dept]["req"]),
                          self.pick(self.suppliers), amount, issue)

        # C3: split purchases - one need split into 2-3 POs just under 5,000
        split_depts = list(SPLIT_DEPT_WEIGHTS)
        split_p = np.array(list(SPLIT_DEPT_WEIGHTS.values()))
        for _ in range(N_C3_GROUPS):
            dept = split_depts[int(self.rng.choice(len(split_depts), p=split_p / split_p.sum()))]
            requester, supplier = self.pick(self.org[dept]["req"]), self.pick(self.suppliers)
            parts = int(self.rng.integers(2, 4))
            start = year_start + timedelta(days=float(self.rng.uniform(0, 320)))
            group = []
            for k in range(parts):
                cid = new_id()
                amt = float(self.rng.uniform(3_200, 4_950))
                self.purchase(cid, start + timedelta(days=float(self.rng.uniform(0, 5))),
                              dept, requester, supplier, amt)
                group.append(cid)
            for cid in group:
                self.flag(cid, "C3", f"split group {group[0]} ({parts} POs)")

        self.events.sort(key=lambda e: (e["case_id"], e["timestamp"]))
        self.mark_incidental_splits()

    def mark_incidental_splits(self):
        """Ordinary purchases can meet the split rule by coincidence; by the
        rule's definition they ARE violations, so add them to the answer key."""
        planted = {i["case_id"] for i in self.issues if i["rule"] == "C3"}
        po_time = {e["case_id"]: e["timestamp"] for e in self.events if e["activity"] == ACT["po"]}
        by_key = {}
        for c in self.cases:
            if c["po_amount_usd"] < THRESHOLD_DH:
                by_key.setdefault((c["requester_id"], c["supplier_id"]), []).append(c)
        for group in by_key.values():
            group.sort(key=lambda c: po_time[c["case_id"]])
            for i, a in enumerate(group):
                ta = datetime.fromisoformat(po_time[a["case_id"]])
                window = [a]
                for b in group[i + 1:]:
                    if datetime.fromisoformat(po_time[b["case_id"]]) - ta <= timedelta(days=7):
                        window.append(b)
                if len(window) > 1 and sum(c["po_amount_usd"] for c in window) >= THRESHOLD_DH:
                    for c in window:
                        if c["case_id"] not in planted:
                            planted.add(c["case_id"])
                            self.flag(c["case_id"], "C3", "incidental (meets rule by coincidence)")


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cases", type=int, default=8000)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    g = Gen(args.seed)
    g.run(args.cases)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "events.csv", g.events,
              ["case_id", "activity", "timestamp", "user_id", "role", "invoice_number", "amount_usd"])
    write_csv(out / "cases.csv", g.cases,
              ["case_id", "department", "supplier_id", "category", "po_amount_usd",
               "payment_terms", "requester_id"])
    write_csv(out / "users.csv", g.users, ["user_id", "role", "department"])
    write_csv(out / "suppliers.csv", g.suppliers, ["supplier_id", "payment_terms"])
    write_csv(out / "planted_issues.csv", g.issues, ["case_id", "rule", "note"])

    counts = {}
    for i in g.issues:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    print(f"Cases: {len(g.cases):,}  Events: {len(g.events):,}  Users: {len(g.users)}")
    print("Planted issues:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
