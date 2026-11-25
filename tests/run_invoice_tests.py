#!/usr/bin/env python3
"""End-to-end tests for Sample 3 in real n8n 2.48.7.
Mock Sheets / Telegram / OCR on port 7999; real Tesseract OCR on 7998.
The runner drives the Telegram-MOCK workflow (photos + callbacks) and
asserts on the mock's /log state. Run twice for stability.
Usage: python3 run_invoice_tests.py [run1|run2]
Writes isbat-style results to invoice-test-results.json.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

N8N = "http://127.0.0.1:5678"
MOCK = "http://127.0.0.1:7999"
BASE = os.path.dirname(os.path.abspath(__file__))
SENDER = "222222"
REVIEWER = "111111"
TG_URL = N8N + "/webhook/invoice-telegram"
TG_REALOCR_URL = N8N + "/webhook/invoice-telegram-realocr"
ENG_URL = N8N + "/webhook/invoice-matching"

_cookie = None


def req(method, url, body=None, headers=None):
    global _cookie
    h = {"Content-Type": "application/json"}
    if _cookie:
        h["Cookie"] = _cookie
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def n8n_login():
    global _cookie
    import http.cookiejar
    # Login via urllib with a cookie jar (the n8n-auth cookie must be sent
    # back as an explicit Cookie header -- see Sample 1/2 rig notes).
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    data = json.dumps({"emailOrLdapLoginId": "test@example.local",
                       "password": "TestLocal123!"}).encode()
    r = urllib.request.Request(N8N + "/rest/login", data=data,
                               headers={"Content-Type": "application/json"}, method="POST")
    opener.open(r, timeout=30)
    _cookie = "; ".join(c.name + "=" + c.value for c in cj)
    assert _cookie, "login failed (no cookie)"
    print("n8n login ok")


def unwrap(body):
    d = json.loads(body)
    return d.get("data", d) if isinstance(d, dict) else d


def n8n_import(path):
    wf = json.load(open(os.path.join(BASE, path)))
    wf["active"] = False
    st, body = req("POST", N8N + "/rest/workflows", wf)
    assert st in (200, 201), f"import {path} -> {st} {body[:300]}"
    d = unwrap(body)
    return d["id"], d.get("versionId")


def n8n_get_workflow(wid):
    st, body = req("GET", N8N + f"/rest/workflows/{wid}")
    assert st == 200, f"get workflow {wid} -> {st}"
    return unwrap(body)


def n8n_list():
    # Paginated: the rig holds 100+ stale workflows, so one page is not enough.
    out = []
    skip = 0
    while True:
        st, body = req("GET", N8N + f"/rest/workflows?take=100&skip={skip}")
        assert st == 200, f"list workflows -> {st}"
        d = unwrap(body)
        rows = d if isinstance(d, list) else d.get("results", [])
        out.extend(rows)
        if len(rows) < 100:
            break
        skip += 100
    return out


def n8n_deactivate(wid):
    st, _ = req("POST", N8N + f"/rest/workflows/{wid}/deactivate", {})
    return st == 200


def deactivate_stale(keep_ids):
    names = {"Invoice Matching Engine (Sample 3)", "Invoice Processor — Telegram (Sample 3)"}
    for w in n8n_list():
        if w.get("name") in names and w.get("id") not in keep_ids and w.get("active"):
            if n8n_deactivate(w["id"]):
                print("deactivated stale", w["id"], w["name"])


def n8n_activate(wid):
    d = n8n_get_workflow(wid)
    vid = d.get("versionId")
    st, body = req("POST", N8N + f"/rest/workflows/{wid}/activate", {"versionId": vid})
    assert st == 200, f"activate {wid} -> {st} {body[:300]}"
    print("activated", wid)


def n8n_run_manual(wid, trigger_name):
    st, body = req("POST", N8N + f"/rest/workflows/{wid}/run",
                   {"triggerToStartFrom": {"name": trigger_name}})
    assert st in (200, 201), f"manual run {wid} -> {st} {body[:300]}"
    return unwrap(body) if body else {}


def mock_reset():
    st, _ = req("POST", MOCK + "/reset", {})
    assert st == 200


def mock_log():
    st, body = req("GET", MOCK + "/log")
    assert st == 200
    return json.loads(body)


def mock_post(path, body):
    st, b = req("POST", MOCK + path, body)
    assert st == 200, f"{path} -> {st} {b[:200]}"
    return json.loads(b)


def wait_for(fn, timeout=40, desc=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        lg = mock_log()
        v = fn(lg)
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError("timeout waiting for: " + desc)


def fixtures():
    fx = json.load(open(os.path.join(BASE, "fixtures.json")))
    return {v["name"]: (k, v) for k, v in fx.items()}


FX = fixtures()


def invoice_id(name):
    h, _ = FX[name]
    return "INV-" + h[:8]


def inv_row(lg, iid):
    return next((r for r in lg["sheets"]["Invoices"] if r["invoice_id"] == iid), None)


def consumed(lg, po_line_id):
    return sum(r["qty"] for r in lg["sheets"]["Allocations"]
               if r["po_line_id"] == po_line_id and r["status"] == "active")


_uid = [0]


def submit_photo(name, from_id=SENDER, url=TG_URL):
    _uid[0] += 1
    upd = {"update_id": 1000 + _uid[0],
           "message": {"message_id": _uid[0], "from": {"id": int(from_id), "first_name": "Test"},
                       "chat": {"id": int(from_id), "type": "private"},
                       "date": 1793000000,
                       "photo": [{"file_id": f"fixture:{name}", "file_unique_id": "u1",
                                  "width": 100, "height": 100}]}}
    st, body = req("POST", url, upd)
    assert st == 200, f"submit {name} -> {st} {body[:200]}"


def send_callback(data, from_id, chat_id=None):
    _uid[0] += 1
    upd = {"update_id": 2000 + _uid[0],
           "callback_query": {"id": str(_uid[0]), "from": {"id": int(from_id), "first_name": "Test"},
                              "message": {"message_id": 1, "chat": {"id": int(chat_id or from_id), "type": "private"}},
                              "data": data}}
    st, body = req("POST", TG_URL, upd)
    assert st == 200, f"callback {data} -> {st} {body[:200]}"


def tg_count(lg):
    return len(lg["telegram"])


def decide(iid, version, review_id, action, reason_key, from_id, chat_id=None):
    """Drive one review decision through the Telegram callbacks.
    approve/reject go through the reason keyboard; correct/defer are direct."""
    lg0 = mock_log()
    n0 = tg_count(lg0)
    send_callback(f"{action}:{iid}:{version}:{review_id}", from_id, chat_id)
    if action in ("approve", "reject"):
        wait_for(lambda lg: tg_count(lg) > n0, desc=f"reason keyboard for {action}")
        send_callback(f"reason:{action}:{reason_key}:{iid}:{version}:{review_id}", from_id, chat_id)


def engine_revalidate(iid, requested_by=REVIEWER):
    st, body = req("POST", ENG_URL, {"mode": "revalidate", "invoice_id": iid, "requested_by": requested_by})
    assert st == 200, f"engine revalidate -> {st} {body[:200]}"
    return json.loads(body)


def main():
    run_tag = sys.argv[1] if len(sys.argv) > 1 else "run1"
    checks = []

    def check(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else " — " + str(detail)[:400]))

    n8n_login()
    mock_reset()
    eng_id, _ = n8n_import("invoice-matching-engine-workflow-MOCK.json")
    tg_id, _ = n8n_import("invoice-telegram-workflow-MOCK.json")
    tg_ro_id, _ = n8n_import("invoice-telegram-workflow-MOCK-REALOCR.json")
    # Same webhook paths as previous runs: deactivate stale copies first.
    # Webhook deregistration lags the deactivate call, so wait before
    # activating or n8n answers 409 (webhook conflict).
    deactivate_stale({eng_id, tg_id, tg_ro_id})
    time.sleep(2)
    n8n_activate(eng_id)
    n8n_activate(tg_id)
    n8n_activate(tg_ro_id)
    time.sleep(3)  # webhook registration race

    def wait_invoice(iid, status=None, reason=None, timeout=40):
        def fn(lg):
            r = inv_row(lg, iid)
            if not r:
                return None
            if status and r["status"] != status:
                return None
            if reason and r["reason_code"] != reason:
                return None
            return r
        return wait_for(fn, timeout=timeout, desc=f"invoice {iid} -> {status}/{reason}")

    def wait_audit(iid, reason, timeout=30):
        def fn(lg):
            return next((a for a in lg["sheets"]["Audit_Log"]
                         if a["invoice_id"] == iid and a["reason_code"] == reason), None)
        return wait_for(fn, timeout=timeout, desc=f"audit {iid}/{reason}")

    def wait_allocations(iid, n, timeout=30):
        # The invoice row (Upsert branch) can land before the allocation
        # appends (parallel branch), so poll instead of reading once.
        def fn(lg):
            c = sum(1 for a in lg["sheets"]["Allocations"] if a["invoice_id"] == iid)
            return c if c >= n else None
        return wait_for(fn, timeout=timeout, desc=f"allocations {iid} >= {n}")

    def wait_consumed(po_line_id, qty, timeout=30):
        def fn(lg):
            c = consumed(lg, po_line_id)
            return c if c >= qty else None
        return wait_for(fn, timeout=timeout, desc=f"consumed {po_line_id} >= {qty}")

    # ---- S01 clean match (mixed tax) ----
    submit_photo("inv-001")
    r = wait_invoice(invoice_id("inv-001"), "auto_matched", "exact_match")
    check("S01 auto_matched/exact_match", r is not None, r)
    n_alloc = wait_allocations(invoice_id("inv-001"), 2)
    check("S01 two allocations (TONER + CABLE)", n_alloc == 2, n_alloc)
    # ---- S02 partial ----
    submit_photo("inv-002")
    r = wait_invoice(invoice_id("inv-002"), "auto_matched", "partial_match")
    check("S02 auto_matched/partial_match", r is not None, r)
    # ---- S03 completes the PO line ----
    submit_photo("inv-003")
    r = wait_invoice(invoice_id("inv-003"), "auto_matched")
    check("S03 auto_matched", r is not None, r)
    c = wait_consumed("PO-001-L1", 100)
    check("S03 NOTE-A5 consumed 100", c == 100, c)
    # ---- S04 third invoice after full consumption ----
    submit_photo("inv-004")
    r = wait_invoice(invoice_id("inv-004"), "blocked", "insufficient_received")
    check("S04 blocked/insufficient_received", r is not None, r)
    lg = mock_log()
    check("S04 no allocation", not any(a["invoice_id"] == invoice_id("inv-004")
          for a in lg["sheets"]["Allocations"]))
    # ---- S05 small variance -> review -> approve ----
    submit_photo("inv-005")
    r = wait_invoice(invoice_id("inv-005"), "needs_review", "price_variance_review")
    check("S05 needs_review/price_variance_review", r is not None, r)
    lg = mock_log()
    rev_msgs = [m for m in lg["telegram"] if m["chat_id"] == REVIEWER and "NS-1005" in m["text"]
                and m.get("reply_markup")]
    sender_buttons = [m for m in lg["telegram"] if m["chat_id"] == SENDER and m.get("reply_markup")]
    check("S05 review request (buttons) to reviewer only",
          len(rev_msgs) == 1 and len(sender_buttons) == 0,
          {"reviewer_msgs": rev_msgs, "sender_buttons": sender_buttons})
    decide(invoice_id("inv-005"), 1, r["current_review_id"], "approve", "price_within_policy", REVIEWER)
    r = wait_invoice(invoice_id("inv-005"), "exception_approved", "manual_approve")
    check("S05 approve -> exception_approved", r is not None, r)
    lg = mock_log()
    check("S05 allocation recorded (10 PEN-BLUE)", any(
        a["invoice_id"] == invoice_id("inv-005") and a["qty"] == 10 for a in lg["sheets"]["Allocations"]))
    # ---- S06a large variance -> blocked; approval attempt blocked ----
    submit_photo("inv-006")
    r = wait_invoice(invoice_id("inv-006"), "blocked", "price_variance_blocked")
    check("S06a blocked/price_variance_blocked", r is not None, r)
    lg0 = mock_log()
    n_audit = len(lg0["sheets"]["Audit_Log"])
    decide(invoice_id("inv-006"), 1, "", "approve", "price_within_policy", REVIEWER)
    a = wait_for(lambda lg: len(lg["sheets"]["Audit_Log"]) > n_audit, desc="S06a blocked audit")
    lg = mock_log()
    blk = next((x for x in lg["sheets"]["Audit_Log"][n_audit:]
                if x["invoice_id"] == invoice_id("inv-006") and x["reason_code"].startswith("review_blocked")), None)
    check("S06a approval attempt blocked + audited", blk is not None, blk)
    check("S06a still blocked, no allocation", inv_row(lg, invoice_id("inv-006"))["status"] == "blocked"
          and not any(x["invoice_id"] == invoice_id("inv-006") for x in lg["sheets"]["Allocations"]))
    # ---- S06b sum cap ----
    submit_photo("inv-007")
    r = wait_invoice(invoice_id("inv-007"), "blocked", "price_variance_blocked")
    check("S06b blocked/price_variance_blocked (sum 5100)", r is not None, r)
    # ---- S07 duplicate number ----
    submit_photo("inv-008")
    r = wait_invoice(invoice_id("inv-008"), "blocked", "duplicate_invoice")
    check("S07 blocked/duplicate_invoice", r is not None, r)
    # ---- S08 same image again ----
    lg0 = mock_log()
    n_inv = len(lg0["sheets"]["Invoices"])
    submit_photo("inv-001")
    wait_audit(invoice_id("inv-001"), "duplicate_image")
    lg = mock_log()
    check("S08 duplicate_image, no new row", len(lg["sheets"]["Invoices"]) == n_inv,
          (n_inv, len(lg["sheets"]["Invoices"])))
    # ---- S09 unknown PO ----
    submit_photo("inv-009")
    r = wait_invoice(invoice_id("inv-009"), "blocked", "unknown_po")
    check("S09 blocked/unknown_po", r is not None, r)
    # ---- S10 missing receipt -> pending -> add receipt -> manual batch -> matched ----
    submit_photo("inv-010")
    r = wait_invoice(invoice_id("inv-010"), "pending_receipt", "missing_receipt")
    check("S10 pending_receipt/missing_receipt", r is not None, r)
    mock_post("/seed/receipt", {"receipt_id": "RCV-006", "po_line_id": "PO-004-L1",
                                "qty_accepted": 20, "received_date": "2026-11-22", "status": "active"})
    n8n_run_manual(eng_id, "Manual Revalidate")
    r = wait_invoice(invoice_id("inv-010"), "auto_matched")
    check("S10 manual batch revalidate -> auto_matched", r is not None, r)
    lg = mock_log()
    check("S10 STAPLER consumed 20", consumed(lg, "PO-004-L1") == 20, consumed(lg, "PO-004-L1"))
    # manual batch with nothing pending: no-op
    lg0 = mock_log()
    n_audit = len(lg0["sheets"]["Audit_Log"])
    n8n_run_manual(eng_id, "Manual Revalidate")
    time.sleep(5)
    lg = mock_log()
    check("T-batch empty run is a no-op", len(lg["sheets"]["Audit_Log"]) == n_audit,
          (n_audit, len(lg["sheets"]["Audit_Log"])))
    # ---- S11 tax mismatch ----
    submit_photo("inv-011")
    r = wait_invoice(invoice_id("inv-011"), "blocked", "tax_mismatch")
    check("S11 blocked/tax_mismatch", r is not None, r)
    # ---- S12 OCR misread -> correction -> revalidate v2 -> matched ----
    submit_photo("inv-012")
    r = wait_invoice(invoice_id("inv-012"), "needs_review", "arithmetic_mismatch")
    check("S12 needs_review/arithmetic_mismatch", r is not None, r)
    decide(invoice_id("inv-012"), 1, r["current_review_id"], "correct", "", REVIEWER)
    r = wait_invoice(invoice_id("inv-012"), "correction_required", "correction_requested")
    check("S12 correction_required", r is not None, r)
    lg = mock_log()
    snap = next((a for a in lg["sheets"]["Audit_Log"] if a["invoice_id"] == invoice_id("inv-012")
                 and a["reason_code"] == "correction_requested"), None)
    check("S12 old values snapshotted (6000)", snap is not None and "6000" in snap["note"], snap)
    mock_post("/sheets/correct", {"invoice_id": invoice_id("inv-012"), "lines": [{"line_no": 1, "line_total_piastres": 5000}]})
    engine_revalidate(invoice_id("inv-012"))
    r = wait_invoice(invoice_id("inv-012"), "auto_matched")
    check("S12 revalidate v2 -> auto_matched", r is not None and r["version"] == 2, r)
    # ---- S13 unauthorized + SoD + reviewer approval ----
    submit_photo("inv-013")
    r = wait_invoice(invoice_id("inv-013"), "needs_review", "price_variance_review")
    check("S13 needs_review", r is not None, r)
    rev13 = r["current_review_id"]
    lg0 = mock_log()
    n_audit = len(lg0["sheets"]["Audit_Log"])
    decide(invoice_id("inv-013"), 1, rev13, "approve", "price_within_policy", "999999")
    a = wait_audit(invoice_id("inv-013"), "review_blocked_auth")
    check("S13 wrong user blocked (auth)", a is not None, a)
    decide(invoice_id("inv-013"), 1, rev13, "approve", "price_within_policy", SENDER)
    a = wait_audit(invoice_id("inv-013"), "review_blocked_submitter")
    check("S13 submitter blocked (SoD)", a is not None, a)
    lg = mock_log()
    check("S13 still needs_review, no allocation yet", inv_row(lg, invoice_id("inv-013"))["status"] == "needs_review"
          and not any(x["invoice_id"] == invoice_id("inv-013") for x in lg["sheets"]["Allocations"]))
    decide(invoice_id("inv-013"), 1, rev13, "approve", "price_within_policy", REVIEWER)
    r = wait_invoice(invoice_id("inv-013"), "exception_approved", "manual_approve")
    check("S13 reviewer approve -> exception_approved", r is not None, r)
    # ---- S14 correction + replay stale ----
    submit_photo("inv-014")
    r = wait_invoice(invoice_id("inv-014"), "needs_review", "price_variance_review")
    check("S14 needs_review", r is not None, r)
    rev14 = r["current_review_id"]
    decide(invoice_id("inv-014"), 1, rev14, "correct", "", REVIEWER)
    r = wait_invoice(invoice_id("inv-014"), "correction_required")
    check("S14 correction_required", r is not None, r)
    mock_post("/sheets/correct", {"invoice_id": invoice_id("inv-014"),
                                  "invoice_fields": {"subtotal_piastres": 2500, "tax_piastres": 350, "total_piastres": 2850},
                                  "lines": [{"line_no": 1, "unit_price_piastres": 500, "line_total_piastres": 2500}]})
    engine_revalidate(invoice_id("inv-014"))
    r = wait_invoice(invoice_id("inv-014"), "auto_matched")
    check("S14 revalidate v2 -> auto_matched", r is not None and r["version"] == 2, r)
    decide(invoice_id("inv-014"), 1, rev14, "approve", "price_within_policy", REVIEWER)
    a = wait_audit(invoice_id("inv-014"), "review_blocked_stale")
    check("S14 replay v1 approve blocked (stale)", a is not None, a)
    lg = mock_log()
    check("S14 still auto_matched", inv_row(lg, invoice_id("inv-014"))["status"] == "auto_matched")
    # ---- S15 float trap ----
    submit_photo("inv-015")
    r = wait_invoice(invoice_id("inv-015"), "auto_matched", "exact_match")
    check("S15 auto_matched/exact_match (float trap)", r is not None, r)
    # ---- S16 combined price variance (within cap) + tax mismatch -> blocked ----
    # Round-4 regression (review item 1): variance 300pt is reviewable, but
    # claimed tax 700 vs correct 742 must block -- never a reviewable exception.
    submit_photo("inv-016")
    r = wait_invoice(invoice_id("inv-016"), "blocked", "tax_mismatch")
    check("S16 blocked/tax_mismatch (variance within cap + wrong tax)", r is not None, r)
    lg = mock_log()
    check("S16 no review request sent",
          not any("NS-1016" in (m.get("text") or "") and m.get("reply_markup")
                  for m in lg["telegram"]))
    check("S16 no allocation", not any(
        a["invoice_id"] == invoice_id("inv-016") for a in lg["sheets"]["Allocations"]))
    lg0 = mock_log()
    n_audit = len(lg0["sheets"]["Audit_Log"])
    decide(invoice_id("inv-016"), 1, "", "approve", "price_within_policy", REVIEWER)
    a = wait_for(lambda lg: len(lg["sheets"]["Audit_Log"]) > n_audit, desc="S16 blocked audit")
    lg = mock_log()
    blk = next((x for x in lg["sheets"]["Audit_Log"][n_audit:]
                if x["invoice_id"] == invoice_id("inv-016") and x["reason_code"].startswith("review_blocked")), None)
    check("S16 approval attempt blocked + audited", blk is not None, blk)
    check("S16 still blocked, no allocation", inv_row(lg, invoice_id("inv-016"))["status"] == "blocked"
          and not any(x["invoice_id"] == invoice_id("inv-016") for x in lg["sheets"]["Allocations"]))
    # ---- final invariants ----
    lg = mock_log()
    auto = [x for x in lg["sheets"]["Invoices"] if x["status"] == "auto_matched"]
    exc = [x for x in lg["sheets"]["Invoices"] if x["status"] == "exception_approved"]
    check("FINAL 7 auto_matched", len(auto) == 7, [x["invoice_id"] for x in auto])
    check("FINAL 2 exception_approved", len(exc) == 2, [x["invoice_id"] for x in exc])
    check("FINAL PEN-BLUE consumed 35/50", consumed(lg, "PO-001-L2") == 35, consumed(lg, "PO-001-L2"))
    check("FINAL every invoice audited", all(
        any(a["invoice_id"] == x["invoice_id"] for a in lg["sheets"]["Audit_Log"])
        for x in lg["sheets"]["Invoices"]))
    check("FINAL no over-consumption", all(
        consumed(lg, pl) <= sum(r["qty_accepted"] for r in lg["sheets"]["Receipt_Lines"]
                                if r["po_line_id"] == pl)
        for pl in ["PO-001-L1", "PO-001-L2", "PO-002-L1", "PO-002-L2", "PO-003-L1", "PO-004-L1"]))
    # ---- T-WRITEFAIL: a failed Sheets write must fail visibly (review item 3) ----
    # Runs AFTER the final invariants: the successful retry below writes one
    # extra auto_matched invoice + allocation, which the T-OCR reset wipes.
    # NOTE: the payload is a fresh PEN-BLUE invoice (10 x 500, tax correct).
    # It must be clean AND consumable at this point in the suite: NOTE-A5,
    # TONER-X, CABLE-USB, PAPER-A4 and STAPLER are all fully consumed by
    # the scenarios, only PEN-BLUE still has balance (15 of 50). The
    # invoice number NS-9001 is fresh so Stage B's (vendor, number)
    # duplicate check does not trip on S02's NS-1002.
    wf_payload = {"mode": "match",
                  "invoice": {"vendor_id": "VND-001",
                              "vendor_invoice_number": "NS-9001",
                              "po_id": "PO-001", "invoice_date": "2026-11-22",
                              "subtotal_pt": 5000, "tax_pt": 700,
                              "total_pt": 5700,
                              "document_hash": "writefailtest", "submitter_id": SENDER},
                  "lines": [{"po_line_id": "PO-001-L2", "sku": "PEN-BLUE", "qty": 10,
                             "unit_price_pt": 500, "line_total_pt": 5000}]}
    mock_post("/fail-writes", {"enabled": True})
    n_tg = tg_count(mock_log())
    st, body = req("POST", ENG_URL, wf_payload)
    check("T-WRITEFAIL engine answers error on write failure",
          st >= 400 and "auto_matched" not in body, {"status": st, "body": body[:300]})
    lg = mock_log()
    check("T-WRITEFAIL no invoice row written for the failed call",
          inv_row(lg, "INV-writefai") is None)
    check("T-WRITEFAIL no Telegram message sent for the failed call",
          len(lg["telegram"]) == n_tg, len(lg["telegram"]) - n_tg)
    mock_post("/fail-writes", {"enabled": False})
    st, body = req("POST", ENG_URL, wf_payload)
    ok2 = st == 200 and json.loads(body).get("status") == "auto_matched"
    check("T-WRITEFAIL same payload succeeds after failures off", ok2,
          {"status": st, "body": body[:300]})
    # ---- T-OCR: real Tesseract path through the REALOCR workflow ----
    mock_reset()
    submit_photo("inv-002", url=TG_REALOCR_URL)
    r = wait_invoice(invoice_id("inv-002"), "auto_matched", "partial_match")
    check("T-OCR real Tesseract intake -> auto_matched", r is not None, r)

    failed = [c for c in checks if not c["ok"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} n8n checks passed ({run_tag})")
    json.dump({"run": run_tag, "passed": len(checks) - len(failed), "total": len(checks),
               "checks": checks},
              open(os.path.join(BASE, f"invoice-test-results-{run_tag}.json"), "w"), indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
