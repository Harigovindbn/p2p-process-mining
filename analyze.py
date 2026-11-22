"""
Procure-to-Pay Process Mining - analysis.

Reads the event log and produces:
  output/variants.csv            process variants and their frequency
  output/transitions.csv         directly-follows transitions with wait times
  output/case_metrics.csv        one row per case: cycle time, happy path, payment timing
  output/approver_waits.csv      approval wait time per approver and level
  output/department_summary.csv  departments compared on speed and compliance
  output/control_flags.csv       every control violation (C1-C6) with evidence
  output/evaluation.md           detection accuracy vs the answer key (test data only)
  output/findings.md             headline findings and recommendations
  output/charts/*.png            charts for the README / case study
  output/process_map.png         process map (needs Graphviz 'dot'; skipped if missing)

Usage:  python analyze.py [--data data] [--out output]
"""

import argparse
import shutil
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

THRESHOLD_DH, THRESHOLD_CFO = 5_000, 50_000
SPLIT_WINDOW_DAYS = 7
APPROVALS = {
    "Approve Requisition (Line Manager)": "Line manager",
    "Approve Requisition (Dept Head)": "Department head",
    "Approve Requisition (CFO)": "CFO",
}
SHORT = {
    "Create Requisition": "Requisition", "Approve Requisition (Line Manager)": "Approve (LM)",
    "Approve Requisition (Dept Head)": "Approve (Head)", "Approve Requisition (CFO)": "Approve (CFO)",
    "Create PO": "Create PO", "Change PO": "Change PO", "Send PO to Supplier": "Send PO",
    "Receive Goods": "Goods receipt", "Receive Invoice": "Invoice", "Three-Way Match": "3-way match",
    "Clear Invoice Exception": "Clear exception", "Pay Invoice": "Pay",
}

# chart style: one hue, recessive axes (see dataviz guidance)
BLUE, MUTED, ACCENT = "#2a78d6", "#b9c7d8", "#c2410c"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
    "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
    "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "savefig.facecolor": "#fcfcfb",
})


# --------------------------------------------------------------------------
def load(data: Path):
    ev = pd.read_csv(data / "events.csv", parse_dates=["timestamp"])
    cases = pd.read_csv(data / "cases.csv")
    ev = ev.sort_values(["case_id", "timestamp"], kind="stable").reset_index(drop=True)
    return ev, cases


def quality_checks(ev, cases):
    issues = []
    for col in ["case_id", "activity", "timestamp", "user_id"]:
        n = ev[col].isna().sum()
        if n:
            issues.append(f"{n} events missing {col}")
    dup = ev.duplicated(["case_id", "activity", "timestamp"]).sum()
    if dup:
        issues.append(f"{dup} duplicate events")
    orphan = set(ev.case_id) - set(cases.case_id)
    if orphan:
        issues.append(f"{len(orphan)} cases in events but not in cases.csv")
    no_req = (~cases.case_id.isin(ev.loc[ev.activity == "Create Requisition", "case_id"])).sum()
    if no_req:
        issues.append(f"{no_req} cases with no requisition")
    return issues or ["No data-quality problems found"]


# --------------------------------------------------------------------------
def expected_path(amount):
    path = ["Create Requisition", "Approve Requisition (Line Manager)"]
    if amount >= THRESHOLD_DH:
        path.append("Approve Requisition (Dept Head)")
    if amount > THRESHOLD_CFO:
        path.append("Approve Requisition (CFO)")
    return path + ["Create PO", "Send PO to Supplier", "Receive Goods", "Receive Invoice",
                   "Three-Way Match", "Pay Invoice"]


def variants_and_paths(ev, cases):
    seq = ev.groupby("case_id")["activity"].apply(tuple)
    amt = cases.set_index("case_id")["po_amount_usd"]
    happy = pd.Series({c: s == tuple(expected_path(amt[c])) for c, s in seq.items()}, name="happy_path")
    var = seq.map(lambda s: " > ".join(SHORT[a] for a in s)).value_counts().rename_axis("variant")
    var = var.reset_index(name="cases")
    var["share"] = (var["cases"] / var["cases"].sum()).round(4)
    var.insert(0, "rank", range(1, len(var) + 1))
    return var, happy


