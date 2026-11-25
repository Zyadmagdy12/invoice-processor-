#!/usr/bin/env python3
"""Invoice OCR extraction via Tesseract (plain text + TSV confidence).
Two synthetic layouts, both parsed:
  Layout A table header: "PO Line  SKU  Qty  Unit Price  Total"
  Layout B table header: "Qty  SKU  Total  Unit Price  PO Line"
Amounts on the image are EGP with 2 decimals; output is integer piastres.
"""
import re
import subprocess
import sys

AMT = r"([\d,]+\.\d{2})"


def _pt(s):
    return int(round(float(s.replace(",", "")) * 100))


def _run_tesseract(path, extra_args):
    r = subprocess.run(["tesseract", path, "stdout"] + extra_args,
                       capture_output=True, text=True, timeout=120)
    return r.stdout


def _confidence(path):
    tsv = _run_tesseract(path, ["tsv"])
    confs = []
    lines = tsv.splitlines()
    if not lines:
        return 0
    hdr = lines[0].split("\t")
    try:
        ci = hdr.index("conf")
        ti = hdr.index("text")
    except ValueError:
        return 0
    for ln in lines[1:]:
        cols = ln.split("\t")
        if len(cols) <= max(ci, ti):
            continue
        try:
            c = float(cols[ci])
        except ValueError:
            continue
        if c >= 0 and cols[ti].strip():
            confs.append(c)
    return round(sum(confs) / len(confs)) if confs else 0


def _parse_lines(text):
    """Return (layout, [line dicts]) from the table block."""
    lines = text.splitlines()
    header_idx = None
    layout = None
    for i, ln in enumerate(lines):
        if "PO Line" in ln and "SKU" in ln and "Total" in ln:
            header_idx = i
            layout = "A" if ln.strip().startswith("PO") else "B"
            break
    if header_idx is None:
        return None, []
    out = []
    for ln in lines[header_idx + 1:]:
        s = ln.strip()
        if not s:
            break
        if s.startswith(("Subtotal", "Tax", "VAT", "Total", "SYNTHETIC")):
            break
        parts = re.split(r"\s{2,}", s)
        if len(parts) < 5:
            # Fallback: split on any whitespace; SKU/PO ids have no spaces.
            parts = s.split()
        if len(parts) < 5:
            continue
        try:
            if layout == "A":
                po_line_id, sku, qty, unit, total = parts[0], parts[1], int(parts[2]), _pt(parts[3]), _pt(parts[4])
            else:
                qty, sku, total, unit, po_line_id = int(parts[0]), parts[1], _pt(parts[2]), _pt(parts[3]), parts[4]
        except (ValueError, IndexError):
            continue
        out.append({"po_line_id": po_line_id, "sku": sku, "qty": qty,
                    "unit_price_pt": unit, "line_total_pt": total})
    return layout, out


def extract(path):
    # Upscale 2x first: at native size Tesseract systematically misreads
    # "PO-001-L1" as "PO-0901-L1" (proven on inv-002 / inv-008); at 2x both
    # read clean. --psm 6 alone does NOT fix it.
    from PIL import Image
    import os
    import tempfile
    im = Image.open(path)
    up = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    fd, up_path = tempfile.mkstemp(suffix=".png")
    try:
        os.close(fd)
        up.save(up_path)
        text = _run_tesseract(up_path, [])
        conf = _confidence(up_path)
    finally:
        try:
            os.unlink(up_path)
        except OSError:
            pass
    m = re.search(r"Vendor:\s*(VND-\d+)", text)
    vendor_id = m.group(1) if m else None
    m = re.search(r"Invoice No:\s*(\S+)", text)
    inv_no = m.group(1) if m else None
    m = re.search(r"PO:\s*(PO-\d+)", text)
    po_id = m.group(1) if m else None
    m = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", text)
    date = m.group(1) if m else None
    m = re.search(r"Subtotal:\s*EGP\s*" + AMT, text)
    subtotal = _pt(m.group(1)) if m else None
    tax = None
    m = re.search(r"^Tax:\s*EGP\s*" + AMT, text, re.M)
    if m:
        tax = _pt(m.group(1))
    else:
        vat = re.findall(r"VAT \d+%:\s*EGP\s*" + AMT, text)
        if vat:
            tax = sum(_pt(v) for v in vat)
    m = re.search(r"^Total:\s*EGP\s*" + AMT, text, re.M)
    total = _pt(m.group(1)) if m else None
    layout, lines = _parse_lines(text)
    return {
        "vendor_id": vendor_id,
        "vendor_invoice_number": inv_no,
        "po_id": po_id,
        "invoice_date": date,
        "lines": lines,
        "subtotal_pt": subtotal,
        "tax_pt": tax,
        "total_pt": total,
        "layout": layout,
        "confidence": conf,
        "engine": "tesseract",
        "raw_text": text,
    }


if __name__ == "__main__":
    import json
    r = extract(sys.argv[1])
    r.pop("raw_text", None)
    print(json.dumps(r, indent=2))
