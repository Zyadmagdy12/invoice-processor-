# Case Study — Invoice Processor (3-Way Matching)

**Client type (demo):** a small Egyptian office-supplies buyer paying
suppliers by bank transfer and reconciling paper invoices by hand.
**Problem:** an invoice that does not match its purchase order — wrong
price, wrong quantity, or a receipt that never arrived — is paid anyway,
or sits in a pile until the supplier calls. Matching by eye does not
scale, and a spreadsheet total hides line-level differences.
**What was built:** a deterministic 3-way matching sample in n8n
(2 workflows, 7 Sheets tabs). A supplier sends an invoice photo on
Telegram; OCR extracts it; one engine matches it line by line against
the PO and the goods receipts — price, quantity and tax, in integer
piastres — and routes every discrepancy to a human reviewer on Telegram.
Every decision is audited.

## The control that matters

The match is cumulative, not per-invoice. Each PO line carries a
received balance (`accepted_received − already_invoiced`), drawn down in
an append-only ledger. The demo proves it with 100 notebooks:
a 60-unit invoice matches, a 40-unit invoice completes the line, and a
third invoice for 50 is blocked — the system saw the reduced balance.
No discrepant invoice auto-matches: zero automatic tolerance, a
EGP 50 cap on price variances (per line **and** on their sum), and tax
mismatches are corrections, never exceptions.

## Results (local, synthetic data — clearly labelled)

- **16 scenarios, 53/53 checks, twice** in real n8n 2.48.7: clean and
  partial matches, cumulative drawdown, sequential competition, small
  variance approved as a documented exception, large variance blocked
  with the approval attempt blocked and audited, duplicate invoice and
  duplicate image blocked, unknown PO blocked, missing receipt parked
  then promoted, tax mismatch blocked, OCR corruption corrected and
  revalidated, unauthorized and self-approval blocked, replayed approval
  blocked as stale, and the float trap (7 × 19.99) matched exactly.
  An independent review then found a real bypass — a within-cap price
  variance short-circuited tax validation — fixed by reordering the
  stages and covered by a dedicated regression case, plus a write-failure
  test proving a failed Sheets write is never reported as success.
- **Real Tesseract accuracy: 202/202 fields** on the synthetic invoices.
- **Real workflows round-trip through n8n with 0 differences.**

## Honest limits

All data is synthetic. Google Sheets is the demo store, and the
no-duplicate-allocation claim holds for the supported sequential demo
only — under truly concurrent submissions Sheets cannot guarantee it.
The live run against real accounts has not happened yet; the sample is
not declared ready. `auto_matched` and `exception_approved` are
matching outcomes, not payments.
