# Invoice Processor — Schema & Matching Rules (frozen design)

**Date:** 2026-11-22. **Status:** approved, frozen. **n8n target:** 2.48.7.
**Money:** integer piastres everywhere (`amount_piastres = EGP × 100`). No floats
in any computation. Tax: VAT 14% on taxable lines, 0% on exempt SKUs
(`tax_rate_pct` on the PO line). Rounding: half-up to the nearest piastre,
applied identically on both sides. Currency: EGP only.

## Tables (7 tabs, Google Sheets = demo store)

| Tab | Columns |
|---|---|
| Vendors | `vendor_id \| vendor_name \| tax_id \| status` (status: `active` / `inactive`) |
| PO_Lines | `po_id \| po_line_id \| vendor_id \| sku \| description \| qty_ordered \| unit_price_piastres \| tax_rate_pct \| currency \| status` (status: `open` / `closed` / `cancelled`) |
| Receipt_Lines | `receipt_id \| po_line_id \| qty_accepted \| received_date \| status` (status: `active` / `voided`) |
| Invoices | `invoice_id \| vendor_id \| vendor_invoice_number \| document_hash \| status \| reason_code \| version \| current_review_id \| submitter_id \| subtotal_piastres \| tax_piastres \| total_piastres \| created_at \| updated_at` |
| Invoice_Lines | `invoice_id \| line_no \| po_line_id \| sku \| qty \| unit_price_piastres \| line_total_piastres \| tax_piastres` |
| Allocations | `allocation_id \| invoice_id \| po_line_id \| qty \| status \| created_at` (status: `active` / `voided`; append-only — a reversal is a NEW void entry, never an edit) |
| Audit_Log | `audit_id \| invoice_id \| old_status \| new_status \| reason_code \| decided_by \| note \| created_at` |

PO header data lives on the line rows (no separate PO header tab).
Review decisions live in Audit_Log (no separate Reviews tab);
`Invoices.current_review_id` binds an approval to its review.

## States

`received → extracted → validated → auto_matched`
or `validated → pending_receipt / needs_review / blocked`,
with terminal outcomes `exception_approved / rejected / correction_required`.

`extracted` is transient (OCR done, not yet validated). `validated` means
document validation (Stage A) passed. `auto_matched` and
`exception_approved` stay distinguishable. Neither means the invoice was paid.

## Matching engine — 7 stages, in order (first applicable rule decides)

**Stage 0 (pre-check, before persisting): document hash.**
`document_hash` already in Invoices → `duplicate_image`, no new row,
audit only. (Same rule as Sample 2 rule 1.)

**Stage A — Document validation.**
Required fields present (vendor code, invoice number, date, PO id, ≥1 line
with sku/qty/unit price/line total, subtotal, tax, total). Internal
arithmetic: every `line_total == qty × unit_price`; `subtotal == Σ line
totals`; `subtotal + tax == total`. Missing field → `missing_field` →
Needs Review. Arithmetic fails → `arithmetic_mismatch` → Needs Review.
Both go to the correction path, never to auto-match.

**Stage B — Identity and duplicates.**
- Vendor resolved by vendor code / tax_id from the invoice, against the
  Vendors master — never by name similarity. Unknown vendor → `blocked /
  vendor_mismatch`.
- Duplicate `(vendor_id, vendor_invoice_number)` already in Invoices →
  `blocked / duplicate_invoice`.
- PO exists? No → `blocked / unknown_po`. PO vendor ≠ invoice vendor →
  `blocked / vendor_mismatch`. PO status not `open` → `blocked / po_closed`.

**Stage C — Line matching.**
Each invoice line matches a PO line by `po_line_id` (printed on the
invoice layout). Line without an identifiable PO line → `needs_review /
line_unmatched` (correction). SKU not on that PO line → `blocked /
unknown_sku`. Never match on descriptions or grand totals alone.

**Stage D — Price matching.**
Per line: `line_variance = qty × |invoice_unit_price − po_unit_price|`
(piastres). Cap: `PRICE_REVIEW_MAX_EGP = 50` (5000 piastres), enforced
**both** per line **and** on `Σ|line variances|` across the invoice.
- Any line variance > 5000, or Σ > 5000 → `blocked / price_variance_blocked`
  (non-approvable; must be corrected).
