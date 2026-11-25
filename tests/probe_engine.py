#!/usr/bin/env python3
"""Engine unit tests: run ENGINE_JS under node for all 15 scenarios
against in-memory tables (same seeds as mock-server.js), applying the
returned rows exactly as the n8n workflow would (upsert invoice, append
lines / allocations / audit). No n8n, no HTTP -- pure logic proof.
Usage: python3 probe_engine.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from invoice_extra import build_engine_js, REVIEWER_ID_MOCK

BASE = os.path.dirname(os.path.abspath(__file__))
SENDER = "222222"
REVIEWER = REVIEWER_ID_MOCK

VENDORS = [
    {"vendor_id": "VND-001", "vendor_name": "Nile Office Supplies", "tax_id": "TAX-1001", "status": "active"},
    {"vendor_id": "VND-002", "vendor_name": "Delta Trading", "tax_id": "TAX-1002", "status": "active"},
    {"vendor_id": "VND-003", "vendor_name": "Cairo Paper Co", "tax_id": "TAX-1003", "status": "active"},
]
PO = [
    {"po_id": "PO-001", "po_line_id": "PO-001-L1", "vendor_id": "VND-001", "sku": "NOTE-A5", "description": "Notebook A5", "qty_ordered": 100, "unit_price_piastres": 2000, "tax_rate_pct": 14, "currency": "EGP", "status": "open"},
    {"po_id": "PO-001", "po_line_id": "PO-001-L2", "vendor_id": "VND-001", "sku": "PEN-BLUE", "description": "Pen blue", "qty_ordered": 50, "unit_price_piastres": 500, "tax_rate_pct": 14, "currency": "EGP", "status": "open"},
    {"po_id": "PO-002", "po_line_id": "PO-002-L1", "vendor_id": "VND-002", "sku": "TONER-X", "description": "Toner cartridge X", "qty_ordered": 10, "unit_price_piastres": 15000, "tax_rate_pct": 14, "currency": "EGP", "status": "open"},
    {"po_id": "PO-002", "po_line_id": "PO-002-L2", "vendor_id": "VND-002", "sku": "CABLE-USB", "description": "USB cable", "qty_ordered": 200, "unit_price_piastres": 750, "tax_rate_pct": 0, "currency": "EGP", "status": "open"},
    {"po_id": "PO-003", "po_line_id": "PO-003-L1", "vendor_id": "VND-001", "sku": "PAPER-A4", "description": "Paper A4 ream", "qty_ordered": 7, "unit_price_piastres": 1999, "tax_rate_pct": 14, "currency": "EGP", "status": "open"},
    {"po_id": "PO-004", "po_line_id": "PO-004-L1", "vendor_id": "VND-002", "sku": "STAPLER", "description": "Stapler", "qty_ordered": 20, "unit_price_piastres": 3000, "tax_rate_pct": 14, "currency": "EGP", "status": "open"},
]
RECEIPTS = [
    {"receipt_id": "RCV-001", "po_line_id": "PO-001-L1", "qty_accepted": 100, "received_date": "2026-11-20", "status": "active"},
    {"receipt_id": "RCV-002", "po_line_id": "PO-001-L2", "qty_accepted": 50, "received_date": "2026-11-20", "status": "active"},
    {"receipt_id": "RCV-003", "po_line_id": "PO-002-L1", "qty_accepted": 10, "received_date": "2026-11-20", "status": "active"},
    {"receipt_id": "RCV-004", "po_line_id": "PO-002-L2", "qty_accepted": 200, "received_date": "2026-11-20", "status": "active"},
    {"receipt_id": "RCV-005", "po_line_id": "PO-003-L1", "qty_accepted": 7, "received_date": "2026-11-20", "status": "active"},
]

# ---- node harness ----
HARNESS = """
const fs = require('fs');
const js = fs.readFileSync(process.argv[2], 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = new Function('$input', js);
const out = fn({ first: () => ({ json: input }) });
console.log(JSON.stringify(out));
"""


class Engine:
    def __init__(self):
        self.tables = {"Vendors": list(VENDORS), "PO_Lines": list(PO),
                       "Receipt_Lines": list(RECEIPTS), "Invoices": [],
                       "Invoice_Lines": [], "Allocations": [], "Audit_Log": []}
        self.js_path = os.path.join(BASE, ".engine_probe.js")
        with open(self.js_path, "w") as f:
            f.write(build_engine_js(REVIEWER))
        self.harness = os.path.join(BASE, ".engine_harness.js")
        with open(self.harness, "w") as f:
            f.write(HARNESS)

    def call(self, payload):
        inp = dict(self.tables)
        inp.update(payload)
        p = subprocess.run(["node", self.harness, self.js_path],
                           input=json.dumps(inp).encode(), capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("engine JS failed: " + p.stderr.decode()[:2000])
        return json.loads(p.stdout.decode())[0]["json"]

    def apply(self, out):
        """Apply returned rows to the in-memory tables, as the workflow does."""
        if out.get("invoice_row"):
            row = out["invoice_row"]
            ex = next((r for r in self.tables["Invoices"] if r["invoice_id"] == row["invoice_id"]), None)
            if ex:
                ex.update(row)
            else:
                self.tables["Invoices"].append(dict(row))
        for r in out.get("lines_rows") or []:
            self.tables["Invoice_Lines"].append(dict(r))
        for r in out.get("allocation_rows") or []:
            self.tables["Allocations"].append(dict(r))
        for r in out.get("audit_rows") or []:
            self.tables["Audit_Log"].append(dict(r))
        return out

    def invoice(self, invoice_id):
        return next((r for r in self.tables["Invoices"] if r["invoice_id"] == invoice_id), None)

    def consumed(self, po_line_id):
        return sum(r["qty"] for r in self.tables["Allocations"]
                   if r["po_line_id"] == po_line_id and r["status"] == "active")


def fx_map():
    fixtures = json.load(open(os.path.join(BASE, "fixtures.json")))
    m = {}
    for h, fx in fixtures.items():
        gt = dict(fx["ground_truth"])
        if "mock_override" in fx:
            for k, v in fx["mock_override"].items():
                gt[k] = v
        gt["document_hash"] = h
        m[fx["name"]] = gt
    return m


def payload_for(gt):
    return {"vendor_id": gt["vendor_id"], "vendor_invoice_number": gt["vendor_invoice_number"],
            "po_id": gt["po_id"], "invoice_date": gt["invoice_date"],
            "subtotal_pt": gt["subtotal_pt"], "tax_pt": gt["tax_pt"], "total_pt": gt["total_pt"],
            "document_hash": gt["document_hash"], "submitter_id": SENDER}


def main():
    fx = fx_map()
    e = Engine()
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        print(("PASS " if cond else "FAIL ") + name + ((" — " + str(detail)) if detail and not cond else ""))

    def match(name):
        gt = fx[name]
        out = e.apply(e.call({"mode": "match", "invoice": payload_for(gt), "lines": gt["lines"]}))
        return out

    # S01 clean match (mixed tax)
    o = match("inv-001")
    check("S01 auto_matched/exact_match", o["status"] == "auto_matched" and o["reason_code"] == "exact_match", o)
    check("S01 allocations 2 lines", len(o["allocation_rows"]) == 2, o)
    # S02 partial
    o = match("inv-002")
    check("S02 auto_matched/partial_match", o["status"] == "auto_matched" and o["reason_code"] == "partial_match", o)
    # S03 completes the PO line
    o = match("inv-003")
    check("S03 auto_matched", o["status"] == "auto_matched", o)
    check("S03 NOTE-A5 consumed 100", e.consumed("PO-001-L1") == 100, e.consumed("PO-001-L1"))
    # S04 third invoice after full consumption
    o = match("inv-004")
    check("S04 blocked/insufficient_received", o["status"] == "blocked" and o["reason_code"] == "insufficient_received", o)
    check("S04 no allocation", len(o["allocation_rows"]) == 0, o)
    # S05 small variance -> review -> approve
    o = match("inv-005")
    inv5 = o["invoice_id"]
    check("S05 needs_review/price_variance_review", o["status"] == "needs_review" and o["reason_code"] == "price_variance_review", o)
    check("S05 review id bound", o["invoice_row"]["current_review_id"] == "REV-" + inv5 + "-v1", o)
    o = e.apply(e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv5, "version": 1, "review_id": "REV-" + inv5 + "-v1",
        "action": "approve", "reason_key": "price_within_policy", "from_id": REVIEWER}}))
    check("S05 approve -> exception_approved", o["status"] == "exception_approved" and o["reason_code"] == "manual_approve", o)
    check("S05 allocation recorded", len(o["allocation_rows"]) == 1 and o["allocation_rows"][0]["qty"] == 10, o)
    # S06a large variance -> blocked; approval attempt blocked
    o = match("inv-006")
    inv6 = o["invoice_id"]
    check("S06a blocked/price_variance_blocked", o["status"] == "blocked" and o["reason_code"] == "price_variance_blocked", o)
    o2 = e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv6, "version": 1, "review_id": "",
        "action": "approve", "reason_key": "price_within_policy", "from_id": REVIEWER}})
    check("S06a approval blocked (not_approvable/repeat)", o2["outcome"].startswith("review_blocked"), o2)
    check("S06a no allocation from blocked approval", len(o2["allocation_rows"]) == 0, o2)
    # S06b sum cap
    o = match("inv-007")
    check("S06b blocked/price_variance_blocked (sum 5100 > 5000)", o["status"] == "blocked" and o["reason_code"] == "price_variance_blocked", o)
    # S07 duplicate number
    o = match("inv-008")
    check("S07 blocked/duplicate_invoice", o["status"] == "blocked" and o["reason_code"] == "duplicate_invoice", o)
    # S08 same image again
    n_before = len(e.tables["Invoices"])
    o = e.call({"mode": "match", "invoice": payload_for(fx["inv-001"]), "lines": fx["inv-001"]["lines"]})
    check("S08 duplicate_image", o["outcome"] == "duplicate_image" and o.get("no_new_row") is True, o)
    check("S08 no new invoice row", len(e.tables["Invoices"]) == n_before, (n_before, len(e.tables["Invoices"])))
    e.apply(o)
    # S09 unknown PO
    o = match("inv-009")
    check("S09 blocked/unknown_po", o["status"] == "blocked" and o["reason_code"] == "unknown_po", o)
    # S10 missing receipt -> pending -> add receipt -> revalidate -> matched
    o = match("inv-010")
    inv10 = o["invoice_id"]
    check("S10 pending_receipt/missing_receipt", o["status"] == "pending_receipt" and o["reason_code"] == "missing_receipt", o)
    e.tables["Receipt_Lines"].append({"receipt_id": "RCV-006", "po_line_id": "PO-004-L1", "qty_accepted": 20, "received_date": "2026-11-22", "status": "active"})
    o = e.apply(e.call({"mode": "revalidate", "invoice_id": inv10, "requested_by": REVIEWER}))
    check("S10 revalidate -> auto_matched", o["status"] == "auto_matched", o)
    check("S10 STAPLER consumed 20", e.consumed("PO-004-L1") == 20, e.consumed("PO-004-L1"))
    # S11 tax mismatch
    o = match("inv-011")
    check("S11 blocked/tax_mismatch", o["status"] == "blocked" and o["reason_code"] == "tax_mismatch", o)
    # S12 OCR misread -> correction -> revalidate v2 -> matched
    o = match("inv-012")
    inv12 = o["invoice_id"]
    check("S12 needs_review/arithmetic_mismatch", o["status"] == "needs_review" and o["reason_code"] == "arithmetic_mismatch", o)
    o = e.apply(e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv12, "version": 1, "review_id": o["invoice_row"]["current_review_id"],
        "action": "correction", "reason_key": "wrong_total", "from_id": REVIEWER}}))
    check("S12 correction -> correction_required", o["status"] == "correction_required", o)
    ln = next(r for r in e.tables["Invoice_Lines"] if r["invoice_id"] == inv12)
    ln["line_total_piastres"] = 5000  # operator fixes the misread digit in Sheets
    o = e.apply(e.call({"mode": "revalidate", "invoice_id": inv12, "requested_by": REVIEWER}))
    check("S12 revalidate v2 -> auto_matched", o["status"] == "auto_matched" and o["version"] == 2, o)
    old_vals = [a for a in e.tables["Audit_Log"] if a["invoice_id"] == inv12 and a["reason_code"] == "correction_requested"]
    check("S12 old values snapshotted before edit", len(old_vals) == 1 and "6000" in old_vals[0]["note"], old_vals)
    check("S12 correction_accepted event exists", any(
        a["invoice_id"] == inv12 and a["reason_code"] == "correction_accepted" for a in e.tables["Audit_Log"]), None)
    # S13 unauthorized + submitter + reviewer approval
    o = match("inv-013")
    inv13 = o["invoice_id"]
    rev13 = o["invoice_row"]["current_review_id"]
    check("S13 needs_review", o["status"] == "needs_review", o)
    o2 = e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv13, "version": 1, "review_id": rev13,
        "action": "approve", "reason_key": "price_within_policy", "from_id": "999999"}})
    check("S13 wrong user blocked (auth)", o2["outcome"] == "review_blocked_auth", o2)
    o2 = e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv13, "version": 1, "review_id": rev13,
        "action": "approve", "reason_key": "price_within_policy", "from_id": SENDER}})
    check("S13 submitter blocked (SoD)", o2["outcome"] == "review_blocked_submitter", o2)
    o = e.apply(e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv13, "version": 1, "review_id": rev13,
        "action": "approve", "reason_key": "price_within_policy", "from_id": REVIEWER}}))
    check("S13 reviewer approve -> exception_approved", o["status"] == "exception_approved", o)
    check("S13 still needs_review before? no allocation leak", e.consumed("PO-001-L2") == 30, e.consumed("PO-001-L2"))
    # S14 correction + replay stale
    o = match("inv-014")
    inv14 = o["invoice_id"]
    rev14 = o["invoice_row"]["current_review_id"]
    check("S14 needs_review", o["status"] == "needs_review", o)
    o = e.apply(e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv14, "version": 1, "review_id": rev14,
        "action": "correction", "reason_key": "wrong_price", "from_id": REVIEWER}}))
    check("S14 correction_required", o["status"] == "correction_required", o)
    inv_row = e.invoice(inv14)
    inv_row["subtotal_piastres"] = 2500
    inv_row["tax_piastres"] = 350
    inv_row["total_piastres"] = 2850
    ln = next(r for r in e.tables["Invoice_Lines"] if r["invoice_id"] == inv14)
    ln["unit_price_piastres"] = 500
    ln["line_total_piastres"] = 2500
    o = e.apply(e.call({"mode": "revalidate", "invoice_id": inv14, "requested_by": REVIEWER}))
    check("S14 revalidate v2 -> auto_matched", o["status"] == "auto_matched" and o["version"] == 2, o)
    o2 = e.call({"mode": "apply_exception", "decision": {
        "invoice_id": inv14, "version": 1, "review_id": rev14,
        "action": "approve", "reason_key": "price_within_policy", "from_id": REVIEWER}})
    check("S14 replay v1 approve blocked (stale)", o2["outcome"] == "review_blocked_stale", o2)
    # S15 float trap
    o = match("inv-015")
    check("S15 auto_matched/exact_match (float trap)", o["status"] == "auto_matched" and o["reason_code"] == "exact_match", o)
    check("S15 PAPER-A4 consumed 7", e.consumed("PO-003-L1") == 7, e.consumed("PO-003-L1"))
    # S16 combined price variance (within cap) + tax mismatch -> blocked
    # Round-4 regression: the price-review return must not run before Stage F.
    o = match("inv-016")
    check("S16 blocked/tax_mismatch (variance within cap + wrong tax)", o["status"] == "blocked" and o["reason_code"] == "tax_mismatch", o)
    check("S16 no allocation", len(o["allocation_rows"]) == 0, o)
    # Final invariants
    auto = [r for r in e.tables["Invoices"] if r["status"] == "auto_matched"]
    exc = [r for r in e.tables["Invoices"] if r["status"] == "exception_approved"]
    check("FINAL: 7 auto_matched", len(auto) == 7, [r["invoice_id"] for r in auto])
    check("FINAL: 2 exception_approved", len(exc) == 2, [r["invoice_id"] for r in exc])
    check("FINAL: every invoice has an audit event", all(
        any(a["invoice_id"] == r["invoice_id"] for a in e.tables["Audit_Log"]) for r in e.tables["Invoices"]), None)
    check("FINAL: no over-consumption anywhere", all(
        e.consumed(pl["po_line_id"]) <= sum(r["qty_accepted"] for r in e.tables["Receipt_Lines"]
                                            if r["po_line_id"] == pl["po_line_id"]) for pl in PO), None)
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} engine checks passed")
    json.dump({"passed": len(checks) - len(failed), "total": len(checks),
               "failed": [c[0] for c in failed]},
              open(os.path.join(BASE, "engine-probe-results.json"), "w"), indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
