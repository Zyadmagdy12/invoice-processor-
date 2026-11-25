# Invoice Processor (Sample 3) — builders' shared constants + engine JS.
# The Matching Engine is ONE Code node (ENGINE_JS) with three modes:
#   match            — new extracted invoice (payload) -> stages A-G -> rows
#   apply_exception  — review decision (approve/reject/correction/defer)
#                      -> re-validates invariants, records allocation only
#                         via this engine, finalises exception_approved
#   revalidate       — single invoice in pending_receipt / correction_required
#                      -> version bump + full re-run (correction path)
# The engine is PURE computation: it returns rows; the workflow writes them.
# All money is integer piastres. REVIEWER_ID is substituted per variant.
PRICE_REVIEW_MAX_PT = 5000  # EGP 50, per line AND sum of |line variances|
REVIEWER_ID_MOCK = "111111"
REVIEWER_ID_REAL = "FILL_REVIEWER_TELEGRAM_ID"
DEMO_TODAY = "2026-11-22"

COLS = {
    "Vendors": ["vendor_id", "vendor_name", "tax_id", "status"],
    "PO_Lines": ["po_id", "po_line_id", "vendor_id", "sku", "description", "qty_ordered", "unit_price_piastres", "tax_rate_pct", "currency", "status"],
    "Receipt_Lines": ["receipt_id", "po_line_id", "qty_accepted", "received_date", "status"],
    "Invoices": ["invoice_id", "vendor_id", "vendor_invoice_number", "document_hash", "status", "reason_code", "version", "current_review_id", "submitter_id", "subtotal_piastres", "tax_piastres", "total_piastres", "created_at", "updated_at"],
    "Invoice_Lines": ["invoice_id", "line_no", "po_line_id", "sku", "qty", "unit_price_piastres", "line_total_piastres", "tax_piastres"],
    "Allocations": ["allocation_id", "invoice_id", "po_line_id", "qty", "status", "created_at"],
    "Audit_Log": ["audit_id", "invoice_id", "old_status", "new_status", "reason_code", "decided_by", "note", "created_at"],
}