- Σ > 0 (and within both caps) → `needs_review / price_variance_review`
  (the ONLY approvable reason code). **Ordering (round-4 fix):** this
  outcome is returned only AFTER Stages E and F have passed — a combined
  price-variance + tax-mismatch invoice is `blocked / tax_mismatch`,
  never reviewable. The price-review flag is held, not returned early.
- Σ = 0 → pass.

**Stage E — Quantity matching (the core).**
Per PO line: `available_received = Σ active Receipt_Lines qty_accepted
− Σ active Allocations qty`. Also `already_invoiced = Σ active
Allocations qty` for that line.
- No active receipt rows for the PO line at all (received = 0) →
  `pending_receipt / missing_receipt` (checked before the quantity
  checks — an invoice for unreceived goods parks, it is not a
  quantity violation).
- `new_qty > available_received` → `blocked / insufficient_received`.
- `already_invoiced + new_qty > qty_ordered` → `blocked / over_po_quantity`.
- Else pass. Partial deliveries and multiple invoices against one PO are
  supported; discrete goods have zero quantity tolerance; UOM conversions
  use approved mappings only (none in v1 — an unmapped UOM is
  `blocked / unknown_sku`).

**Stage F — Financial validation.**
Recompute from PO rules: `line_total = qty × unit_price` (integer);
`tax = round_half_up(line_total × tax_rate_pct / 100)` per line;
`total = subtotal + Σ tax`. Any mismatch vs the invoice's stated tax or
total → `blocked / tax_mismatch`. Discounts are not supported in v1: a
discount field breaks Stage A arithmetic and routes to correction.

**Stage G — Decision.**
All stages pass → `auto_matched` (reason `exact_match`, or
`partial_match` when the invoice is a valid partial against received
quantity). Write Allocations (one active entry per consumed PO line) +
audit. Any other outcome keeps its disposition; no allocation is written.

## Review rules

- Reviewer is one authorized Telegram user id (single source, same
  constant as the review-message routing — Sample 2 round-3 pattern).
- The review request (Approve/Reject buttons) goes to the **reviewer's**
  chat only; the sender gets the outcome notice only.
- **Approval is allowed ONLY when** `status == needs_review` **and**
  `reason_code == price_variance_review` **and** the callback's
  `(invoice_id, version, review_id)` matches the invoice's current
  `(invoice_id, version, current_review_id)` **and** the decider is the
  authorized reviewer **and** the decider is not the submitter.
  Check order in Apply: stale → submitter → auth → repeat → approvable.
  (Submitter before auth: the submitter is never the reviewer, so the
  auth check alone would mask the conflict-of-interest reason.)
  Anything else → blocked, audited, nothing changes:
  `review_blocked_auth` (wrong user), `review_blocked_submitter`
  (submitter approving own invoice), `review_blocked_stale` (old
  version/review_id), `review_blocked_repeat` (already decided),
  `review_blocked_not_approvable` (approve attempted on a non-approvable
  reason or a blocked invoice).
- **Approval goes through the Matching Engine** (mode=apply_exception):
  the review branch never decides or writes an allocation. The Engine
  re-runs the critical invariants (identity, duplicate, quantity,
  version) at commit time, records the allocation, finalizes
  `exception_approved` (reason `manual_approve`), and returns the
  outcome. A rejected or stale decision consumes nothing.
- Reviewer actions: Approve permitted exception (reason required) /
  Reject (reason required, closes the review) / Request correction
  (→ `correction_required`; the engine snapshots the invoice's current
  values into the `correction_requested` audit event **at request time,
  before the operator edits anything**; the operator fixes values in
  Sheets; revalidate creates version+1 and re-runs all 7 stages; the
  `correction_accepted` audit event references that snapshot — the old
  values are never read back from the edited rows, because they are
  already gone by then)
  / Defer (invoice stays in its current state, review stays open, audit
  event `review_deferred`).
- Every transition and every blocked attempt writes an Audit event
  (old → new + reason + decided_by + time).

## Concurrency (documented limit, not engineered around)

Sequential processing only. The claim is: **no duplicate allocation in
the supported sequential demo**. Under truly concurrent submissions the
Sheets store cannot guarantee it — the limitation is documented in the
README and review-report. The competing-invoices test (S04) is
sequential: invoice A consumes, invoice B submitted after it sees the
reduced balance and is blocked.

## Seed data (synthetic, all EGP)

