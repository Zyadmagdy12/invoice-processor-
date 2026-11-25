# Invoice Processor — Review Report

**Date:** 2026-11-22. **n8n target:** 2.48.7. **Status:** local tests pass
(53/53 twice). **Verdict on readiness:** NOT READY — see
`readiness-review.md`. This report records what was checked, what broke,
and what was fixed — with evidence, not claims.

## Design under review

- 2 workflows (Telegram unified + Matching Engine), 7 Sheets tabs, 16
  synthetic scenarios (frozen 15 + S16 round-4 regression), deterministic
  7-stage engine, integer piastres,
  zero automatic tolerance, price-review cap EGP 50 (per line **and**
  sum), tax mismatch Blocked, Engine owns all Sheets writes, approval
  only through the Engine in `apply_exception` mode, versioned
  correction, segregation of duties, sequential processing only.
- The frozen design is in `schema.md`; the approved plan in `plan.md`.

## Round-4 review fixes (2026-11-22)

An independent review of the delivered ZIP found one reproducible
engine defect and two Sheets integration risks, plus a documentation
error. All four were verified against the code before fixing, and each
has a regression test.

### 12. Engine: price-review return bypassed tax validation (confirmed defect)
`runStages()` returned `needs_review / price_variance_review` as soon
as the price variance was within cap — **before** Stage F ran. A
combined-discrepancy invoice (subtotal 5300, claimed tax 700, correct
742, variance 300pt) came back reviewable and could be
`exception_approved` with an active allocation. Reproduced with the
exact numbers before the fix (`/tmp/repro_tax_bypass.py` → `needs_review
/ price_variance_review`).
**Fix (engine only, one block moved):** the price-review return now sits
**after** Stage F, so every non-overridable validation (A, B, C,
D-over-cap, E, F) executes before a reviewable outcome is returned.
The same `runStages` feeds match and apply, so the apply path is fixed
too: revalidation at commit time now returns `blocked / tax_mismatch`
for such an invoice. **Regression:** new fixture inv-016 + S16 in the
probe (45/45) and the live suite (53/53): `blocked / tax_mismatch`, no
review request, no allocation, approval attempt blocked + audited.

### 13. Builder: Sheets expressions had a space after `=` (confirmed risk)
`columns_map` generated `"= {{ $json.row.… }}"` in all 36 real-Sheets
column mappings (4 write nodes). In n8n that evaluates to the value
with a leading space, so live Sheets identifiers would have been stored
padded (` INV-…`), breaking lookups. The mock path was unaffected (it
uses `={{ JSON.stringify(…) }}`).
**Fix (builder only):** `columns_map` now emits `"={{ $json.row.… }}"`.
Verified in the regenerated JSON: **0** occurrences of `"= {{` in any
workflow, **36** correct `={{ $json.row…` mappings. Live Sheets
verification of stored values remains a live-run step
(`setup-instructions.md` §6 step 9) — the mock cannot prove the real
Sheets node's expression handling.

### 14. Write failures were reported as success (confirmed risk)
The real Sheets write nodes used `continueOnFail: true`, and `Collect
Writes` returned `{ writes: n, ok: true }` without inspecting anything —
a failed invoice/allocation/audit write would have been reported as a
completed operation.
**Fix (three parts, all small):** (a) `Collect Writes` now inspects
every incoming item and throws on `error`, `statusCode >= 400`, or
`ok === false` (covers the mock HTTP error shapes); (b) the 4 real
Sheets write nodes no longer carry `continueOnFail`, so a live Sheets
failure aborts the execution at the node; (c) the Telegram workflow's
two Engine-call nodes (`Call Matching Engine`, `Call Engine (Apply)`)
no longer carry `continueOnFail`, so a failed Engine call fails the
Telegram execution visibly instead of producing an empty sender notice.
**Regression:** T-WRITEFAIL in the live suite — a `POST /fail-writes`
switch in the mock makes every Sheets write fail with 500; a direct
Engine call then answers HTTP ≥ 400, writes no invoice row and sends no
Telegram message; the same payload succeeds (`auto_matched`) once the
switch is off. Writing the test itself caught two payload mistakes (a
duplicate invoice number, then a fully consumed PO line) — both were
test bugs, fixed in the test, not engine bugs.