def transitions(ev):
    e = ev.copy()
    e["next_activity"] = e.groupby("case_id")["activity"].shift(-1)
    e["next_ts"] = e.groupby("case_id")["timestamp"].shift(-1)
    e = e.dropna(subset=["next_activity"])
    e["wait_h"] = (e["next_ts"] - e["timestamp"]).dt.total_seconds() / 3600
    t = e.groupby(["activity", "next_activity"]).agg(
        count=("wait_h", "size"), median_wait_h=("wait_h", "median"),
        p90_wait_h=("wait_h", lambda x: x.quantile(0.9)), total_wait_h=("wait_h", "sum"),
    ).reset_index().sort_values("total_wait_h", ascending=False)
    return t.round(1)


def case_metrics(ev, cases, happy):
    first = ev.groupby("case_id")["timestamp"].min()
    pay = ev[ev.activity == "Pay Invoice"].groupby("case_id")["timestamp"].min()
    inv = ev[ev.activity == "Receive Invoice"].groupby("case_id")["timestamp"].min()
    rework = ev[ev.activity == "Change PO"].case_id.unique()
    exc = ev[ev.activity == "Clear Invoice Exception"].case_id.unique()
    m = cases.set_index("case_id").copy()
    req = ev[ev.activity == "Create Requisition"].groupby("case_id")["timestamp"].min()
    po = ev[ev.activity == "Create PO"].groupby("case_id")["timestamp"].min()
    m["cycle_days"] = ((pay - first).dt.total_seconds() / 86400).round(2)
    m["req_to_po_days"] = ((po - req).dt.total_seconds() / 86400).round(2)
    m["days_invoice_to_pay"] = ((pay - inv).dt.total_seconds() / 86400).round(2)
    m["happy_path"] = happy
    m["rework"] = m.index.isin(rework)
    m["invoice_exception"] = m.index.isin(exc)
    m["paid_late"] = m["days_invoice_to_pay"] > 30
    disc = m["payment_terms"].eq("2/10 net 30")
    m["discount_available_usd"] = np.where(disc, m["po_amount_usd"] * 0.02, 0).round(2)
    m["discount_captured"] = disc & (m["days_invoice_to_pay"] <= 10)
    m["discount_missed_usd"] = np.where(disc & ~m["discount_captured"], m["discount_available_usd"], 0).round(2)
    m["month"] = first.dt.to_period("M").astype(str)
    return m.reset_index()


def approver_waits(ev, users_path):
    e = ev.copy()
    e["prev_ts"] = e.groupby("case_id")["timestamp"].shift(1)
    a = e[e.activity.isin(APPROVALS)].copy()
    a["level"] = a.activity.map(APPROVALS)
    a["wait_h"] = (a["timestamp"] - a["prev_ts"]).dt.total_seconds() / 3600
    a["wait_business_days"] = np.busday_count(a["prev_ts"].values.astype("datetime64[D]"),
                                              a["timestamp"].values.astype("datetime64[D]"))
    g = a.groupby(["level", "user_id"]).agg(
        approvals=("wait_h", "size"), median_wait_h=("wait_h", "median"),
        median_business_days=("wait_business_days", "median"),
    ).reset_index()
    if users_path.exists():
        g = g.merge(pd.read_csv(users_path)[["user_id", "department"]], on="user_id", how="left")
    level_median = a.groupby("level")["wait_h"].median()
    g["level_median_h"] = g["level"].map(level_median)
    g["vs_peers"] = (g["median_wait_h"] / g["level_median_h"]).round(2)
    return g.round(1).sort_values(["level", "median_wait_h"], ascending=[True, False])


