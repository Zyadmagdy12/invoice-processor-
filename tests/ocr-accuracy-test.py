#!/usr/bin/env python3
"""Real Tesseract accuracy on the 15 invoice fixtures.
Fields scored: vendor_id, vendor_invoice_number, po_id, invoice_date,
per-line (po_line_id, sku, qty, unit_price_pt, line_total_pt),
subtotal_pt, tax_pt, total_pt. Writes ocr-accuracy-results.json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from invoice_extract import extract

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    fixtures = json.load(open(os.path.join(BASE, "fixtures.json")))
    results = []
    total = correct = 0
    for h, fx in sorted(fixtures.items(), key=lambda kv: kv[1]["name"]):
        gt = fx["ground_truth"]
        r = extract(os.path.join(BASE, "fixtures", fx["name"] + ".png"))
        checks = []
        for f in ["vendor_id", "vendor_invoice_number", "po_id", "invoice_date",
                  "subtotal_pt", "tax_pt", "total_pt"]:
            ok = r.get(f) == gt[f]
            checks.append({"field": f, "expected": gt[f], "got": r.get(f), "ok": ok})
        if len(r["lines"]) != len(gt["lines"]):
            checks.append({"field": "lines_count", "expected": len(gt["lines"]),
                           "got": len(r["lines"]), "ok": False})
        for i, gl in enumerate(gt["lines"]):
            rl = r["lines"][i] if i < len(r["lines"]) else {}
            for f in ["po_line_id", "sku", "qty", "unit_price_pt", "line_total_pt"]:
                ok = rl.get(f) == gl[f]
                checks.append({"field": f"line{i}.{f}", "expected": gl[f], "got": rl.get(f), "ok": ok})
        c = sum(1 for ch in checks if ch["ok"])
        results.append({"name": fx["name"], "layout": fx["layout"], "confidence": r["confidence"],
                        "passed": f"{c}/{len(checks)}", "failures": [ch for ch in checks if not ch["ok"]]})
        total += len(checks)
        correct += c
        print(fx["name"], f"{c}/{len(checks)}", "conf", r["confidence"],
              "FAIL:", [f["field"] for f in results[-1]["failures"]] or "-")
    out = {"total": f"{correct}/{total}", "fixtures": results}
    json.dump(out, open(os.path.join(BASE, "ocr-accuracy-results.json"), "w"), indent=2)
    print("TOTAL", out["total"])


if __name__ == "__main__":
    main()
