# Demo Script — Invoice Processor (90 seconds)

**Setup:** n8n open on the two workflows; Telegram open on the submitter
and reviewer accounts; the Sheets spreadsheet open on Allocations.
All data synthetic, watermarked as such.

## Beat 1 — Clean match, cumulative drawdown (0:00–0:30)

1. Submitter sends the NOTE-A5 invoice (60 units) on Telegram.
   Say: *"Sixty notebooks against a hundred received — a valid partial."*
2. Show the notice: `auto_matched / partial_match`, and the Allocations
   tab: NOTE-A5 consumed 60/100.
3. Submitter sends the second NOTE-A5 invoice (40 units).
   Say: *"Forty more — the line is now complete."*
4. Show Allocations: NOTE-A5 consumed 100/100.

## Beat 2 — A discrepancy goes to a human (0:30–0:60)

5. Submitter sends the PEN-BLUE invoice (10 × 5.30 vs PO 5.00 — a 3 EGP
   variance, inside the 50 EGP policy).
   Say: *"Three pounds over the PO price. The system does not auto-match
   it — it asks a human."*
6. Show the review request arriving in the **reviewer's** chat only,
   with Approve / Reject buttons. Reviewer taps Approve → picks
   "Price within EGP 50 policy".
7. Show Invoices: `exception_approved` — visibly **not** `auto_matched` —
   and the allocation of 10 PEN-BLUE recorded.

## Beat 3 — A bypass attempt is blocked (0:60–0:90)

8. Submitter sends a third NOTE-A5 invoice (50 units) after the line is
   fully consumed.
   Say: *"Fifty more notebooks — but the received balance is zero."*
9. Show Invoices: `blocked / insufficient_received`, no allocation.
10. Show the Audit_Log: the blocked attempt is recorded with reason,
    decider and time.
    Say: *"Every decision — including the blocked ones — is in the audit
    log. That is the whole sample."*
