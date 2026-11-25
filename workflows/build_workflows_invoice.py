#!/usr/bin/env python3
"""Build the 2 Invoice Processor workflows (real + MOCK variants).
Emits: invoice-matching-engine-workflow.json (+ -MOCK),
        invoice-telegram-workflow.json (+ -MOCK, -MOCK-REALOCR).
n8n target: 2.48.7. Builders are the single source of truth -- never
hand-edit the generated JSON.
"""
import json
import pathlib

from invoice_extra import (
    COLS, build_engine_js, build_review_msg_js,
    REVIEWER_ID_MOCK, REVIEWER_ID_REAL,
    ROUTE_UPDATE_JS, PARSE_CALLBACK_JS, BUILD_REASON_MSG_JS,
    PARSE_ENGINE_CALL_JS, FILTER_CANDIDATES_JS, EXPAND_WRITES_JS,
    COLLECT_WRITES_JS, BUILD_RESPONSE_JS, PICK_PHOTO_JS, BUILD_ENGINE_PAYLOAD_JS,
    BUILD_APPLY_CALL_JS, BUILD_NOTIFY_JS, aggregate_js,
)

BASE = pathlib.Path(__file__).parent
MOCK = "http://127.0.0.1:7999"
OCR_MOCK_URL = MOCK + "/ocr"
OCR_LOCAL_URL = "http://127.0.0.1:7998/ocr"
OCR_REAL_URL = "http://FILL_OCR_SERVICE_HOST:7998/ocr"
ENGINE_MOCK_URL = "http://127.0.0.1:5678/webhook/invoice-matching"
ENGINE_REAL_URL = "http://FILL_N8N_HOST:5678/webhook/invoice-matching"
SHEETS_URL = "https://docs.google.com/spreadsheets/d/FILL_SHEETS_ID/edit"
TABLES = ["Vendors", "PO_Lines", "Receipt_Lines", "Invoices", "Invoice_Lines", "Allocations"]


def node(id_, name, type_, params, pos, creds=None, version=2, extra=None):
    n = {"id": id_, "name": name, "parameters": params, "position": list(pos),
         "type": type_, "typeVersion": version}
    if creds:
        n["credentials"] = creds
    if extra:
        n.update(extra)
    return n


SHEETS_CREDS = {"googleSheetsOAuth2": {"id": None, "name": "Google Sheets — fill on import"}}
TG_CREDS = {"telegramApi": {"id": None, "name": "Telegram Bot — fill on import"}}


def code_node(id_, name, js, pos):
    return node(id_, name, "n8n-nodes-base.code",
                {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": js},
                pos, version=2)


def merge_node(id_, name, pos):
    return node(id_, name, "n8n-nodes-base.merge",
                {"mode": "combine", "combineBy": "combineByPosition", "numberInputs": 2, "options": {}},
                pos, version=3)


def if_node(id_, name, conditions, pos, combinator="and"):
    conds = [{"id": "cond-%s-%d" % (id_, i), "leftValue": left, "rightValue": right,
              "operator": {"type": ot, "operation": op, "name": opn}}
             for i, (left, right, ot, op, opn) in enumerate(conditions)]
    return node(id_, name, "n8n-nodes-base.if",
                {"conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": conds, "combinator": combinator},
                 "looseTypeValidation": True},
                pos, version=2.2)


def if_bool(id_, name, left_expr, pos):
    return if_node(id_, name,
                   [(left_expr, True, "boolean", "true", "filter.operator.boolean.true")], pos)


def if_eq(id_, name, left_expr, value, pos):
    return if_node(id_, name,
                   [(left_expr, value, "string", "equals", "filter.operator.string.equals")], pos)


def http_plain(id_, name, method, url, body_json, pos, binary_in=False,
               binary_out=False, retry_on_fail=True, continue_on_fail=True,
               full_response=True):
    params = {"method": method, "url": url,
              "sendBody": bool(binary_in or body_json is not None)}
    if binary_in:
        params["contentType"] = "binaryData"
        params["inputDataFieldName"] = "data"
    else:
        params["contentType"] = "json"
        params["specifyBody"] = "json"
    if body_json is not None:
        params["jsonBody"] = body_json
    if binary_out:
        params["options"] = {"response": {"response": {"fullResponse": False, "responseFormat": "autodetect"}}}
    else:
        params["options"] = {"response": {"response": {"fullResponse": full_response, "responseFormat": "json"}}}
    extra = {"continueOnFail": continue_on_fail}
    if retry_on_fail:
        extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000})
    return node(id_, name, "n8n-nodes-base.httpRequest", params, pos,
                version=4.5, extra=extra)