### 15. Docs: OCR setup described Sample 2's receipt contract; OCR bind was loopback-only
`setup-instructions.md` §3 listed payment-receipt fields (`amount_egp,
ref_number, …`). Rewritten with the actual invoice OCR contract
(`vendor_id, vendor_invoice_number, po_id, invoice_date, lines[],
subtotal_pt, tax_pt, total_pt, layout, confidence, engine,
image_hash`), including the `null`-means-unreadable rule and the 500
error shape. The OCR service bound `127.0.0.1` only, while §4 told
Docker users to reach it via `host.docker.internal` — unreachable as
written. **Fix:** the service now binds `0.0.0.0:7998` (verified:
`/health` answers and a real `/ocr` call on inv-002 returns the full
contract with `image_hash`). Docker itself was not tested here (no
Docker in this VM).

## Evidence

| Check | Result | Evidence |
|---|---|---|
| Engine probe (pure logic, in-memory tables) | **45/45** | `probe_engine.py` → `engine-probe-results.json` |
| Real Tesseract accuracy (16 fixtures, every field) | **202/202** (conf 93–95) | `ocr-accuracy-test.py` → `ocr-accuracy-results.json` |
| Full n8n suite, run 1 | **53/53** | `run_invoice_tests.py run1` → `invoice-test-results-run1.json` |
| Full n8n suite, run 2 | **53/53** (identical check list) | `run_invoice_tests.py run2` → `invoice-test-results-run2.json` |
| Real-workflow export round-trip | **0 parameter differences, connections equal** (Engine 38 nodes, Telegram 26 nodes) | `export_check_invoice.py` → `exported-from-n8n/` |
| Real OCR intake end-to-end (T-OCR) | pass (auto_matched via the real Tesseract service) | part of the 53/53 suite |

## Defects found by executing (and fixed)

### 1. Engine: allocations missing on the approve path
The first probe run was 33/42. `runStages` computed allocations only for
the auto-match outcome, so the approve path had nothing to write — an
approved exception would have finalized `exception_approved` without
consuming quantity. **Fix (engine only):** compute allocations right
after Stage E passes and return them with the price-review outcome too;
match/revalidate modes write them only when status is `auto_matched`,
the apply path consumes them on approval. Proven by the probe (45/45)
and by S05/S13 in the live suite (allocation recorded after approval).

### 2. Engine: missing receipt returned the wrong disposition
S10 returned `blocked / insufficient_received` instead of
`pending_receipt / missing_receipt`. **Fix:** Stage E now checks
`received == 0` before the quantity checks — an invoice for unreceived
goods parks, it is not a quantity violation. Proven by S10.

### 3. Engine: `duplicate_image` output lacked its marker
The output was missing `no_new_row: true`, so a caller could not
distinguish "no row created" from a dropped result. **Fix:** added.
Proven by S08.

### 4. Engine: segregation-of-duties check was unreachable
The Apply check order was stale → auth → submitter → …, and since the
submitter is never the reviewer, the auth check fired first and the
submitter case was never reported as such. **Fix:** check order is now
stale → **submitter** → auth → repeat → approvable. `schema.md` updated.
Proven by S13 (submitter blocked with `review_blocked_submitter`,
distinct from `review_blocked_auth`).

### 5. Engine: correction snapshot taken too late (design flaw)
The first design snapshotted the pre-correction values at revalidate
time — but by then the operator has already edited the rows, so the old
values are gone. **Fix:** the Engine snapshots the values into the
`correction_requested` audit event **at request time**, before any edit;
`correction_accepted` references that snapshot. Proven by S12/S14
(the S12 snapshot contains the old value "6000").

### 6. OCR: Tesseract misreads PO line ids at native size
At native resolution Tesseract systematically read `PO-001-L1` as
`PO-0901-L1` (and `PO-002-L1` as `PO-0902-L1`); `--psm 6` did not fix
it. **Fix:** 2× LANCZOS upscale before OCR, built into `extract()`.
Accuracy re-measured: 202/202. (The mock OCR misread in S12 is a
simulated error, not this bug — real Tesseract reads inv-012 correctly.)