# --------------------------------------------------------------------------
def control_checks(ev, cases):
    flags = []
    c = cases.set_index("case_id")
    first_ts = lambda act: ev[ev.activity == act].groupby("case_id")["timestamp"].min()
    po_ts, inv_ts = first_ts("Create PO"), first_ts("Receive Invoice")
    gr_ts, pay_ts = first_ts("Receive Goods"), first_ts("Pay Invoice")

    # C1 missing approval before the PO
    appr = ev[ev.activity.isin(APPROVALS)].merge(po_ts.rename("po_ts"), on="case_id")
    appr = appr[appr.timestamp <= appr.po_ts].groupby("case_id")["activity"].apply(set)
    for cid, row in c.iterrows():
        have = appr.get(cid, set())
        need = set(expected_path(row.po_amount_usd)) & set(APPROVALS)
        missing = need - have
        if missing:
            flags.append((cid, "C1", row.po_amount_usd, "missing: " + ", ".join(sorted(APPROVALS[m] for m in missing))))

    # C2 invoice before PO
    both = pd.concat([inv_ts.rename("inv"), po_ts.rename("po")], axis=1).dropna()
    for cid, r in both[both.inv < both.po].iterrows():
        flags.append((cid, "C2", c.at[cid, "po_amount_usd"], f"invoice {r.inv:%Y-%m-%d} before PO {r.po:%Y-%m-%d}"))

    # C3 split purchase: same requester + supplier, POs within 7 days, each under threshold, total over
    small = c[c.po_amount_usd < THRESHOLD_DH].join(po_ts.rename("po_ts")).dropna(subset=["po_ts"])
    split_cases = {}
    for (req, sup), grp in small.groupby(["requester_id", "supplier_id"]):
        if len(grp) < 2:
            continue
        grp = grp.sort_values("po_ts")
        times, amts, ids = grp.po_ts.tolist(), grp.po_amount_usd.tolist(), grp.index.tolist()
        for i in range(len(ids)):
            win = [j for j in range(i, len(ids))
                   if times[j] - times[i] <= pd.Timedelta(days=SPLIT_WINDOW_DAYS)]
            if len(win) > 1 and sum(amts[j] for j in win) >= THRESHOLD_DH:
                total = sum(amts[j] for j in win)
                for j in win:
                    split_cases.setdefault(ids[j], (total, ids[win[0]], len(win)))
    for cid, (total, first_id, n) in split_cases.items():
        flags.append((cid, "C3", c.at[cid, "po_amount_usd"], f"{n} POs from {first_id}, total USD {total:,.0f}"))

    # C4 segregation of duties: requester approved own requisition
    appr_users = ev[ev.activity.isin(APPROVALS)][["case_id", "user_id"]]
    sod = appr_users.merge(c[["requester_id"]], left_on="case_id", right_index=True)
    for cid in sod[sod.user_id == sod.requester_id].case_id.unique():
        flags.append((cid, "C4", c.at[cid, "po_amount_usd"], f"user {c.at[cid, 'requester_id']} raised and approved"))

    # C5 paid before goods receipt (or never received)
    pg = pd.concat([pay_ts.rename("pay"), gr_ts.rename("gr")], axis=1).dropna(subset=["pay"])
    for cid, r in pg[(pg.gr.isna()) | (pg.pay < pg.gr)].iterrows():
        when = "no goods receipt" if pd.isna(r.gr) else f"goods received {r.gr:%Y-%m-%d}"
        flags.append((cid, "C5", c.at[cid, "po_amount_usd"], f"paid {r.pay:%Y-%m-%d}, {when}"))

    # C6 duplicate payment of the same invoice
    pays = ev[ev.activity == "Pay Invoice"]
    dup = pays.groupby(["case_id", "invoice_number"]).agg(n=("timestamp", "size"), amt=("amount_usd", "first"))
    for (cid, inv), r in dup[dup.n > 1].iterrows():
        flags.append((cid, "C6", r.amt * (r.n - 1), f"{inv} paid {r.n} times"))

    f = pd.DataFrame(flags, columns=["case_id", "rule", "value_usd", "evidence"])
    f = f.merge(cases[["case_id", "department", "supplier_id"]], on="case_id", how="left")
    return f.sort_values(["rule", "value_usd"], ascending=[True, False]).round(2)