ENGINE_JS = r"""
// Invoice Matching Engine — mode: match | apply_exception | revalidate
// Input (one item): { mode, invoice?, lines?, decision?, invoice_id?,
//   Vendors, PO_Lines, Receipt_Lines, Invoices, Invoice_Lines, Allocations }
// Output (one item): { ok, mode, invoice_id, status, reason_code, version,
//   invoice_row, lines_rows, allocation_rows, audit_rows, summary, confirm }
const PRICE_REVIEW_MAX_PT = 5000;
const REVIEWER_ID = "__REVIEWER_ID__";
const inp = $input.first().json;
const mode = inp.mode;
const vendors = inp.Vendors || [];
const poLines = inp.PO_Lines || [];
const receipts = inp.Receipt_Lines || [];
const invoices = inp.Invoices || [];
const invLines = inp.Invoice_Lines || [];
const allocs = inp.Allocations || [];
let _seq = 0;
function nowISO(){ return new Date().toISOString(); }
function uid(p){ _seq += 1; return p + '-' + Date.now().toString(36) + '-' + _seq; }
function num(v){ const n = Number(v); return Number.isFinite(n) ? n : 0; }
function taxFor(lineTotalPt, ratePct){ return Math.round(lineTotalPt * ratePct / 100); }
function findPO(id){ return poLines.find(r => r.po_line_id === id); }
function activeReceipts(poLineId){ return receipts.filter(r => r.po_line_id === poLineId && r.status === 'active'); }
function activeAllocs(poLineId){ return allocs.filter(r => r.po_line_id === poLineId && r.status === 'active'); }
function availableReceived(poLineId){
  const rec = activeReceipts(poLineId).reduce((s,r)=>s+num(r.qty_accepted),0);
  const alc = activeAllocs(poLineId).reduce((s,r)=>s+num(r.qty),0);
  return rec - alc;
}
function alreadyInvoiced(poLineId){ return activeAllocs(poLineId).reduce((s,r)=>s+num(r.qty),0); }
function auditRow(invoiceId, oldStatus, newStatus, reason, decidedBy, note){
  return { audit_id: uid('AUD'), invoice_id: invoiceId, old_status: oldStatus || '', new_status: newStatus || '',
           reason_code: reason || '', decided_by: decidedBy || '', note: note || '', created_at: nowISO() };
}
function resolveVendor(invoice){
  if (invoice.vendor_id){
    const v = vendors.find(r => r.vendor_id === invoice.vendor_id);
    if (v && v.status === 'active') return v.vendor_id;
    return null;
  }
  if (invoice.vendor_tax_id){
    const v = vendors.find(r => r.tax_id === invoice.vendor_tax_id);
    if (v && v.status === 'active') return v.vendor_id;
  }
  return null;
}
function groupQty(lines){
  const byPo = {};
  for (const ln of lines){ byPo[ln.po_line_id] = (byPo[ln.po_line_id] || 0) + num(ln.qty); }
  return byPo;
}
// --- 7 stages. Returns {status, reason_code, allocations?} (first failure wins,
// except price-review (D soft) is held so a later blocked stage can override it).
function runStages(invoice, lines){
  // Stage A — document validation
  if (!invoice.vendor_invoice_number || !invoice.invoice_date ||
      invoice.subtotal_pt === undefined || invoice.tax_pt === undefined || invoice.total_pt === undefined)
    return { status: 'needs_review', reason_code: 'missing_field' };
  if (!lines || lines.length === 0)
    return { status: 'needs_review', reason_code: 'missing_field' };
  for (const ln of lines){
    if (ln.qty === undefined || ln.unit_price_pt === undefined || ln.line_total_pt === undefined || !ln.sku)
      return { status: 'needs_review', reason_code: 'missing_field' };
    if (num(ln.line_total_pt) !== num(ln.qty) * num(ln.unit_price_pt))
      return { status: 'needs_review', reason_code: 'arithmetic_mismatch' };
  }
  const subtotal = lines.reduce((s,l)=>s+num(l.line_total_pt),0);
  if (subtotal !== num(invoice.subtotal_pt)) return { status: 'needs_review', reason_code: 'arithmetic_mismatch' };
  if (subtotal + num(invoice.tax_pt) !== num(invoice.total_pt)) return { status: 'needs_review', reason_code: 'arithmetic_mismatch' };
  // Stages B+C — identity + line binding (per line)
  const vendorId = resolveVendor(invoice);
  if (!vendorId) return { status: 'blocked', reason_code: 'vendor_mismatch' };
  for (const ln of lines){
    if (!ln.po_line_id) return { status: 'needs_review', reason_code: 'line_unmatched' };
    const po = findPO(ln.po_line_id);
    if (!po) return { status: 'blocked', reason_code: 'unknown_po' };
    if (po.vendor_id !== vendorId) return { status: 'blocked', reason_code: 'vendor_mismatch' };
    if (po.status !== 'open') return { status: 'blocked', reason_code: 'po_closed' };
    if (ln.sku !== po.sku) return { status: 'blocked', reason_code: 'unknown_sku' };
  }
  // Stage D — price (hard fail over cap; soft flag under cap)
  let sumVar = 0;
  for (const ln of lines){
    const po = findPO(ln.po_line_id);
    const v = num(ln.qty) * Math.abs(num(ln.unit_price_pt) - num(po.unit_price_piastres));
    if (v > PRICE_REVIEW_MAX_PT) return { status: 'blocked', reason_code: 'price_variance_blocked' };
    sumVar += v;
  }
  if (sumVar > PRICE_REVIEW_MAX_PT) return { status: 'blocked', reason_code: 'price_variance_blocked' };
  const priceReview = sumVar > 0;
  // Stage E — quantity (cumulative consumption)
  const byPo = groupQty(lines);
  for (const pl of Object.keys(byPo)){
    const po = findPO(pl);
    const received = activeReceipts(pl).reduce((s,r)=>s+num(r.qty_accepted),0);
    if (received === 0) return { status: 'pending_receipt', reason_code: 'missing_receipt' };
    if (byPo[pl] > availableReceived(pl)) return { status: 'blocked', reason_code: 'insufficient_received' };
    if (alreadyInvoiced(pl) + byPo[pl] > num(po.qty_ordered)) return { status: 'blocked', reason_code: 'over_po_quantity' };
  }
  // Allocations are determined by quantity availability (Stage E), not by
  // price: a price-review invoice carries its would-be allocations so the
  // apply path can consume them on approval. Match/revalidate only WRITE
  // them when the outcome is auto_matched.
  const allocations = Object.keys(byPo).map(pl => ({ allocation_id: uid('ALC'), invoice_id: invoice.invoice_id,
    po_line_id: pl, qty: byPo[pl], status: 'active', created_at: nowISO() }));
  // Stage F — financial validation (tax recomputed from PO rules). Runs
  // BEFORE any price-review outcome is returned: a tax mismatch is a
  // non-overridable block, so a combined price-variance + tax-mismatch
  // invoice must be blocked, never reviewable. (Round-4 fix: the
  // price-review return used to sit here, before Stage F, and a
  // within-cap variance short-circuited tax validation entirely.)
  let taxSum = 0;
  for (const ln of lines){
    const po = findPO(ln.po_line_id);
    taxSum += taxFor(num(ln.line_total_pt), num(po.tax_rate_pct));
  }
  if (taxSum !== num(invoice.tax_pt)) return { status: 'blocked', reason_code: 'tax_mismatch' };
  if (priceReview) return { status: 'needs_review', reason_code: 'price_variance_review', allocations };
  // Stage G — decision
  const exact = Object.keys(byPo).every(pl => byPo[pl] === availableReceived(pl));
  return { status: 'auto_matched', reason_code: exact ? 'exact_match' : 'partial_match', allocations };
}
function invoiceRowFor(invoice, vendorId, status, reason, version, reviewId){
  return { invoice_id: invoice.invoice_id, vendor_id: vendorId, vendor_invoice_number: invoice.vendor_invoice_number,
    document_hash: invoice.document_hash || '', status, reason_code: reason, version,
    current_review_id: reviewId || '', submitter_id: invoice.submitter_id || '',
    subtotal_piastres: num(invoice.subtotal_pt), tax_piastres: num(invoice.tax_pt), total_piastres: num(invoice.total_pt),
    created_at: invoice.created_at || nowISO(), updated_at: nowISO() };
}
function linesRowsFor(invoiceId, lines){
  return lines.map((ln, i) => ({ invoice_id: invoiceId, line_no: i + 1, po_line_id: ln.po_line_id || '',
    sku: ln.sku || '', qty: num(ln.qty), unit_price_piastres: num(ln.unit_price_pt),
    line_total_piastres: num(ln.line_total_pt), tax_piastres: 0 }));
}
function out(o){ return [{ json: o }]; }

if (mode === 'match'){
  const invoice = inp.invoice || {};
  const lines = inp.lines || [];
  // Stage 0 — document hash duplicate (before persisting anything)
  const dup = (invoice.document_hash && invoices.find(r => r.document_hash && r.document_hash === invoice.document_hash)) || null;
  if (dup){
    return out({ ok: true, mode, outcome: 'duplicate_image', invoice_id: dup.invoice_id, status: 'duplicate_image',
      reason_code: 'duplicate_image', version: dup.version, no_new_row: true, invoice_row: null, lines_rows: [], allocation_rows: [],
      audit_rows: [auditRow(dup.invoice_id, dup.status, dup.status, 'duplicate_image', invoice.submitter_id || '', 'same document hash resubmitted')],
      summary: { status: 'duplicate_image', reason_code: 'duplicate_image', text: 'This document was already submitted.' } });
  }
  const vendorId = resolveVendor(invoice);
  const invoiceId = 'INV-' + String(invoice.document_hash || 'nohash').slice(0, 8);
  invoice.invoice_id = invoiceId;
  if (!vendorId){
    const row = invoiceRowFor(invoice, '', 'blocked', 'vendor_mismatch', 1, '');
    return out({ ok: true, mode, outcome: 'blocked', invoice_id: invoiceId, status: 'blocked', reason_code: 'vendor_mismatch',
      version: 1, invoice_row: row, lines_rows: linesRowsFor(invoiceId, lines), allocation_rows: [],
      audit_rows: [auditRow(invoiceId, 'received', 'blocked', 'vendor_mismatch', invoice.submitter_id || '', 'vendor could not be resolved against the vendor master')],
      summary: { status: 'blocked', reason_code: 'vendor_mismatch', text: 'Vendor not recognised.' } });
  }
  const dupNum = invoices.find(r => r.vendor_id === vendorId && r.vendor_invoice_number === invoice.vendor_invoice_number);
  if (dupNum){
    const row = invoiceRowFor(invoice, vendorId, 'blocked', 'duplicate_invoice', 1, '');
    return out({ ok: true, mode, outcome: 'blocked', invoice_id: invoiceId, status: 'blocked', reason_code: 'duplicate_invoice',
      version: 1, invoice_row: row, lines_rows: linesRowsFor(invoiceId, lines), allocation_rows: [],
      audit_rows: [auditRow(invoiceId, 'received', 'blocked', 'duplicate_invoice', invoice.submitter_id || '', 'same vendor + invoice number already exists as ' + dupNum.invoice_id)],
      summary: { status: 'blocked', reason_code: 'duplicate_invoice', text: 'Duplicate invoice number for this vendor.' } });
  }
  const res = runStages(invoice, lines);
  const reviewId = res.status === 'needs_review' ? ('REV-' + invoiceId + '-v1') : '';
  const row = invoiceRowFor(invoice, vendorId, res.status, res.reason_code, 1, reviewId);
  const allocsOut = res.status === 'auto_matched' ? (res.allocations || []) : [];
  return out({ ok: true, mode, outcome: res.status, invoice_id: invoiceId, status: res.status, reason_code: res.reason_code,
    version: 1, invoice_row: row, lines_rows: linesRowsFor(invoiceId, lines), allocation_rows: allocsOut,
    audit_rows: [auditRow(invoiceId, 'received', res.status, res.reason_code, invoice.submitter_id || '', '')],
    summary: { status: res.status, reason_code: res.reason_code, text: res.status + ' / ' + res.reason_code } });
}

if (mode === 'apply_exception'){
  const d = inp.decision || {};
  const inv = invoices.find(r => r.invoice_id === d.invoice_id);
  if (!inv){
    return out({ ok: false, mode, outcome: 'review_blocked_notfound', invoice_id: d.invoice_id || '', status: '', reason_code: 'review_blocked_notfound',
      version: 0, invoice_row: null, lines_rows: [], allocation_rows: [],
      audit_rows: [auditRow(d.invoice_id || '', '', '', 'review_blocked_notfound', d.from_id || '', 'invoice not found')],
      summary: { status: 'blocked', reason_code: 'review_blocked_notfound', text: 'Invoice not found.' } });
  }
  const oldStatus = inv.status;
  const block = (reason, note) => out({ ok: false, mode, outcome: reason, invoice_id: inv.invoice_id, status: oldStatus,
    reason_code: reason, version: num(inv.version), invoice_row: null, lines_rows: [], allocation_rows: [],
    audit_rows: [auditRow(inv.invoice_id, oldStatus, oldStatus, reason, d.from_id || '', note || '')],
    summary: { status: 'blocked', reason_code: reason, text: 'Decision blocked: ' + reason } });
  // Check order: stale -> submitter -> auth -> repeat -> approvable.
  // (Submitter before auth: the submitter is never the reviewer, so the
  // auth check alone would mask the conflict-of-interest reason.)
  if (num(d.version) !== num(inv.version) || String(d.review_id || '') !== String(inv.current_review_id || ''))
    return block('review_blocked_stale', 'decision refers to version ' + d.version + ' / ' + d.review_id + ', current is v' + inv.version + ' / ' + inv.current_review_id);
  if (String(d.from_id || '') === String(inv.submitter_id || '') && String(inv.submitter_id || '') !== '')
    return block('review_blocked_submitter', 'submitter cannot approve their own invoice');
  if (String(d.from_id || '') !== REVIEWER_ID)
    return block('review_blocked_auth', 'decider ' + d.from_id + ' is not the authorised reviewer');
  if (oldStatus !== 'needs_review')
    return block('review_blocked_repeat', 'invoice is already ' + oldStatus);
  if (d.action === 'approve' && inv.reason_code !== 'price_variance_review')
    return block('review_blocked_not_approvable', 'reason ' + inv.reason_code + ' is not an approvable exception');
  if (d.action === 'approve'){
    // Re-run critical invariants at commit time (identity + quantity on current state)
    const storedLines = invLines.filter(r => r.invoice_id === inv.invoice_id);
    const checkInvoice = { vendor_id: inv.vendor_id, vendor_invoice_number: inv.vendor_invoice_number,
      invoice_date: '2026-11-22', subtotal_pt: num(inv.subtotal_piastres), tax_pt: num(inv.tax_piastres),
      total_pt: num(inv.total_piastres), invoice_id: inv.invoice_id };
    const checkLines = storedLines.map(r => ({ po_line_id: r.po_line_id, sku: r.sku, qty: num(r.qty),
      unit_price_pt: num(r.unit_price_piastres), line_total_pt: num(r.line_total_piastres) }));
    const re = runStages(checkInvoice, checkLines);
    if (re.status === 'blocked')
      return block(re.reason_code, 'revalidation at commit time failed: ' + re.reason_code);
    if (re.status !== 'needs_review' || re.reason_code !== 'price_variance_review')
      return block('review_blocked_not_approvable', 'revalidation no longer yields an approvable exception (' + re.status + '/' + re.reason_code + ')');
    const allocsOut = re.allocations || [];
    const row = Object.assign({}, inv, { status: 'exception_approved', reason_code: 'manual_approve', updated_at: nowISO() });
    return out({ ok: true, mode, outcome: 'exception_approved', invoice_id: inv.invoice_id, status: 'exception_approved',
      reason_code: 'manual_approve', version: num(inv.version), invoice_row: row, lines_rows: [], allocation_rows: allocsOut,
      audit_rows: [auditRow(inv.invoice_id, oldStatus, 'exception_approved', 'manual_approve', d.from_id || '', d.reason_key || '')],
      summary: { status: 'exception_approved', reason_code: 'manual_approve', text: 'Exception approved.' },
      confirm: { chat_id: inv.submitter_id, text: 'Invoice ' + inv.vendor_invoice_number + ' approved as an exception (price variance within EGP 50).' } });
  }
  if (d.action === 'reject'){
    const row = Object.assign({}, inv, { status: 'rejected', reason_code: 'manual_reject', updated_at: nowISO() });
    return out({ ok: true, mode, outcome: 'rejected', invoice_id: inv.invoice_id, status: 'rejected', reason_code: 'manual_reject',
      version: num(inv.version), invoice_row: row, lines_rows: [], allocation_rows: [],
      audit_rows: [auditRow(inv.invoice_id, oldStatus, 'rejected', 'manual_reject', d.from_id || '', d.reason_key || '')],
      summary: { status: 'rejected', reason_code: 'manual_reject', text: 'Rejected by reviewer.' },
      confirm: { chat_id: inv.submitter_id, text: 'Invoice ' + inv.vendor_invoice_number + ' was rejected by the reviewer.' } });
  }
  if (d.action === 'correction'){
    const storedLines = invLines.filter(r => r.invoice_id === inv.invoice_id);
    const snapshot = 'pre-correction v' + num(inv.version) + ' values: subtotal=' + inv.subtotal_piastres +
      ' tax=' + inv.tax_piastres + ' total=' + inv.total_piastres + ' lines=' +
      JSON.stringify(storedLines.map(l => [l.po_line_id, l.qty, l.unit_price_piastres, l.line_total_piastres]));
    const row = Object.assign({}, inv, { status: 'correction_required', reason_code: 'correction_requested', updated_at: nowISO() });
    return out({ ok: true, mode, outcome: 'correction_required', invoice_id: inv.invoice_id, status: 'correction_required',
      reason_code: 'correction_requested', version: num(inv.version), invoice_row: row, lines_rows: [], allocation_rows: [],
      audit_rows: [auditRow(inv.invoice_id, oldStatus, 'correction_required', 'correction_requested', d.from_id || '', (d.reason_key ? d.reason_key + ' | ' : '') + snapshot)],
      summary: { status: 'correction_required', reason_code: 'correction_requested', text: 'Correction requested.' },
      confirm: { chat_id: inv.submitter_id, text: 'Invoice ' + inv.vendor_invoice_number + ' needs correction. Fix the values in the sheet, then revalidate.' } });
  }
  // defer
  return out({ ok: true, mode, outcome: 'deferred', invoice_id: inv.invoice_id, status: oldStatus, reason_code: 'review_deferred',
    version: num(inv.version), invoice_row: null, lines_rows: [], allocation_rows: [],
    audit_rows: [auditRow(inv.invoice_id, oldStatus, oldStatus, 'review_deferred', d.from_id || '', d.reason_key || '')],
    summary: { status: oldStatus, reason_code: 'review_deferred', text: 'Review deferred.' } });
}

function revalidateOne(invId, requestedBy){
  const inv = invoices.find(r => r.invoice_id === invId);
  if (!inv){
    return { ok: false, outcome: 'review_blocked_notfound', invoice_id: invId || '', status: '', reason_code: 'review_blocked_notfound',
      version: 0, invoice_row: null, lines_rows: [], allocation_rows: [], audit_rows: [],
      summary: { status: 'blocked', reason_code: 'review_blocked_notfound', text: 'Invoice not found.' } };
  }
  const oldStatus = inv.status;
  if (oldStatus !== 'pending_receipt' && oldStatus !== 'correction_required'){
    return { ok: false, outcome: 'nothing_to_revalidate', invoice_id: inv.invoice_id, status: oldStatus,
      reason_code: 'nothing_to_revalidate', version: num(inv.version), invoice_row: null, lines_rows: [], allocation_rows: [], audit_rows: [],
      summary: { status: oldStatus, reason_code: 'nothing_to_revalidate', text: 'Invoice is ' + oldStatus + '; nothing to revalidate.' } };
  }
  const auditRows = [];
  let version = num(inv.version);
  if (oldStatus === 'correction_required'){
    // The previous version's values were snapshotted in the
    // correction_requested audit event (written before the operator edited
    // anything). Accepting the new version just references that snapshot.
    auditRows.push(auditRow(inv.invoice_id, oldStatus, oldStatus, 'correction_accepted', requestedBy || '',
      'accepted version v' + (version + 1) + ' (pre-correction values preserved in the correction_requested audit event)'));
    version = version + 1;
  }
  const storedLines = invLines.filter(r => r.invoice_id === inv.invoice_id);
  const checkInvoice = { vendor_id: inv.vendor_id, vendor_invoice_number: inv.vendor_invoice_number,
    invoice_date: '2026-11-22', subtotal_pt: num(inv.subtotal_piastres), tax_pt: num(inv.tax_piastres),
    total_pt: num(inv.total_piastres), invoice_id: inv.invoice_id };
  const checkLines = storedLines.map(r => ({ po_line_id: r.po_line_id, sku: r.sku, qty: num(r.qty),
    unit_price_pt: num(r.unit_price_piastres), line_total_pt: num(r.line_total_piastres) }));
  const res = runStages(checkInvoice, checkLines);
  const reviewId = res.status === 'needs_review' ? ('REV-' + inv.invoice_id + '-v' + version) : '';
  const row = Object.assign({}, inv, { status: res.status, reason_code: res.reason_code, version, current_review_id: reviewId, updated_at: nowISO() });
  auditRows.push(auditRow(inv.invoice_id, oldStatus, res.status, res.reason_code, requestedBy || '', 'revalidation (v' + version + ')'));
  return { ok: true, outcome: res.status, invoice_id: inv.invoice_id, status: res.status, reason_code: res.reason_code,
    version, invoice_row: row, lines_rows: [], allocation_rows: res.status === 'auto_matched' ? (res.allocations || []) : [], audit_rows: auditRows,
    summary: { status: res.status, reason_code: res.reason_code, text: 'Revalidated: ' + res.status + ' / ' + res.reason_code } };
}

if (mode === 'revalidate'){
  return out(Object.assign({ mode }, revalidateOne(inp.invoice_id, inp.requested_by)));
}

if (mode === 'revalidate_batch'){
  const ids = inp.invoice_ids || [];
  const invoiceRows = [], allocRows = [], auditRows = [], summaries = [];
  for (const id of ids){
    const r = revalidateOne(id, inp.requested_by);
    if (r.invoice_row) invoiceRows.push(r.invoice_row);
    allocRows.push(...(r.allocation_rows || []));
    auditRows.push(...(r.audit_rows || []));
    summaries.push({ invoice_id: id, status: r.status, reason_code: r.reason_code });
  }
  return out({ ok: true, mode, outcome: 'revalidate_batch', invoice_id: '', status: '', reason_code: '',
    version: 0, invoice_row: null, invoice_rows: invoiceRows, lines_rows: [], allocation_rows: allocRows,
    audit_rows: auditRows, summaries,
    summary: { status: 'revalidate_batch', reason_code: '', text: 'Revalidated ' + ids.length + ' invoice(s).' } });
}

return out({ ok: false, mode, outcome: 'unknown_mode', invoice_id: '', status: '', reason_code: 'unknown_mode',
  version: 0, invoice_row: null, lines_rows: [], allocation_rows: [], audit_rows: [],
  summary: { status: 'error', reason_code: 'unknown_mode', text: 'Unknown engine mode: ' + mode } });
"""