### 7. Webhook response: `responseMode: lastNode` is not reliable for a branched workflow (the main defect of the build)
The first live suite run returned `53/53` on the tables but the Engine
webhook answered `500 "No item to return was found"` — the Telegram
workflow's engine calls got empty responses, so review/notify messages
arrived empty (`Invoice : `). Root cause, established by reading n8n's
own code (`webhook-last-node-response-extractor.js`,
`sub-workflow-output.js`, `workflow-execute.js`) and by a minimal
Merge probe: with `lastNode`, the webhook returns the output of the
node n8n records as last-executed, which in a multi-branch tail is not
necessarily the response builder. The write nodes' outputs are the
mock/Sheets responses and carry none of the engine's fields, so the
response cannot be rebuilt from them.
**Fix (builders only, three parts):**
1. **Collect Writes** (Code node, `runOnceForAllItems`) gathers all five
   write branches into one item — without it, the downstream Merge can
   fire on the first write wave while other branches are still running.
2. **Merge Engine Output** pairs that one item with the Matching
   Engine's own item, so the response builder reads the engine fields.
3. **Respond to Webhook** node after Build Response, with the trigger's
   `responseMode` changed to `responseNode` — the response is sent
   explicitly when the node runs, after every write has landed.
   Direct check after the fix: `POST /webhook/invoice-matching` →
   `200` with the full flat response. The full suite then passed
   53/53 twice, including the review-message-to-reviewer-only check
   that had failed before. In manual (revalidate) runs the Respond node
   is a harmless pass-through (verified: S10's manual batch revalidate
   passes).

### 8. Runner: invoice row lands before allocation rows
The S01 check read the Allocations table immediately after the invoice
row appeared and found `[]` — the Upsert branch and the Append
Allocations branch run in parallel. **Fix (runner only):** the
allocation checks now poll (`wait_allocations`, `wait_consumed`) instead
of reading once. Not an engine defect: the engine's writes all complete
before its webhook response; the runner was reading the tables directly.

### 9. Runner: n8n REST responses are wrapped in `data`
The first import crashed with `KeyError: 'id'` — create/list responses
wrap the object in `data`. **Fix:** an `unwrap()` helper in the runner.
(Same trap as Samples 1–2; recorded here so it is not re-learned.)

### 10. Runner: webhook conflict (409) on re-activation
Re-importing workflows with the same webhook paths collided with the
previous run's still-active copies, and deactivation lags the API call.
**Fix:** the runner lists **all** workflows (paginated — the rig holds
138 workflows, one page is not enough), deactivates stale copies by
name, and sleeps 2s before activating.

### 11. Builder: duplicate node name
n8n rejected the Telegram MOCK workflow: two nodes named
"Send Reason Keyboard (Mock)". **Fix:** renamed to "Send Approve
Reasons (Mock)" / "Send Reject Reasons (Mock)".

## What was deliberately NOT changed

- No concurrency machinery (sequential demo is the documented limit).
- No PDF, no multi-currency, no discounts, no Schedule Trigger.
- No LLM anywhere in the decision path.
- The correction mechanism stayed Sheets-edit + revalidate (no new
  Form workflow).
- The known limitation from Sample 2's pattern is inherited and
  documented: a forged callback (knowing a receipt/invoice id + the
  webhook URL) still reaches the reason picker before Apply Decision
  blocks it. The decision itself is blocked + audited (S13, T-OCR
  path); moving the check earlier was deliberately not done to keep
  scope.

## What is NOT proven (see readiness-review.md)

- Live run against a real Telegram bot, real Google Sheets, real OCR
  hosting, real n8n hosting — Blocked by Credentials.
- Real phone-photo OCR accuracy (fixtures are clean renders).
- Concurrent submissions.
- The free-HTTPS-webhook and Docker-OCR setup steps (documented from
  public docs, not live-tested here).