def webhook_trigger(id_, name, path, pos, response_mode="lastNode"):
    return node(id_, name, "n8n-nodes-base.webhook",
                {"httpMethod": "POST", "path": path, "responseMode": response_mode,
                 "options": {}},
                pos, version=2)


def respond_node(id_, name, pos):
    # Explicit response node (responseMode "responseNode" on the trigger):
    # returns the first incoming item as the webhook response. More robust
    # than responseMode "lastNode" for a branched workflow, where the last
    # executed node is not necessarily the response builder.
    return node(id_, name, "n8n-nodes-base.respondToWebhook",
                {"respondWith": "firstIncomingItem", "options": {}},
                pos, version=1.4)


def manual_trigger(id_, name, pos):
    return node(id_, name, "n8n-nodes-base.manualTrigger", {}, pos, version=1)


def sheets_col(name):
    return {"id": name, "displayName": name, "required": False, "display": True,
            "type": "string", "canBeUsedToMatch": True, "removed": False}


def sheets_doc(tab):
    return {"documentId": {"__rl": True, "mode": "url", "value": SHEETS_URL},
            "sheetName": {"__rl": True, "mode": "list", "value": tab, "cachedResultName": tab}}


def sheets_read(id_, name, tab, pos):
    return node(id_, name, "n8n-nodes-base.googleSheets",
                {"resource": "sheet", "operation": "read", **sheets_doc(tab), "options": {}},
                pos, creds=SHEETS_CREDS, version=4.7, extra={"continueOnFail": True})


def columns_map(cols, prefix=""):
    # No space after '=': '= {{ ... }}' evaluates to the value with a
    # leading space, which would store padded identifiers in Sheets.
    return {c: "={{ $json.%s%s }}" % (prefix, c) for c in cols}


def sheets_append(id_, name, tab, cols, pos, prefix=""):
    # No continueOnFail: a failed write must fail the execution visibly,
    # never be swallowed and reported as success (see Collect Writes).
    return node(id_, name, "n8n-nodes-base.googleSheets",
                {"resource": "sheet", "operation": "append", **sheets_doc(tab),
                 "columns": {"mappingMode": "defineBelow", "value": columns_map(cols, prefix),
                             "matchingColumns": [], "schema": [sheets_col(c) for c in cols]},
                 "options": {}},
                pos, creds=SHEETS_CREDS, version=4.7)


def sheets_upsert(id_, name, tab, cols, match_col, pos, prefix=""):
    # No continueOnFail: see sheets_append.
    return node(id_, name, "n8n-nodes-base.googleSheets",
                {"resource": "sheet", "operation": "appendOrUpdate", **sheets_doc(tab),
                 "columns": {"mappingMode": "defineBelow", "value": columns_map(cols, prefix),
                             "matchingColumns": [match_col], "schema": [sheets_col(c) for c in cols]},
                 "options": {}},
                pos, creds=SHEETS_CREDS, version=4.7)


def telegram_trigger(id_, name, updates, pos):
    return node(id_, name, "n8n-nodes-base.telegramTrigger",
                {"updates": updates}, pos, creds=TG_CREDS, version=1.2)


def mock_webhook_trigger(id_, name, path, pos):
    return node(id_, name, "n8n-nodes-base.webhook",
                {"httpMethod": "POST", "path": path, "responseMode": "onReceived",
                 "options": {}},
                pos, version=2)


def telegram_send(id_, name, text_expr, pos, keyboard=None, chat_expr="={{ $json.chat_id }}"):
    params = {"resource": "message", "operation": "sendMessage",
              "chatId": chat_expr, "text": text_expr,
              "additionalFields": {"appendAttribution": False}}
    if keyboard is not None:
        params["additionalFields"]["replyMarkup"] = "inlineKeyboard"
        params["inlineKeyboard"] = keyboard
    return node(id_, name, "n8n-nodes-base.telegram", params, pos,
                creds=TG_CREDS, version=1.2, extra={"continueOnFail": True})