def build_engine_js(reviewer_id):
    return ENGINE_JS.replace("__REVIEWER_ID__", reviewer_id)


# --- Telegram workflow JS (kept small; the engine does the thinking) ---
# Router: message with photo -> intake; callback_query -> review.
ROUTE_UPDATE_JS = r"""
const it = $input.first().json;
const isCallback = !!(it.callback_query || (it.body && it.body.callback_query));
const isPhoto = !!((it.message && it.message.photo) || (it.body && it.body.message && it.body.message.photo));
return [{ json: { ...it, route: isCallback ? 'callback' : (isPhoto ? 'photo' : 'other') } }];
"""

# Parse a review callback: approve:<inv>:<ver>:<rev> | reject:<inv>:<ver>:<rev>
# | correct:<inv>:<ver>:<rev> | defer:<inv>:<ver>:<rev>
# | reason:<action>:<reason_key>:<inv>:<ver>:<rev>
PARSE_CALLBACK_JS = r"""
const it = $input.first().json;
const cb = it.callback_query || (it.body && it.body.callback_query) || {};
const data = (cb.data || '').trim();
const fromId = String((cb.from && cb.from.id) || '');
const parts = data.split(':');
let out = { from_id: fromId, chat_id: String((cb.message && cb.message.chat && cb.message.chat.id) || fromId) };
if (parts[0] === 'reason' && parts.length >= 6){
  out = { ...out, stage: 'reason', action: parts[1], reason_key: parts[2], invoice_id: parts[3], version: Number(parts[4]), review_id: parts[5] };
} else if (parts.length >= 4){
  out = { ...out, stage: 'action', action: parts[0], invoice_id: parts[1], version: Number(parts[2]), review_id: parts[3] };
} else {
  out = { ...out, stage: 'unknown', action: '', invoice_id: '', version: 0, review_id: '' };
}
return [{ json: { ...it, ...out } }];
"""