def evaluate(flags, answer_key: Path):
    if not answer_key.exists():
        return None
    truth = pd.read_csv(answer_key)
    rows = []
    for rule in sorted(set(truth.rule) | set(flags.rule)):
        t = set(truth.loc[truth.rule == rule, "case_id"])
        p = set(flags.loc[flags.rule == rule, "case_id"])
        tp = len(t & p)
        rows.append({"rule": rule, "planted": len(t), "flagged": len(p), "correct": tp,
                     "precision": tp / len(p) if p else 1.0, "recall": tp / len(t) if t else 1.0})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
def save_bar(series, title, xlabel, path, highlight=None, fmt="{:,.0f}"):
    s = series.iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(s) + 1.2))
    colors = [ACCENT if highlight and highlight(k) else BLUE for k in s.index]
    ax.barh(range(len(s)), s.values, color=colors, height=0.6)
    ax.set_yticks(range(len(s)), s.index)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)
    for i, v in enumerate(s.values):
        ax.text(v, i, " " + fmt.format(v), va="center", fontsize=9, color=INK2)
    ax.set_xlim(0, s.max() * 1.15)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def charts(trans, waits, metrics, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    top = trans.head(8).copy()
    top["label"] = top.activity.map(SHORT) + " → " + top.next_activity.map(SHORT)
    save_bar(top.set_index("label")["total_wait_h"] / 24,
             "Where purchases wait: total waiting time by step", "Total waiting time (days, all cases)",
             out / "bottlenecks.png")

    w = waits[waits.level.isin(["Line manager", "Department head"])].copy()
    w["label"] = w.user_id + " · " + w.level.str.replace("Department head", "Dept head") + " · " + w.department.fillna("")
    w = w.sort_values("median_wait_h", ascending=False).head(12).set_index("label")["median_wait_h"]
    save_bar(w, "Slowest approvers: median wait for an approval", "Median wait (hours)",
             out / "approver_waits.png", highlight=lambda k: w[k] > 2 * waits.median_wait_h.median(), fmt="{:,.0f} h")

    d = metrics[metrics.payment_terms == "2/10 net 30"]["days_invoice_to_pay"].dropna()
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.hist(d.clip(upper=60), bins=np.arange(0, 61, 2), color=BLUE, edgecolor="#fcfcfb", linewidth=1)
    for x, lab in ((10, "Discount deadline (day 10)"), (30, "Due date (day 30)")):
        ax.axvline(x, color=INK2, linestyle="--", linewidth=1)
        ax.text(x + 0.6, ax.get_ylim()[1] * 0.92, lab, fontsize=9, color=INK2)
    ax.set_title("Most 2/10 invoices are paid after the discount deadline")
    ax.set_xlabel("Days from invoice received to payment")
    ax.set_ylabel("Invoices")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out / "discount_timing.png", dpi=160)
    plt.close(fig)