def kb_button(text, callback_expr):
    return {"text": text, "additionalFields": {"callback_data": callback_expr}}


def kb_rows(button_rows):
    return {"rows": [{"row": {"buttons": row}} for row in button_rows]}


def mock_read_body(sheet):
    return '{"sheet": "%s"}' % sheet


def mock_append_body(sheet):
    return "={{ JSON.stringify({sheet: '%s', row: $json.row}) }}" % sheet


def mock_upsert_body(sheet, match_key):
    return "={{ JSON.stringify({sheet: '%s', row: $json.row, matchKey: '%s'}) }}" % (sheet, match_key)


def mock_send_body_expr(body_expr):
    # body_expr: an n8n expression evaluating to the JSON string to POST.
    return body_expr


def read_table(id_, name, tab, pos, mock_mode, base):
    if mock_mode:
        return http_plain(id_, name, "POST", base + "/sheets/read",
                          mock_read_body(tab), pos)
    return sheets_read(id_, name, tab, pos)


def write_table(id_, name, tab, pos, mock_mode, base, upsert=False, match_key=None):
    cols = COLS[tab]
    if mock_mode:
        body = mock_upsert_body(tab, match_key) if upsert else mock_append_body(tab)
        return http_plain(id_, name, "POST",
                          base + ("/sheets/appendOrUpdate" if upsert else "/sheets/append"),
                          body, pos, retry_on_fail=False)
    if upsert:
        return sheets_upsert(id_, name, tab, cols, match_key, pos, prefix="row.")
    return sheets_append(id_, name, tab, cols, pos, prefix="row.")