# Build the review message (to the reviewer) + inline keyboard.
# Input: engine response (flat fields: invoice_id, vendor_invoice_number,
# vendor_id, total_piastres, version, current_review_id).
BUILD_REVIEW_MSG_JS = r"""
const it = $input.first().json;
const reviewer = "__REVIEWER_ID__";
const text = 'Invoice review needed\n'
  + 'Invoice: ' + (it.vendor_invoice_number || it.invoice_id) + '\n'
  + 'Vendor: ' + (it.vendor_id || '') + '\n'
  + 'Total: EGP ' + (Number(it.total_piastres || 0) / 100).toFixed(2) + '\n'
  + 'Discrepancy: ' + (it.reason_code || '') + '\n'
  + 'Version: v' + (it.version || 1) + ' | Review: ' + (it.current_review_id || '');
const kb = { inline_keyboard: [[
  { text: 'Approve', callback_data: 'approve:' + it.invoice_id + ':' + it.version + ':' + it.current_review_id },
  { text: 'Reject', callback_data: 'reject:' + it.invoice_id + ':' + it.version + ':' + it.current_review_id },
  { text: 'Correction', callback_data: 'correct:' + it.invoice_id + ':' + it.version + ':' + it.current_review_id },
  { text: 'Defer', callback_data: 'defer:' + it.invoice_id + ':' + it.version + ':' + it.current_review_id },
]]};
return [{ json: { chat_id: reviewer, text, reply_markup: kb,
  mock_body: JSON.stringify({ chat_id: reviewer, text, reply_markup: kb }) } }];
"""

