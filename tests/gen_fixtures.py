#!/usr/bin/env python3
"""Render synthetic invoice fixtures (2 layouts) + fixtures.json.
Amounts on the image are EGP with 2 decimals; ground truth is in piastres.
Watermark on every image: SYNTHETIC DEMO INVOICE - NOT A REAL TRANSACTION.
"""
import hashlib
import json
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(BASE, "fixtures")
os.makedirs(FIX, exist_ok=True)
F_BIG = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
F_TXT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
F_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# name, layout, vendor_id, vendor_invoice_number, po_id, lines, subtotal, tax, total (EGP), override?
# lines: (po_line_id, sku, qty, unit_price_egp, line_total_egp)
INVOICES = [
    ("inv-001", "B", "VND-002", "DT-2001", "PO-002",
     [("PO-002-L1", "TONER-X", 10, 150.00, 1500.00), ("PO-002-L2", "CABLE-USB", 200, 7.50, 1500.00)],
     3000.00, 210.00, 3210.00, None),
    ("inv-002", "A", "VND-001", "NS-1002", "PO-001",
     [("PO-001-L1", "NOTE-A5", 60, 20.00, 1200.00)], 1200.00, 168.00, 1368.00, None),
    ("inv-003", "A", "VND-001", "NS-1003", "PO-001",
     [("PO-001-L1", "NOTE-A5", 40, 20.00, 800.00)], 800.00, 112.00, 912.00, None),
    ("inv-004", "A", "VND-001", "NS-1004", "PO-001",
     [("PO-001-L1", "NOTE-A5", 50, 20.00, 1000.00)], 1000.00, 140.00, 1140.00, None),
    ("inv-005", "A", "VND-001", "NS-1005", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 5.30, 53.00)], 53.00, 7.42, 60.42, None),
    ("inv-006", "A", "VND-001", "NS-1006", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 12.00, 120.00)], 120.00, 16.80, 136.80, None),
    ("inv-007", "A", "VND-001", "NS-1007", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 8.00, 80.00), ("PO-003-L1", "PAPER-A4", 7, 22.99, 160.93)],
     240.93, 33.73, 274.66, None),
    ("inv-008", "A", "VND-002", "DT-2001", "PO-002",
     [("PO-002-L1", "TONER-X", 5, 150.00, 750.00)], 750.00, 105.00, 855.00, None),
    ("inv-009", "A", "VND-001", "NS-1009", "PO-999",
     [("PO-999-L1", "NOTE-A5", 10, 20.00, 200.00)], 200.00, 28.00, 228.00, None),
    ("inv-010", "B", "VND-002", "DT-2010", "PO-004",
     [("PO-004-L1", "STAPLER", 20, 30.00, 600.00)], 600.00, 84.00, 684.00, None),
    ("inv-011", "A", "VND-001", "NS-1011", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 5.00, 50.00)], 50.00, 6.00, 56.00, None),  # tax wrong on purpose (correct 7.00)
    ("inv-012", "A", "VND-001", "NS-1012", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 5.00, 50.00)], 50.00, 7.00, 57.00,
     "MISREAD_LINE_TOTAL_6000"),
    ("inv-013", "A", "VND-001", "NS-1013", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 5.20, 52.00)], 52.00, 7.28, 59.28, None),
    ("inv-014", "A", "VND-001", "NS-1014", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 5, 5.40, 27.00)], 27.00, 3.78, 30.78, None),
    ("inv-015", "B", "VND-001", "NS-1015", "PO-003",
     [("PO-003-L1", "PAPER-A4", 7, 19.99, 139.93)], 139.93, 19.59, 159.52, None),
    ("inv-016", "A", "VND-001", "NS-1016", "PO-001",
     [("PO-001-L2", "PEN-BLUE", 10, 5.30, 53.00)], 53.00, 7.00, 60.00, None),  # combined: variance 300pt (within cap) + tax 7.00 vs correct 7.42
]

VENDOR_NAMES = {"VND-001": "Nile Office Supplies", "VND-002": "Delta Trading", "VND-003": "Cairo Paper Co"}


def egp(x):
    return f"{x:.2f}"


def pt(x):
    return int(round(x * 100))


