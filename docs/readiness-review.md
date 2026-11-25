# Invoice Processor — Readiness Review

**Date:** 2026-11-22. **Verdict: NOT READY.** The sample is built and
proven locally, but the live run has not happened. Nothing in this file
claims otherwise.

## Proven locally (real n8n 2.48.7, real execution)

| Area | Evidence |
|---|---|
| 16-scenario suite, twice | 53/53 run1 + 53/53 run2 (`invoice-test-results-run1.json`, `invoice-test-results-run2.json`) |
| Engine logic (pure) | 45/45 (`engine-probe-results.json`) |
| Real Tesseract accuracy | 202/202 fields, conf 93–95 (`ocr-accuracy-results.json`) |
| Real OCR intake end-to-end | T-OCR passes inside the 53/53 suite |
| Combined price-variance + tax-mismatch | S16: blocked / tax_mismatch, no allocation, approval blocked (probe + live suite) |
| Failed Sheets write fails visibly | T-WRITEFAIL: Engine answers error, no row written, no Telegram message |
| Real-workflow round-trip | 0 parameter differences, connections equal (`exported-from-n8n/`) |
| 7 auto-matches + 2 exception_approved per run | Final invariants asserted in each run |
| No over-consumption; PEN-BLUE 35/50 | Final invariants asserted in each run |
| Every invoice audited | Final invariant asserted in each run |
| Review request to reviewer only (two accounts) | S05 + S13 (sender 222222, reviewer 111111) |
| Stale/replayed approval blocked | S14 (`review_blocked_stale`) |
| Float trap (7 × 19.99) matched exactly | S15 |

## NOT proven — the live run (Blocked by Credentials)

The following need the user's accounts and have **not** been tested:

1. **n8n hosting** — where his n8n runs, and a public HTTPS webhook URL
   for the Telegram Trigger (`WEBHOOK_URL`).
2. **Telegram bot** — bot token + the reviewer's numeric user id.
3. **Google Sheets** — a spreadsheet with the 7 tabs + OAuth2 credential.
4. **OCR service deployment** — a host for `invoice-ocr-service.py`
   (or an OCR.space adapter returning `image_hash`).
5. **Live steps in `setup-instructions.md` section 6** (two-account
   check, Sheets values check, demo recording).
6. **Real phone-photo OCR accuracy** — fixtures are clean renders.
7. **Free-HTTPS-webhook and Docker-OCR setup steps** — documented
   from public docs, not live-tested here.

## What "ready" requires

Run `setup-instructions.md` section 6 end-to-end on the live accounts,
with the two-account check and the Sheets values check, then record the
90-second demo. Only then can the verdict change. Until then, do not
present this sample as production-ready, and do not claim any commercial
result from the synthetic data.

## Known limitations (also in README)

- Sequential processing only; no duplicate allocation is claimed beyond
  the sequential demo.
- EGP only; one VAT rule (14% / 0%); images only (2 layouts); no PDF;
  no discounts; no multi-currency.
- No LLM in the decision path; decisions are deterministic rules.
- `auto_matched` and `exception_approved` are not payments.