# Reason keyboard after Approve/Reject is tapped (approve needs a reason too).
BUILD_REASON_MSG_JS = r"""
const it = $input.first().json;
const a = it.action; const inv = it.invoice_id; const v = it.version; const r = it.review_id;
const reasons = a === 'approve'
  ? [['price_within_policy', 'Price within EGP 50 policy'], ['rounding_accepted', 'Rounding accepted']]
  : [['price_too_high', 'Price too high'], ['wrong_invoice', 'Wrong invoice'], ['duplicate', 'Duplicate']];
const kb = { inline_keyboard: reasons.map(([k, label]) => [{ text: label, callback_data: 'reason:' + a + ':' + k + ':' + inv + ':' + v + ':' + r }]) };
const text = 'Pick a reason for ' + a + ':';
return [{ json: { chat_id: it.chat_id, text, reply_markup: kb,
  mock_body: JSON.stringify({ chat_id: it.chat_id, text, reply_markup: kb }) } }];
"""


def build_review_msg_js(reviewer_id):
    return BUILD_REVIEW_MSG_JS.replace("__REVIEWER_ID__", reviewer_id)

# --- Additional workflow JS (Engine + Telegram workflows) ---

# Engine webhook: unwrap the Webhook node's {headers, params, query, body}.
PARSE_ENGINE_CALL_JS = r"""
const it = $input.first().json;
const root = it.body || it;
return [{ json: { mode: root.mode || '', invoice: root.invoice || null, lines: root.lines || [],
  decision: root.decision || null, invoice_id: root.invoice_id || '', invoice_ids: root.invoice_ids || [],
  requested_by: root.requested_by || '' } }];
"""