### Vendors
| vendor_id | vendor_name | tax_id | status |
|---|---|---|---|
| VND-001 | Nile Office Supplies | TAX-1001 | active |
| VND-002 | Delta Trading | TAX-1002 | active |
| VND-003 | Cairo Paper Co | TAX-1003 | active |

### PO_Lines
| po_id | po_line_id | vendor_id | sku | qty_ordered | unit_price_pt | tax_rate_pct | status |
|---|---|---|---|---|---|---|---|
| PO-001 | PO-001-L1 | VND-001 | NOTE-A5 | 100 | 2000 (EGP 20.00) | 14 | open |
| PO-001 | PO-001-L2 | VND-001 | PEN-BLUE | 50 | 500 (EGP 5.00) | 14 | open |
| PO-002 | PO-002-L1 | VND-002 | TONER-X | 10 | 15000 (EGP 150.00) | 14 | open |
| PO-002 | PO-002-L2 | VND-002 | CABLE-USB | 200 | 750 (EGP 7.50) | 0 | open |
| PO-003 | PO-003-L1 | VND-001 | PAPER-A4 | 7 | 1999 (EGP 19.99) | 14 | open |
| PO-004 | PO-004-L1 | VND-002 | STAPLER | 20 | 3000 (EGP 30.00) | 14 | open |

### Receipt_Lines (seeded; PO-004-L1 has NONE initially — S10 adds it)
| receipt_id | po_line_id | qty_accepted |
|---|---|---|
| RCV-001 | PO-001-L1 | 100 |
| RCV-002 | PO-001-L2 | 50 |
| RCV-003 | PO-002-L1 | 10 |
| RCV-004 | PO-002-L2 | 200 |
| RCV-005 | PO-003-L1 | 7 |

## The 16 scenarios (frozen 15 + S16, the round-4 combined-discrepancy regression; amounts in piastres)

| # | Invoice | Expected | Proves |
|---|---|---|---|
| S01 | INV-001 (VND-002, DT-2001, PO-002): TONER-X 10×15000 + CABLE-USB 200×750; subtotal 300000; tax 21000 + 0; total 321000 | `auto_matched / exact_match` | Clean match, mixed tax rates (14% + 0%) in one invoice |
| S02 | INV-002 (VND-001, NS-1002, PO-001-L1): NOTE-A5 60×2000; subtotal 120000; tax 16800; total 136800 | `auto_matched / partial_match` | Valid partial delivery (60 of 100 received) |
| S03 | INV-003 (VND-001, NS-1003, PO-001-L1): NOTE-A5 40×2000; subtotal 80000; tax 11200; total 91200 | `auto_matched` (cumulative 100/100) | Second invoice completes the PO line |
| S04 | INV-004 (VND-001, NS-1004, PO-001-L1): NOTE-A5 50×2000 | `blocked / insufficient_received` | Third invoice after full consumption; sequential competition |
| S05 | INV-005 (VND-001, NS-1005, PO-001-L2): PEN-BLUE 10×530; line variance 300pt | `needs_review / price_variance_review` → reviewer approves → `exception_approved` | Small variance is an approvable exception |
| S06a | INV-006 (VND-001, NS-1006, PO-001-L2): PEN-BLUE 10×1200; line variance 7000pt | `blocked / price_variance_blocked`; approval attempt blocked | Single-line variance over the cap is non-approvable |
| S06b | INV-007 (VND-001, NS-1007): PEN-BLUE 10×800 (var 3000) + PAPER-A4 7×2299 (var 2100); Σ = 5100pt | `blocked / price_variance_blocked` | Sum cap: individually small variances cannot accumulate. Note: multi-PO invoice (lines from PO-001 and PO-003) — lines carry their own `po_line_id` and are matched independently |
| S07 | INV-008 (VND-002, **DT-2001 again**, different image) | `blocked / duplicate_invoice` | Same vendor + invoice number, different document |
| S08 | Resubmit INV-001's image | `duplicate_image`, no new row | Same document twice |
| S09 | INV-009 (VND-001, NS-1009, PO-999) | `blocked / unknown_po` | PO does not exist |
| S10 | INV-010 (VND-002, DT-2010, PO-004-L1): STAPLER 20×3000; subtotal 60000; tax 8400; total 68400 | `pending_receipt / missing_receipt` → add RCV-006 (20) → revalidate → `auto_matched` | Missing receipt parks, then promotes |
| S11 | INV-011 (VND-001, NS-1011, PO-001-L2): PEN-BLUE 10×500 = 5000, tax claimed 600 (correct 700) | `blocked / tax_mismatch` | Wrong tax is a correction, not an exception |
| S12 | INV-012 (VND-001, NS-1012, PO-001-L2): PEN-BLUE 10×500, mock OCR returns line total 6000 (misread) | `needs_review / arithmetic_mismatch` → correction in Sheets (line total → 5000) → revalidate (v2) → `auto_matched` | OCR corruption → correction loop with versioning |
| S13 | INV-013 (VND-001, NS-1013, PO-001-L2): PEN-BLUE 10×520; line variance 200pt | `needs_review` → unauthorized (999999) blocked + submitter self-approval blocked → reviewer approves → `exception_approved` | Authorization + segregation of duties |
| S14 | INV-014 (VND-001, NS-1014, PO-001-L2): PEN-BLUE 5×540; line variance 200pt | `needs_review` → request correction → fix unit to 500 → revalidate (v2) → `auto_matched` → replay v1 approve callback → `blocked / review_blocked_stale` | Versioned correction + replay protection |
| S15 | INV-015 (VND-001, NS-1015, PO-003-L1): PAPER-A4 7×1999 = 13993; tax 1959 (14% of 13993 = 1959.02 → 1959); total 15952 | `auto_matched / exact_match` | Float trap: `7 × 19.99 = 139.92999999999998 ≠ 139.93` in doubles — a float engine false-mismatches; integer piastres are exact |
| S16 | INV-016 (VND-001, NS-1016, PO-001-L2): PEN-BLUE 10×530 = 5300; line variance 300pt (within cap); tax claimed 700 (correct 742); total 6000 | `blocked / tax_mismatch`, no review request, no allocation; approval attempt blocked + audited | Round-4 regression: a reviewable price variance never bypasses tax validation |

