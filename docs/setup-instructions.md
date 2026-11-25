# Invoice Processor — Setup Instructions (live run)

**Target:** n8n 2.48.7. **Workflows:** 2 (Telegram unified + Matching Engine).
**Status:** local tests pass (44/44 twice). This document is the live-run
checklist — nothing here has been run against real accounts yet.

## 1. Google Sheets (the demo store)

Create one spreadsheet with **7 tabs**, exact names and columns (column
order matters for the Sheets nodes' resourceMapper):

| Tab | Columns (in order) |
|---|---|
| Vendors | `vendor_id \| vendor_name \| tax_id \| status` |
| PO_Lines | `po_id \| po_line_id \| vendor_id \| sku \| description \| qty_ordered \| unit_price_piastres \| tax_rate_pct \| currency \| status` |
| Receipt_Lines | `receipt_id \| po_line_id \| qty_accepted \| received_date \| status` |
| Invoices | `invoice_id \| vendor_id \| vendor_invoice_number \| document_hash \| status \| reason_code \| version \| current_review_id \| submitter_id \| subtotal_piastres \| tax_piastres \| total_piastres \| created_at \| updated_at` |
| Invoice_Lines | `invoice_id \| line_no \| po_line_id \| sku \| qty \| unit_price_piastres \| line_total_piastres \| tax_piastres` |
| Allocations | `allocation_id \| invoice_id \| po_line_id \| qty \| status \| created_at` |
| Audit_Log | `audit_id \| invoice_id \| old_status \| new_status \| reason_code \| decided_by \| note \| created_at` |

- All money is **integer piastres** (EGP × 100). Never store floats.
- Seed the Vendors / PO_Lines / Receipt_Lines tabs with the data in
  `schema.md` (or your own equivalent demo data — keep the amounts small
  and the vendors/tax IDs consistent with the invoice images you will use).
- Leave Invoices / Invoice_Lines / Allocations / Audit_Log empty.
- Share the spreadsheet with the Google account that owns the n8n
  Google Sheets OAuth2 credential, and copy the spreadsheet ID from its
  URL — it replaces `FILL_SHEETS_ID` in the workflows.

## 2. Telegram bot + reviewer

1. Create the bot with @BotFather, keep the token.
2. Start a chat with the bot from **two** accounts: the submitter (who
   sends invoice photos) and the reviewer (who decides). Message
   @userinfobot (or equivalent) from each account to get their numeric
   Telegram user ids.
3. The reviewer id is a **single source**: it is both the chat the review
   request (Approve/Reject buttons) is sent to **and** the id the Apply
   Decision gate checks. Replace `FILL_REVIEWER_TELEGRAM_ID` in both
   workflows with that id (it is one constant in the builder).
4. Add the bot token as a Telegram credential in n8n
   ("Telegram Bot — fill on import").

## 3. OCR service

The Engine calls an OCR HTTP service: `POST /ocr` with the raw image
bytes → `200` with a JSON object of structured **invoice** fields:

```json
{
  "vendor_id": "VND-001",
  "vendor_invoice_number": "NS-1002",
  "po_id": "PO-001",
  "invoice_date": "2026-11-22",
  "lines": [
    {"po_line_id": "PO-001-L1", "sku": "NOTE-A5", "qty": 60,
     "unit_price_pt": 2000, "line_total_pt": 120000}
  ],
  "subtotal_pt": 120000,
  "tax_pt": 16800,
  "total_pt": 136800,
  "layout": "A",
  "confidence": 94,
  "engine": "tesseract",
  "image_hash": "<sha256 of the received image bytes>"
}
```

All money is integer piastres. `lines[].line_total_pt` is the line total
(qty × unit price); `tax_pt` is the total tax on the invoice. A field the
OCR cannot read is `null` — the Engine treats that as `missing_field`
(review), never as zero. On extraction failure the service answers `500`
with `{"image_hash": "<sha256>", "error": "<message>", "engine": "tesseract"}`.
**`image_hash` (SHA-256 of the exact bytes received) is part of the
contract, on success AND error** — any replacement adapter must return it,
or the duplicate-image check cannot work. `GET /health` → `{"ok": true}`.

**Option A — local Tesseract (free, what the tests use):**
`invoice-ocr-service.py` (stdlib only, port 7998). Needs Tesseract
installed on the host (`apt-get install -y tesseract-ocr`, 5.3.4).
The service upscales images 2× (LANCZOS) before OCR — at native size
Tesseract systematically misreads `PO-001-L1` as `PO-0901-L1`; the
upscale fixes it completely. Run it where n8n can reach it and replace
`FILL_OCR_SERVICE_HOST` with that host.

**Option B — OCR.space (cloud):** an adapter with the same contract
(`POST` the image, return the same fields + `image_hash` computed over
the bytes you sent). Not built here; the node URL is the only change.

## 4. n8n hosting + webhook

- n8n 2.48.7, reachable on HTTPS (Telegram requires HTTPS webhooks).
  For a **free local test**, either:
  - `n8n start --tunnel` (free `hooks.n8n.cloud` URL, changes on restart,
    testing only), or
  - Cloudflare Tunnel: `cloudflared tunnel --url http://localhost:5678`
    (free trycloudflare URL), or ngrok free tier.
  Set `WEBHOOK_URL` to that public URL and restart n8n **before**
  activating the Telegram workflow, so the Trigger registers the right
  webhook. (Documented from public docs; not live-tested here — no real
  bot token was available during the build.)
- The Engine is called by the Telegram workflow over HTTP at
  `http://FILL_N8N_HOST:5678/webhook/invoice-matching` — replace
  `FILL_N8N_HOST` with the host n8n listens on (for Docker, the host's
  address as seen from the n8n container).
- **Docker reaching the local OCR service:** the service binds
  `0.0.0.0:7998` by default, so use `http://host.docker.internal:7998/ocr`
  (Docker Desktop, or Linux with
  `--add-host=host.docker.internal:host-gateway`), or run n8n with
  `--network host` and use localhost. Verify with
  `docker exec <n8n> curl http://host.docker.internal:7998/health`
  before activating. (Also documented, not live-tested here.)

## 5. Import

1. Import `invoice-telegram-workflow.json` and
   `invoice-matching-engine-workflow.json` into n8n (both are the **real**
   variants; the `-MOCK` files are test-only and must not be imported
   for the live run).
2. Fill the credentials: Google Sheets OAuth2 ("Google Sheets — fill on
   import") and Telegram ("Telegram Bot — fill on import").
3. Replace the placeholders (search the workflow JSON for `FILL_`):
   `FILL_SHEETS_ID`, `FILL_REVIEWER_TELEGRAM_ID`,
   `FILL_OCR_SERVICE_HOST`, `FILL_N8N_HOST`.
4. Update `DEMO_TODAY` in the builder (`invoice_extra.py`) to the live
   demo day and regenerate the workflows before importing, if the demo
   uses date-sensitive fixtures.
5. Activate the **Engine first**, then the **Telegram workflow**.
   (The Engine's webhook must be live before the Telegram workflow
   calls it.)

## 6. Live test (do this before the demo recording)

1. Send a clean invoice photo from the submitter account → expect
   `auto_matched` in Invoices + allocations in Allocations + a notice
   to the submitter.
2. Send a second invoice against the same PO line that completes it →
   expect the cumulative drawdown (received balance reaches 0).
3. Send a third invoice against the fully consumed line → expect
   `blocked / insufficient_received`, no allocation.
4. Send a small price-variance invoice → expect the review request to
   arrive in the **reviewer's** chat only (not the submitter's) →
   approve from the reviewer → expect `exception_approved` (visibly
   NOT `auto_matched`) + allocation.
5. **Two-account check:** attempt an approval from the submitter's
   account and from a third account → both blocked + audited, invoice
   unchanged.
6. Send a large-variance invoice → expect `blocked` → attempt approval
   → blocked + audited.
7. Resubmit the first photo → expect `duplicate_image`, no new row.
8. Send an invoice with no goods receipt → expect `pending_receipt` →
   add the receipt row in Sheets → run the Engine's Manual Trigger →
   expect `auto_matched`.
9. **Sheets values check:** open all 7 tabs and verify the actual
   written values — no leading/trailing spaces in any identifier
   (`invoice_id`, `po_line_id`, `vendor_id`, `allocation_id`,
   `audit_id`, `document_hash`), amounts in whole piastres, and the
   Audit_Log has an event for every transition including the blocked
   attempts.
10. Record the 90-second demo (see `demo-script.md`).

## 7. What is NOT covered by the live test above

- Real phone-photo OCR accuracy (the fixtures are clean renders; a
  crumpled, shadowed phone photo is unmeasured).
- Concurrent submissions (sequential demo only — see README).
- PDF or multi-page invoices (images only in v1).