# Manual batch: from {Invoices: [...]} keep pending_receipt + correction_required.
FILTER_CANDIDATES_JS = r"""
const it = $input.first().json;
const invs = it.Invoices || [];
const ids = invs.filter(r => r.status === 'pending_receipt' || r.status === 'correction_required')
  .map(r => r.invoice_id);
if (ids.length === 0) return [];
return [{ json: { mode: 'revalidate_batch', invoice_ids: ids, requested_by: 'manual' } }];
"""

# Collect all write results into a single item (runOnceForAllItems waits for
# every write branch to finish). Without this, the downstream Merge can fire
# on the first write wave while other branches are still running -- its
# second run then has no engine item left to pair and produces nothing,
# which makes the webhook answer "No item to return was found".
COLLECT_WRITES_JS = r"""
// Every incoming item is a write result (mock HTTP response, Sheets row,
// or the no-op passthrough). A failed write must fail the execution
// visibly -- never report success. Failure shapes: {error} (continueOnFail
// connection failure), {statusCode >= 400} (mock HTTP error response),
// {ok: false} / {body: {ok: false}} (mock error body).
const items = $input.all();
for (const it of items){
  const j = (it && it.json) || {};
  const bodyOk = (j.body && typeof j.body === 'object') ? j.body.ok : undefined;
  if (j.error || (typeof j.statusCode === 'number' && j.statusCode >= 400) || j.ok === false || bodyOk === false)
    throw new Error('Write failed: ' + JSON.stringify(j).slice(0, 300));
}
return [{ json: { writes: items.length, ok: true } }];
"""
EXPAND_WRITES_JS = r"""
const it = $input.first().json;
const row = it.invoice_row || null;
const resp = { ok: it.ok !== false, mode: it.mode || '', outcome: it.outcome || '',
  invoice_id: it.invoice_id || '', status: it.status || '', reason_code: it.reason_code || '',
  version: it.version || 0,
  current_review_id: row ? (row.current_review_id || '') : '',
  vendor_invoice_number: row ? (row.vendor_invoice_number || '') : '',
  vendor_id: row ? (row.vendor_id || '') : '',
  total_piastres: row ? (row.total_piastres || 0) : 0,
  submitter_id: row ? (row.submitter_id || '') : '',
  summary_text: (it.summary && it.summary.text) || '', summaries: it.summaries || null,
  confirm_text: (it.confirm && it.confirm.text) || '' };
const out = [];
const invRows = it.invoice_rows || (it.invoice_row ? [it.invoice_row] : []);
for (const r of invRows) out.push({ json: { sheet: 'Invoices', op: 'upsert', match_key: 'invoice_id', row: r, summary: it.summary || {}, resp, mode: it.mode || '', invoice_id: it.invoice_id || '' } });
for (const r of (it.lines_rows || [])) out.push({ json: { sheet: 'Invoice_Lines', op: 'append', row: r, summary: it.summary || {}, resp, mode: it.mode || '', invoice_id: it.invoice_id || '' } });
for (const r of (it.allocation_rows || [])) out.push({ json: { sheet: 'Allocations', op: 'append', row: r, summary: it.summary || {}, resp, mode: it.mode || '', invoice_id: it.invoice_id || '' } });
for (const r of (it.audit_rows || [])) out.push({ json: { sheet: 'Audit_Log', op: 'append', row: r, summary: it.summary || {}, resp, mode: it.mode || '', invoice_id: it.invoice_id || '' } });
if (out.length === 0) out.push({ json: { sheet: 'none', op: 'none', row: {}, summary: it.summary || {}, resp, mode: it.mode || '', invoice_id: it.invoice_id || '' } });
return out;
"""