Auto-matches: S01, S02, S03, S10 (after receipt), S12 (after correction),
S14 (after correction), S15 = 7 outcomes. Zero discrepant auto-matched.

## OCR layouts (2 synthetic layouts, single-page images only)

- **Layout A (standard):** header block (vendor, invoice number, date, PO
  id), table `SKU | Description | Qty | Unit Price | Total`, tax block
  (rate + amount), grand total.
- **Layout B (compact):** totals block first, table with columns in a
  different order (`Qty | SKU | Total | Unit Price`), tax as separate
  lines per rate.
- Both carry `po_line_id` per line (printed as `PO line: PO-001-L1`).
  Watermark on every image: "SYNTHETIC DEMO INVOICE - NOT A REAL TRANSACTION".
- The mock OCR returns structured fields by image SHA-256 (Sample 2
  pattern) so matching logic is testable independent of OCR; real
  Tesseract accuracy is measured separately per field.

## Engine webhook response (implementation note)

The Engine answers its webhook with an explicit **Respond to Webhook**
node (`responseMode: responseNode`), and only after every write has
landed: a **Collect Writes** node gathers all five write branches into
one item, a Merge pairs it with the Matching Engine's own item, and
Build Response rebuilds the flat response from the engine item.
`responseMode: lastNode` was tried first and is **not reliable here** —
in a multi-branch tail the node n8n records as last-executed is not
necessarily the response builder, and the write nodes' outputs (the
mock/Sheets responses) carry none of the engine's fields (see
`review-report.md` defect 7). In manual (revalidate) runs the Respond
node is a harmless pass-through. **Collect Writes also inspects every
write result** and throws on any failure (`error`, `statusCode >= 400`,
`ok === false`): a failed Sheets write fails the execution visibly
instead of being reported as success (round-4 fix). The real Sheets
write nodes carry no `continueOnFail`, so a live Sheets failure aborts
the execution at the node; the mock HTTP writes keep `continueOnFail`
so their error items reach Collect Writes.

## Reason codes (complete)

`exact_match, partial_match, price_variance_review,
price_variance_blocked, ocr_ambiguity, missing_field, line_unmatched,
arithmetic_mismatch, missing_receipt, duplicate_invoice, duplicate_image,
unknown_po, vendor_mismatch, po_closed, unknown_sku,
insufficient_received, over_po_quantity, tax_mismatch, manual_approve,
manual_reject, correction_requested, correction_accepted,
review_blocked_auth, review_blocked_repeat, review_blocked_stale,
review_blocked_not_approvable, review_blocked_submitter, review_deferred`