# ================================================================ ENGINE
def build_engine(base, mock_mode):
    N = []
    C = {}

    def add(n):
        N.append(n)
        return n["name"]

    def link(src, dst, out_idx=0, in_idx=0):
        C.setdefault(src, {"main": []})
        main = C[src]["main"]
        while len(main) <= out_idx:
            main.append([])
        main[out_idx].append({"node": dst, "type": "main", "index": in_idx})

    rev = REVIEWER_ID_MOCK if mock_mode else REVIEWER_ID_REAL
    wh = add(webhook_trigger("webhook", "Engine Webhook", "invoice-matching", [0, 0],
                             response_mode="responseNode"))
    man = add(manual_trigger("manual", "Manual Revalidate", [0, 480]))
    parse = add(code_node("parse", "Parse Engine Call", PARSE_ENGINE_CALL_JS, [220, 0]))
    rcand = add(read_table("read-cand", "Read Invoices (Candidates)", "Invoices", [220, 480], mock_mode, base))
    acand = add(code_node("agg-cand", "Aggregate Candidates", aggregate_js("Invoices"), [440, 480]))
    fcand = add(code_node("filter-cand", "Filter Candidates", FILTER_CANDIDATES_JS, [660, 480]))

    # Six table reads chained; each aggregate fans out to the next read AND
    # to its merge. The payload (parse / filter-cand) joins at the last merge.
    reads, aggs = [], []
    x = 880
    for i, tab in enumerate(TABLES):
        reads.append(add(read_table("read-%d" % i, "Read %s" % tab, tab, [x, 0], mock_mode, base)))
        aggs.append(add(code_node("agg-%d" % i, "Aggregate %s" % tab, aggregate_js(tab), [x + 220, 0])))
        x += 440
    m1 = add(merge_node("mg-1", "Merge Vendors+PO", [1540, 160]))
    m2 = add(merge_node("mg-2", "Merge +Receipts", [1980, 160]))
    m3 = add(merge_node("mg-3", "Merge +Invoices", [2420, 160]))
    m4 = add(merge_node("mg-4", "Merge +Lines", [2860, 160]))
    m5 = add(merge_node("mg-5", "Merge +Allocations", [3300, 160]))
    m6 = add(merge_node("mg-6", "Merge Engine Input", [3520, 160]))
    eng = add(code_node("engine", "Matching Engine", build_engine_js(rev), [3740, 160]))
    exp = add(code_node("expand", "Expand Writes", EXPAND_WRITES_JS, [3960, 160]))
    ifnone = add(if_eq("if-none", "Skip Empty Writes?", "={{ $json.op }}", "none", [4180, 160]))
    ifinv = add(if_eq("if-inv", "Invoice Write?", "={{ $json.sheet }}", "Invoices", [4400, 160]))
    upinv = add(write_table("up-inv", "Upsert Invoice", "Invoices", [4620, 0], mock_mode, base,
                            upsert=True, match_key="invoice_id"))
    iflines = add(if_eq("if-lines", "Lines Write?", "={{ $json.sheet }}", "Invoice_Lines", [4400, 320]))
    aplines = add(write_table("ap-lines", "Append Invoice Lines", "Invoice_Lines", [4620, 320], mock_mode, base))
    ifalc = add(if_eq("if-alc", "Allocation Write?", "={{ $json.sheet }}", "Allocations", [4400, 480]))
    apalc = add(write_table("ap-alc", "Append Allocations", "Allocations", [4620, 480], mock_mode, base))
    apaudit = add(write_table("ap-audit", "Append Audit", "Audit_Log", [4620, 640], mock_mode, base))
    # Collect Writes -> Merge Engine Output: the collector turns the five
    # write branches into ONE item, so the Merge fires exactly once with one
    # engine item to pair (see COLLECT_WRITES_JS). The Merge pairs that item
    # with the Matching Engine's own item (input 1); the write nodes'
    # outputs are the mock/Sheets responses and carry none of the engine's
    # fields, so the response node reads them from the engine item.
    collect = add(code_node("collect", "Collect Writes", COLLECT_WRITES_JS, [4730, 320]))
    mgout = add(merge_node("mg-out", "Merge Engine Output", [4950, 320]))
    resp = add(code_node("response", "Build Response", BUILD_RESPONSE_JS, [5170, 160]))
    respond = add(respond_node("respond", "Respond to Webhook", [5390, 160]))

    link(wh, parse)
    link(man, rcand)
    link(rcand, acand)
    link(acand, fcand)
    link(fcand, reads[0])
    link(fcand, m6, 0, 0)
    link(parse, reads[0])
    link(parse, m6, 0, 0)
    for i in range(6):
        link(reads[i], aggs[i])
        if i < 5:
            link(aggs[i], reads[i + 1])
    link(aggs[0], m1, 0, 1)
    link(aggs[1], m1, 0, 0)
    link(m1, m2, 0, 1)
    link(aggs[2], m2, 0, 0)
    link(m2, m3, 0, 1)
    link(aggs[3], m3, 0, 0)
    link(m3, m4, 0, 1)
    link(aggs[4], m4, 0, 0)
    link(m4, m5, 0, 1)
    link(aggs[5], m5, 0, 0)
    link(m5, m6, 0, 1)
    link(m6, eng)
    link(eng, exp)
    link(exp, ifnone)
    link(ifnone, collect, 0, 0)     # op == none -> collect, then merge
    link(ifnone, ifinv, 1, 0)
    link(ifinv, upinv, 0, 0)
    link(ifinv, iflines, 1, 0)
    link(upinv, collect)
    link(iflines, aplines, 0, 0)
    link(iflines, ifalc, 1, 0)
    link(aplines, collect)
    link(ifalc, apalc, 0, 0)
    link(ifalc, apaudit, 1, 0)
    link(apalc, collect)
    link(apaudit, collect)
    link(collect, mgout, 0, 0)      # one aggregated item -> Merge input 0
    link(eng, mgout, 0, 1)          # engine item -> Merge input 1
    link(mgout, resp)
    link(resp, respond)
    return {"name": "Invoice Matching Engine (Sample 3)",
            "nodes": N, "connections": C, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {},
            "meta": {"n8n_target_version": "2.48.7",
                     "note": "Deterministic 3-way matching engine. Webhook modes: match / apply_exception / revalidate / revalidate_batch. Manual Trigger revalidates pending_receipt + correction_required invoices. This workflow owns ALL Sheets writes; the Telegram workflow never writes Sheets."}}