# Engine webhook response (responseMode lastNode). The response node runs
# after "Merge Engine Output", which pairs the first write result with the
# Matching Engine's own item -- the write nodes' outputs are the mock/Sheets
# responses and do NOT carry the engine's fields, so the response is rebuilt
# from the engine item here (invoice_row, summary, confirm).
BUILD_RESPONSE_JS = r"""
const it = $input.first().json;
const row = it.invoice_row || null;
const s = it.summary || {};
return [{ json: {
  ok: it.ok !== false, mode: it.mode || '', outcome: it.outcome || '',
  invoice_id: it.invoice_id || '', status: it.status || '', reason_code: it.reason_code || '',
  version: it.version || 0,
  current_review_id: row ? (row.current_review_id || '') : '',
  vendor_invoice_number: row ? (row.vendor_invoice_number || '') : '',
  vendor_id: row ? (row.vendor_id || '') : '',
  total_piastres: row ? (row.total_piastres || 0) : 0,
  submitter_id: row ? (row.submitter_id || '') : '',
  summary_text: s.text || '', summaries: it.summaries || null,
  confirm_text: (it.confirm && it.confirm.text) || '' } }];
"""

# Telegram intake: pick the largest photo + sender identity.
PICK_PHOTO_JS = r"""
const it = $input.first().json;
const root = it.body || it;
const msg = root.message || {};
const photos = msg.photo || [];
const file = photos.length ? photos[photos.length - 1] : {};
const from = msg.from || {};
const chat = msg.chat || {};
return [{ json: { file_id: file.file_id || '', from_id: String(from.id || ''), chat_id: String(chat.id || from.id || '') } }];
"""