def render(name, layout, vendor_id, inv_no, po_id, lines, subtotal, tax, total):
    img = Image.new("RGB", (1000, 980), "white")
    d = ImageDraw.Draw(img)
    f_title = ImageFont.truetype(F_BIG, 30)
    f_h = ImageFont.truetype(F_TXT, 24)
    f_m = ImageFont.truetype(F_MONO, 22)
    y = 30
    d.text((40, y), "TAX INVOICE", font=f_title, fill="black")
    y += 60
    d.text((40, y), f"Vendor: {vendor_id} ({VENDOR_NAMES[vendor_id]})", font=f_h, fill="black")
    y += 40
    d.text((40, y), f"Invoice No: {inv_no}", font=f_h, fill="black")
    y += 40
    d.text((40, y), f"PO: {po_id}", font=f_h, fill="black")
    y += 40
    d.text((40, y), "Date: 2026-11-22", font=f_h, fill="black")
    y += 55
    if layout == "A":
        d.text((40, y), "PO Line     SKU        Qty   Unit Price   Total", font=f_m, fill="black")
        y += 36
        for (pl, sku, qty, unit, lt) in lines:
            d.text((40, y), f"{pl}  {sku:<10} {qty:>4}  {egp(unit):>9}  {egp(lt):>9}", font=f_m, fill="black")
            y += 36
        y += 20
        d.text((40, y), f"Subtotal: EGP {egp(subtotal)}", font=f_h, fill="black")
        y += 40
        d.text((40, y), f"Tax: EGP {egp(tax)}", font=f_h, fill="black")
        y += 40
        d.text((40, y), f"Total: EGP {egp(total)}", font=f_h, fill="black")
    else:
        d.text((40, y), f"Total: EGP {egp(total)}", font=f_h, fill="black")
        y += 40
        d.text((40, y), f"Subtotal: EGP {egp(subtotal)}", font=f_h, fill="black")
        y += 55
        d.text((40, y), "Qty   SKU        Total      Unit Price  PO Line", font=f_m, fill="black")
        y += 36
        for (pl, sku, qty, unit, lt) in lines:
            d.text((40, y), f"{qty:>4}  {sku:<10} {egp(lt):>9}  {egp(unit):>9}  {pl}", font=f_m, fill="black")
            y += 36
        y += 20
        # Tax as separate lines per rate (layout B)
        d.text((40, y), f"VAT 14%: EGP {egp(tax)}", font=f_h, fill="black")
        y += 40
        d.text((40, y), "VAT 0%: EGP 0.00", font=f_h, fill="black")
    y = 900
    d.text((40, y), "SYNTHETIC DEMO INVOICE - NOT A REAL TRANSACTION", font=f_h, fill="gray")
    img.save(os.path.join(FIX, name + ".png"))


def main():
    out = {}
    for (name, layout, vendor_id, inv_no, po_id, lines, subtotal, tax, total, override) in INVOICES:
        render(name, layout, vendor_id, inv_no, po_id, lines, subtotal, tax, total)
        path = os.path.join(FIX, name + ".png")
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        gt_lines = [{"po_line_id": pl, "sku": sku, "qty": qty,
                     "unit_price_pt": pt(unit), "line_total_pt": pt(lt)} for (pl, sku, qty, unit, lt) in lines]
        gt = {"vendor_id": vendor_id, "vendor_invoice_number": inv_no, "po_id": po_id,
              "invoice_date": "2026-11-22", "lines": gt_lines,
              "subtotal_pt": pt(subtotal), "tax_pt": pt(tax), "total_pt": pt(total)}
        entry = {"name": name, "layout": layout, "ground_truth": gt}
        if override == "MISREAD_LINE_TOTAL_6000":
            bad = [dict(l) for l in gt_lines]
            bad[0]["line_total_pt"] = 6000
            entry["mock_override"] = {"lines": bad}
        out[h] = entry
        print(name, layout, h[:12], "total_pt", pt(total))
    with open(os.path.join(BASE, "fixtures.json"), "w") as f:
        json.dump(out, f, indent=2)
    print("fixtures:", len(out))


if __name__ == "__main__":
    main()