def process_map(trans, n_cases, path: Path):
    if not shutil.which("dot"):
        return False
    keep = trans[trans["count"] >= 0.01 * n_cases]
    lines = ['digraph P2P {', 'rankdir=TB; bgcolor="#fcfcfb";',
             'node [shape=box style="rounded" fontname="Helvetica" fontsize=11 color="#8a8984"];',
             'edge [fontname="Helvetica" fontsize=9 color="#8a8984" fontcolor="#52514e"];']
    mx = keep["count"].max()
    lines.append('{rank=source; "Requisition";} {rank=sink; "Pay";}')
    for _, r in keep.iterrows():
        pen = 0.6 + 4 * r["count"] / mx
        col = ACCENT if r.median_wait_h >= 48 else "#8a8984"
        rare = r["count"] < 0.10 * n_cases  # deviations: dashed, don't drive the layout
        style = ' style=dashed constraint=false' if rare else ''
        lines.append(f'"{SHORT[r.activity]}" -> "{SHORT[r.next_activity]}" '
                     f'[label="{r["count"]:,}\\n{r.median_wait_h:.0f} h" penwidth={pen:.1f} color="{col}"{style}];')
    lines.append("}")
    dot = path.with_suffix(".dot")
    dot.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(["dot", "-Tpng", "-Gdpi=150", str(dot), "-o", str(path)], check=True)
    return True