# ================================================================ TELEGRAM
def build_telegram(base, mock_mode, ocr_real=False, webhook_path="invoice-telegram"):
    N = []
    C = {}

    def add(n):
        N.append(n)
        return n["name"]

    def link(src, dst, out_idx=0, in_idx=0):
        C.setdefault(src, {"main": []})
        main = C[src]["main"]
        while len(main) <= out_idx:
            main.append([])
        main[out_idx].append({"node": dst, "type": "main", "index": in_idx})

    rev = REVIEWER_ID_MOCK if mock_mode else REVIEWER_ID_REAL
    eng_url = ENGINE_MOCK_URL if mock_mode else ENGINE_REAL_URL
    if mock_mode:
        trig = add(mock_webhook_trigger("trigger", "Telegram Webhook (Mock)", webhook_path, [0, 0]))
    else:
        trig = add(telegram_trigger("trigger", "Telegram Trigger", ["message", "callback_query"], [0, 0]))
    router = add(code_node("router", "Route Update", ROUTE_UPDATE_JS, [220, 0]))
    ifphoto = add(if_eq("if-photo", "Photo?", "={{ $json.route }}", "photo", [440, 0]))
    ifcb = add(if_eq("if-cb", "Callback?", "={{ $json.route }}", "callback", [440, 320]))

    # ---- photo branch ----
    pick = add(code_node("pick", "Pick Photo", PICK_PHOTO_JS, [660, 0]))
    if mock_mode:
        getfile = add(http_plain("getfile", "Get File (Mock Telegram)", "GET",
                                 "={{ 'http://127.0.0.1:7999/telegram/getFile?file_id=' + $json.file_id }}",
                                 None, [880, 0], binary_out=True))
    else:
        getfile = add(node("getfile", "Get File", "n8n-nodes-base.telegram",
                           {"resource": "file", "operation": "get",
                            "fileId": "={{ $json.file_id }}", "download": True},
                           [880, 0], creds=TG_CREDS, version=1.2))
    ocr_url = OCR_LOCAL_URL if (mock_mode and ocr_real) else (OCR_MOCK_URL if mock_mode else OCR_REAL_URL)
    ocr = add(http_plain("ocr", "OCR Invoice", "POST", ocr_url, None, [1100, 0],
                         binary_in=True, full_response=False, retry_on_fail=False))
    mgocr = add(merge_node("mg-ocr", "Merge OCR + Sender", [1320, 0]))
    bpayload = add(code_node("bpayload", "Build Engine Payload", BUILD_ENGINE_PAYLOAD_JS, [1540, 0]))
    calleng = add(http_plain("call-eng", "Call Matching Engine", "POST", eng_url,
                             "={{ JSON.stringify($json.engine_body) }}", [1760, 0],
                             full_response=False, retry_on_fail=False, continue_on_fail=False))
    ifrev = add(if_eq("if-rev", "Needs Review?", "={{ $json.status }}", "needs_review", [1980, 0]))
    if mock_mode:
        brev = add(code_node("brev", "Build Review Message", build_review_msg_js(rev), [2200, 0]))
        sendrev = add(http_plain("send-rev", "Send Review Message (Mock Telegram)", "POST",
                                 base + "/telegram/sendMessage", "={{ $json.mock_body }}",
                                 [2420, 0], retry_on_fail=False))
    else:
        brev = None
        review_text = ("Invoice review needed\nInvoice: {{ $json.vendor_invoice_number }}\n"
                       "Vendor: {{ $json.vendor_id }}\nTotal: EGP {{ (Number($json.total_piastres) / 100).toFixed(2) }}\n"
                       "Discrepancy: {{ $json.reason_code }}\nVersion: v{{ $json.version }} | Review: {{ $json.current_review_id }}")
        kb = kb_rows([[kb_button("Approve", "={{ 'approve:' + $json.invoice_id + ':' + $json.version + ':' + $json.current_review_id }}"),
                       kb_button("Reject", "={{ 'reject:' + $json.invoice_id + ':' + $json.version + ':' + $json.current_review_id }}"),
                       kb_button("Correction", "={{ 'correct:' + $json.invoice_id + ':' + $json.version + ':' + $json.current_review_id }}"),
                       kb_button("Defer", "={{ 'defer:' + $json.invoice_id + ':' + $json.version + ':' + $json.current_review_id }}")]])
        sendrev = add(telegram_send("send-rev", "Send Review Message", review_text, [2420, 0],
                                    keyboard=kb, chat_expr=rev))
    bnotify = add(code_node("bnotify", "Build Sender Notice", BUILD_NOTIFY_JS, [2200, 320]))
    if mock_mode:
        sendnot = add(http_plain("send-not", "Notify Sender (Mock Telegram)", "POST",
                                 base + "/telegram/sendMessage", "={{ $json.mock_body }}",
                                 [2420, 320], retry_on_fail=False))
    else:
        sendnot = add(telegram_send("send-not", "Notify Sender", "={{ $json.text }}", [2420, 320],
                                    chat_expr="={{ $json.chat_id }}"))

    # ---- callback branch ----
    parsecb = add(code_node("parse-cb", "Parse Callback", PARSE_CALLBACK_JS, [660, 480]))
    ifreason = add(if_eq("if-reason", "Reason Callback?", "={{ $json.stage }}", "reason", [880, 480]))
    ifappr = add(if_eq("if-appr", "Approve?", "={{ $json.action }}", "approve", [1100, 480]))
    ifrej = add(if_eq("if-rej", "Reject?", "={{ $json.action }}", "reject", [1320, 480]))
    if mock_mode:
        breason_a = add(code_node("breason-a", "Build Reason Keyboard (Approve)", BUILD_REASON_MSG_JS, [1540, 320]))
        sendreason_a = add(http_plain("sendreason-a", "Send Approve Reasons (Mock)", "POST",
                                      base + "/telegram/sendMessage", "={{ $json.mock_body }}",
                                      [1760, 320], retry_on_fail=False))
        breason_r = add(code_node("breason-r", "Build Reason Keyboard (Reject)", BUILD_REASON_MSG_JS, [1540, 480]))
        sendreason_r = add(http_plain("sendreason-r", "Send Reject Reasons (Mock)", "POST",
                                      base + "/telegram/sendMessage", "={{ $json.mock_body }}",
                                      [1760, 480], retry_on_fail=False))
    else:
        breason_a = breason_r = None
        kb_a = kb_rows([[kb_button("Price within EGP 50 policy", "={{ 'reason:approve:price_within_policy:' + $json.invoice_id + ':' + $json.version + ':' + $json.review_id }}"),
                         kb_button("Rounding accepted", "={{ 'reason:approve:rounding_accepted:' + $json.invoice_id + ':' + $json.version + ':' + $json.review_id }}")]])
        sendreason_a = add(telegram_send("sendreason-a", "Send Reason Keyboard (Approve)",
                                         "Pick a reason for approve:", [1760, 320],
                                         keyboard=kb_a, chat_expr="={{ $json.chat_id }}"))
        kb_r = kb_rows([[kb_button("Price too high", "={{ 'reason:reject:price_too_high:' + $json.invoice_id + ':' + $json.version + ':' + $json.review_id }}"),
                         kb_button("Wrong invoice", "={{ 'reason:reject:wrong_invoice:' + $json.invoice_id + ':' + $json.version + ':' + $json.review_id }}"),
                         kb_button("Duplicate", "={{ 'reason:reject:duplicate:' + $json.invoice_id + ':' + $json.version + ':' + $json.review_id }}")]])
        sendreason_r = add(telegram_send("sendreason-r", "Send Reason Keyboard (Reject)",
                                         "Pick a reason for reject:", [1760, 480],
                                         keyboard=kb_r, chat_expr="={{ $json.chat_id }}"))
    bapply = add(code_node("bapply", "Build Apply Call", BUILD_APPLY_CALL_JS, [1540, 640]))
    callapply = add(http_plain("call-apply", "Call Engine (Apply)", "POST", eng_url,
                               "={{ JSON.stringify($json.engine_body) }}", [1760, 640],
                               full_response=False, retry_on_fail=False, continue_on_fail=False))
    mgconf = add(merge_node("mg-conf", "Merge Confirm + Callback", [1980, 640]))
    if mock_mode:
        sendconf = add(http_plain("send-conf", "Send Confirmation (Mock Telegram)", "POST",
                                  base + "/telegram/sendMessage",
                                  "={{ JSON.stringify({chat_id: $json.chat_id, text: $json.summary_text}) }}",
                                  [2200, 640], retry_on_fail=False))
        ifconf = add(if_node("if-conf", "Submitter Notice?", [("={{ $json.confirm_text }}", "", "string", "notEquals", "filter.operator.string.notEquals")], [2420, 640]))
        sendsub = add(http_plain("send-sub", "Notify Submitter (Mock Telegram)", "POST",
                                 base + "/telegram/sendMessage",
                                 "={{ JSON.stringify({chat_id: $json.submitter_id, text: $json.confirm_text}) }}",
                                 [2640, 640], retry_on_fail=False))
    else:
        sendconf = add(telegram_send("send-conf", "Send Confirmation", "={{ $json.summary_text }}", [2200, 640],
                                     chat_expr="={{ $json.chat_id }}"))
        ifconf = add(if_node("if-conf", "Submitter Notice?", [("={{ $json.confirm_text }}", "", "string", "notEquals", "filter.operator.string.notEquals")], [2420, 640]))
        sendsub = add(telegram_send("send-sub", "Notify Submitter", "={{ $json.confirm_text }}", [2640, 640],
                                    chat_expr="={{ $json.submitter_id }}"))

    link(trig, router)
    link(router, ifphoto)
    link(ifphoto, pick, 0, 0)
    link(ifphoto, ifcb, 1, 0)
    link(ifcb, parsecb, 0, 0)
    link(pick, getfile)
    link(pick, mgocr, 0, 1)
    link(getfile, ocr)
    link(ocr, mgocr, 0, 0)
    link(mgocr, bpayload)
    link(bpayload, calleng)
    link(calleng, ifrev)
    if mock_mode:
        link(ifrev, brev, 0, 0)
        link(brev, sendrev)
    else:
        link(ifrev, sendrev, 0, 0)
    link(ifrev, bnotify, 1, 0)
    link(bnotify, sendnot)
    link(parsecb, ifreason)
    link(parsecb, mgconf, 0, 1)
    link(ifreason, bapply, 0, 0)
    link(ifreason, ifappr, 1, 0)
    link(ifappr, breason_a if mock_mode else sendreason_a, 0, 0)
    if mock_mode:
        link(breason_a, sendreason_a)
    link(ifappr, ifrej, 1, 0)
    link(ifrej, breason_r if mock_mode else sendreason_r, 0, 0)
    if mock_mode:
        link(breason_r, sendreason_r)
    link(ifrej, bapply, 1, 0)
    link(bapply, callapply)
    link(callapply, mgconf, 0, 0)
    link(mgconf, sendconf)
    link(sendconf, ifconf)
    link(ifconf, sendsub, 0, 0)
    return {"name": "Invoice Processor — Telegram (Sample 3)",
            "nodes": N, "connections": C, "active": False,
            "settings": {"executionOrder": "v1"}, "pinData": {},
            "meta": {"n8n_target_version": "2.48.7",
                     "note": "One Telegram entry (photos -> intake, callbacks -> review). Never writes Sheets: all state changes go through the Matching Engine webhook. Review requests go to the reviewer only; the sender gets outcome notices."}}


def main():
    out = {
        "invoice-matching-engine-workflow.json": build_engine(None, False),
        "invoice-matching-engine-workflow-MOCK.json": build_engine(MOCK, True),
        "invoice-telegram-workflow.json": build_telegram(None, False),
        "invoice-telegram-workflow-MOCK.json": build_telegram(MOCK, True),
        "invoice-telegram-workflow-MOCK-REALOCR.json": build_telegram(MOCK, True, ocr_real=True, webhook_path="invoice-telegram-realocr"),
    }
    for name, wf in out.items():
        p = BASE / name
        p.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
        print(name, len(wf["nodes"]), "nodes")


if __name__ == "__main__":
    main()