# OCR fields -> engine match payload (carries from_id/chat_id through).
BUILD_ENGINE_PAYLOAD_JS = r"""
const it = $input.first().json;
const lines = (it.lines || []).map(l => ({ po_line_id: l.po_line_id || '', sku: l.sku || '',
  qty: Number(l.qty || 0), unit_price_pt: Number(l.unit_price_pt || 0), line_total_pt: Number(l.line_total_pt || 0) }));
const invoice = { vendor_id: it.vendor_id || '', vendor_invoice_number: it.vendor_invoice_number || '',
  po_id: it.po_id || '', invoice_date: it.invoice_date || '',
  subtotal_pt: Number(it.subtotal_pt || 0), tax_pt: Number(it.tax_pt || 0), total_pt: Number(it.total_pt || 0),
  document_hash: it.image_hash || '', submitter_id: it.from_id || '' };
return [{ json: { engine_body: { mode: 'match', invoice, lines }, from_id: it.from_id || '', chat_id: it.chat_id || '' } }];
"""

# Review callbacks -> engine apply payload.
# stage 'reason': decision carries action + reason_key.
# stage 'action' with correct/defer: direct apply with a default reason key.
BUILD_APPLY_CALL_JS = r"""
const it = $input.first().json;
let action = it.action || '';
let reasonKey = it.reason_key || '';
if (action === 'correct') { action = 'correction'; reasonKey = reasonKey || 'correction_requested'; }
if (action === 'defer') { reasonKey = reasonKey || 'deferred'; }
const decision = { invoice_id: it.invoice_id || '', version: Number(it.version || 0),
  review_id: it.review_id || '', action, reason_key: reasonKey, from_id: it.from_id || '' };
return [{ json: { engine_body: { mode: 'apply_exception', decision }, chat_id: it.chat_id || '', from_id: it.from_id || '' } }];
"""

# Sender notice after intake: human text from the engine outcome.
BUILD_NOTIFY_JS = r"""
const it = $input.first().json;
const num = it.vendor_invoice_number || it.invoice_id || '';
let text = '';
if (it.status === 'auto_matched') text = 'Invoice ' + num + ' matched (' + (it.reason_code || '') + '). Allocation recorded.';
else if (it.status === 'needs_review') text = 'Invoice ' + num + ' needs human review: ' + (it.reason_code || '') + '. The reviewer has been notified.';
else if (it.status === 'pending_receipt') text = 'Invoice ' + num + ' is waiting for the goods receipt (missing_receipt). It will be revalidated when the receipt arrives.';
else if (it.status === 'blocked') text = 'Invoice ' + num + ' is blocked: ' + (it.reason_code || '') + '.';
else if (it.status === 'duplicate_image') text = 'This document was already submitted; no new invoice was created.';
else text = 'Invoice ' + num + ': ' + (it.summary_text || it.status || '');
return [{ json: { chat_id: it.submitter_id || '', text, mock_body: JSON.stringify({ chat_id: it.submitter_id || '', text }) } }];
"""

# Aggregate: collapse a Sheets read (one item per row) OR a mock HTTP read
# (one item whose .body is the array) into a single item {<key>: [rows]}.
def aggregate_js(key):
    return """const items = $input.all();
let rows = [];
if (items.length === 1 && Array.isArray(items[0].json.body)) {
  rows = items[0].json.body;
} else {
  rows = items.map(i => i.json);
}
return [{ json: { %s: rows } }];
""" % key