# --------------------------------------------------------------------------
def findings_md(ev, cases, var, trans, m, waits, flags, dq, evaluation):
    n = len(cases)
    disc = m[m.payment_terms == "2/10 net 30"]
    lm = waits[waits.level == "Line manager"]
    slow = waits[(waits.vs_peers >= 2) & (waits.approvals >= 20)]
    by_rule = flags.groupby("rule").agg(cases=("case_id", "nunique"), value=("value_usd", "sum"))
    rule_names = {"C1": "Missing approval", "C2": "After-the-fact PO", "C3": "Split purchase",
                  "C4": "Segregation of duties", "C5": "Paid before goods receipt", "C6": "Duplicate payment"}
    any_flag = flags.case_id.nunique()
    top_t = trans.iloc[0]
    dept = (m.groupby("department").agg(cases=("case_id", "size"), median_cycle_days=("cycle_days", "median"),
                                         happy_path=("happy_path", "mean"))
            .join(flags.groupby("department").case_id.nunique().rename("flagged")))
    dept["violation_rate"] = dept.flagged / dept.cases

    L = ["# Findings: Procure-to-Pay Process Mining", "",
         f"Based on {n:,} purchases and {len(ev):,} events in the synthetic 2025 event log.", "",
         "## Headline numbers", "",
         "| KPI | Result | Target |", "| --- | --- | --- |",
         f"| Median end-to-end cycle time | {m.cycle_days.median():.1f} days (90th pct {m.cycle_days.quantile(.9):.1f}) | ≤ 30 days |",
         f"| Median requisition-to-PO time | {m.req_to_po_days.median():.1f} days (90th pct {m.req_to_po_days.quantile(.9):.1f}) | – |",
         f"| Happy-path rate | {m.happy_path.mean():.1%} | ≥ 70% |",
         f"| Rework rate (PO changed) | {m.rework.mean():.1%} | ≤ 10% |",
         f"| Invoice exception rate | {m.invoice_exception.mean():.1%} | – |",
         f"| On-time payment rate | {1 - m.paid_late.mean():.1%} | ≥ 95% |",
         f"| Discount capture rate | {disc.discount_captured.mean():.1%} | ≥ 90% |",
         f"| Control violation rate | {any_flag / n:.1%} of purchases | ≤ 2% |", "",
         "## Cash impact", "",
         f"- **Missed early-payment discounts:** USD {m.discount_missed_usd.sum():,.0f} "
         f"(USD {disc.discount_available_usd.sum():,.0f} was available on {len(disc):,} invoices)",
         f"- **Duplicate payments:** USD {by_rule.loc['C6', 'value'] if 'C6' in by_rule.index else 0:,.0f} paid twice",
         f"- **Late payments:** {m.paid_late.sum():,} invoices paid after 30 days", "",
         "## Bottlenecks", "",
         f"- Biggest total wait: **{top_t.activity} → {top_t.next_activity}**, median {top_t.median_wait_h:.0f} h, "
         f"{top_t.total_wait_h / 24:,.0f} days of waiting across all cases.",
         f"- Median line-manager approval wait: {lm.median_wait_h.median():.0f} h.",
         "- Approvers waiting 2× or more than peers at the same level (≥ 20 approvals):", ""]
    for _, r in slow.iterrows():
        L.append(f"    - {r.user_id} ({r.level}, {r.department}): median {r.median_wait_h:.0f} h vs {r.level_median_h:.0f} h for peers")
    L += ["", "## Control violations", "", "| Rule | Purchases | Value (USD) |", "| --- | --- | --- |"]
    for rule, r in by_rule.iterrows():
        L.append(f"| {rule} {rule_names[rule]} | {int(r.cases):,} | {r.value:,.0f} |")
    L += ["", "## Departments", "", "| Department | Purchases | Median cycle (days) | Happy path | Violation rate |",
          "| --- | --- | --- | --- | --- |"]
    for d, r in dept.sort_values("violation_rate", ascending=False).iterrows():
        L.append(f"| {d} | {int(r.cases):,} | {r.median_cycle_days:.1f} | {r.happy_path:.0%} | {r.violation_rate:.1%} |")
    L += ["", "## Top process variants", "", "| # | Variant | Cases | Share |", "| --- | --- | --- | --- |"]
    for _, r in var.head(5).iterrows():
        L.append(f"| {r['rank']} | {r.variant} | {r.cases:,} | {r.share:.1%} |")
    L += ["", "## Data quality", ""] + [f"- {x}" for x in dq]
    if evaluation is not None:
        L += ["", "## Detection accuracy (test data)", "",
              "| Rule | Planted | Flagged | Correct | Precision | Recall |", "| --- | --- | --- | --- | --- | --- |"]
        for _, r in evaluation.iterrows():
            L.append(f"| {r.rule} | {r.planted} | {r.flagged} | {r.correct} | {r.precision:.1%} | {r.recall:.1%} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ev, cases = load(data)
    dq = quality_checks(ev, cases)
    var, happy = variants_and_paths(ev, cases)
    trans = transitions(ev)
    m = case_metrics(ev, cases, happy)
    waits = approver_waits(ev, data / "users.csv")
    flags = control_checks(ev, cases)
    evaluation = evaluate(flags, data / "planted_issues.csv")

    var.to_csv(out / "variants.csv", index=False)
    trans.to_csv(out / "transitions.csv", index=False)
    m.to_csv(out / "case_metrics.csv", index=False)
    waits.to_csv(out / "approver_waits.csv", index=False)
    flags.to_csv(out / "control_flags.csv", index=False)
    dept = m.groupby("department").agg(
        purchases=("case_id", "size"), spend_usd=("po_amount_usd", "sum"),
        median_cycle_days=("cycle_days", "median"), happy_path_rate=("happy_path", "mean"),
        discount_missed_usd=("discount_missed_usd", "sum"))
    dept = dept.join(flags.groupby("department").case_id.nunique().rename("flagged_purchases")).fillna(0)
    dept["violation_rate"] = dept.flagged_purchases / dept.purchases
    dept.round(3).to_csv(out / "department_summary.csv")
    if evaluation is not None:
        evaluation.round(3).to_csv(out / "evaluation.csv", index=False)

    charts(trans, waits, m, out / "charts")
    has_map = process_map(trans, len(cases), out / "process_map.png")
    (out / "findings.md").write_text(findings_md(ev, cases, var, trans, m, waits, flags, dq, evaluation),
                                     encoding="utf-8")

    print(f"Analysed {len(cases):,} cases / {len(ev):,} events")
    print(f"Happy path {m.happy_path.mean():.1%} | median cycle {m.cycle_days.median():.1f} d | "
          f"missed discounts USD {m.discount_missed_usd.sum():,.0f} | flagged {flags.case_id.nunique():,} cases")
    if evaluation is not None:
        print(evaluation.to_string(index=False, formatters={"precision": "{:.1%}".format, "recall": "{:.1%}".format}))
    print("Process map:", "written" if has_map else "skipped (install Graphviz to enable)")


if __name__ == "__main__":
    main()
