#!/usr/bin/env node
/* Invoice Processor (Sample 3) — local mock server (port 7999).
   In-memory Google Sheets (7 tabs), Telegram (send + getFile by fixture id),
   and MOCK OCR (fixture by image hash). The mock OCR returns FIXED
   extractions from fixtures.json, including one simulated defect
   (inv-012 line total misread 6000 instead of 5000) -- it proves
   processing logic only. Real Tesseract accuracy is measured separately.
   Run: node mock-server.js   (from the sample3 directory)
*/
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = 7999;
const BASE = __dirname;
const FIXTURES = JSON.parse(fs.readFileSync(path.join(BASE, 'fixtures.json'), 'utf8'));

const VENDORS_SEED = [
  { vendor_id: 'VND-001', vendor_name: 'Nile Office Supplies', tax_id: 'TAX-1001', status: 'active' },
  { vendor_id: 'VND-002', vendor_name: 'Delta Trading', tax_id: 'TAX-1002', status: 'active' },
  { vendor_id: 'VND-003', vendor_name: 'Cairo Paper Co', tax_id: 'TAX-1003', status: 'active' },
];
const PO_SEED = [
  { po_id: 'PO-001', po_line_id: 'PO-001-L1', vendor_id: 'VND-001', sku: 'NOTE-A5', description: 'Notebook A5', qty_ordered: 100, unit_price_piastres: 2000, tax_rate_pct: 14, currency: 'EGP', status: 'open' },
  { po_id: 'PO-001', po_line_id: 'PO-001-L2', vendor_id: 'VND-001', sku: 'PEN-BLUE', description: 'Pen blue', qty_ordered: 50, unit_price_piastres: 500, tax_rate_pct: 14, currency: 'EGP', status: 'open' },
  { po_id: 'PO-002', po_line_id: 'PO-002-L1', vendor_id: 'VND-002', sku: 'TONER-X', description: 'Toner cartridge X', qty_ordered: 10, unit_price_piastres: 15000, tax_rate_pct: 14, currency: 'EGP', status: 'open' },
  { po_id: 'PO-002', po_line_id: 'PO-002-L2', vendor_id: 'VND-002', sku: 'CABLE-USB', description: 'USB cable', qty_ordered: 200, unit_price_piastres: 750, tax_rate_pct: 0, currency: 'EGP', status: 'open' },
  { po_id: 'PO-003', po_line_id: 'PO-003-L1', vendor_id: 'VND-001', sku: 'PAPER-A4', description: 'Paper A4 ream', qty_ordered: 7, unit_price_piastres: 1999, tax_rate_pct: 14, currency: 'EGP', status: 'open' },
  { po_id: 'PO-004', po_line_id: 'PO-004-L1', vendor_id: 'VND-002', sku: 'STAPLER', description: 'Stapler', qty_ordered: 20, unit_price_piastres: 3000, tax_rate_pct: 14, currency: 'EGP', status: 'open' },
];
const RECEIPTS_SEED = [
  { receipt_id: 'RCV-001', po_line_id: 'PO-001-L1', qty_accepted: 100, received_date: '2026-11-20', status: 'active' },
  { receipt_id: 'RCV-002', po_line_id: 'PO-001-L2', qty_accepted: 50, received_date: '2026-11-20', status: 'active' },
  { receipt_id: 'RCV-003', po_line_id: 'PO-002-L1', qty_accepted: 10, received_date: '2026-11-20', status: 'active' },
  { receipt_id: 'RCV-004', po_line_id: 'PO-002-L2', qty_accepted: 200, received_date: '2026-11-20', status: 'active' },
  { receipt_id: 'RCV-005', po_line_id: 'PO-003-L1', qty_accepted: 7, received_date: '2026-11-20', status: 'active' },
  // NOTE: PO-004-L1 has NO receipt initially — S10 adds RCV-006 during the test.
];

let sheets = { Vendors: [], PO_Lines: [], Receipt_Lines: [], Invoices: [], Invoice_Lines: [], Allocations: [], Audit_Log: [] };
let telegramLog = [];
let callLog = [];
// Test-only switch (T-WRITEFAIL): when true, every Sheets write fails
// with 500 and writes nothing. Cleared by /reset.
let failWrites = false;

const COLS = {
  Vendors: ['vendor_id', 'vendor_name', 'tax_id', 'status'],
  PO_Lines: ['po_id', 'po_line_id', 'vendor_id', 'sku', 'description', 'qty_ordered', 'unit_price_piastres', 'tax_rate_pct', 'currency', 'status'],
  Receipt_Lines: ['receipt_id', 'po_line_id', 'qty_accepted', 'received_date', 'status'],
  Invoices: ['invoice_id', 'vendor_id', 'vendor_invoice_number', 'document_hash', 'status', 'reason_code', 'version', 'current_review_id', 'submitter_id', 'subtotal_piastres', 'tax_piastres', 'total_piastres', 'created_at', 'updated_at'],
  Invoice_Lines: ['invoice_id', 'line_no', 'po_line_id', 'sku', 'qty', 'unit_price_piastres', 'line_total_piastres', 'tax_piastres'],
  Allocations: ['allocation_id', 'invoice_id', 'po_line_id', 'qty', 'status', 'created_at'],
  Audit_Log: ['audit_id', 'invoice_id', 'old_status', 'new_status', 'reason_code', 'decided_by', 'note', 'created_at'],
};
function cleanRow(sheet, row) {
  const allow = COLS[sheet] || Object.keys(row);
  const out = {};
  for (const k of allow) out[k] = row[k] === undefined ? '' : row[k];
  return out;
}

function seed() {
  sheets = {
    Vendors: JSON.parse(JSON.stringify(VENDORS_SEED)),
    PO_Lines: JSON.parse(JSON.stringify(PO_SEED)),
    Receipt_Lines: JSON.parse(JSON.stringify(RECEIPTS_SEED)),
    Invoices: [],
    Invoice_Lines: [],
    Allocations: [],
    Audit_Log: [],
  };
  telegramLog = [];
  callLog = [];
}
seed();

function body(req) {
  return new Promise((res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => res(Buffer.concat(chunks)));
  });
}
function json(res, code, obj) {
  const d = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(d) });
  res.end(d);
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x');
  const p = u.pathname;
  try {
    if (req.method === 'GET' && p === '/health') return json(res, 200, { ok: true });
    if (req.method === 'GET' && p === '/log')
      return json(res, 200, { sheets, telegram: telegramLog, calls: callLog });
    if (req.method === 'POST' && p === '/reset') { failWrites = false; seed(); return json(res, 200, { ok: true }); }
    // Test-only switch for T-WRITEFAIL: {enabled: true/false}.
    if (req.method === 'POST' && p === '/fail-writes') {
      const { enabled } = JSON.parse((await body(req)).toString() || '{}');
      failWrites = !!enabled;
      return json(res, 200, { ok: true, failWrites });
    }

    if (req.method === 'POST' && p === '/debug-echo') {
      const raw = (await body(req)).toString();
      let parsed = raw;
      try { parsed = JSON.parse(raw); } catch (e) { /* keep raw */ }
      callLog.push({ path: '/debug-echo', body: parsed });
      return json(res, 200, { ok: true });
    }
    if (req.method === 'POST' && p === '/sheets/read') {
      const { sheet } = JSON.parse((await body(req)).toString() || '{}');
      return json(res, 200, sheets[sheet] || []);
    }
    if (req.method === 'POST' && p === '/sheets/append') {
      if (failWrites) return json(res, 500, { ok: false, error: 'injected write failure' });
      const { sheet, row } = JSON.parse((await body(req)).toString() || '{}');
      const clean = cleanRow(sheet, row);
      sheets[sheet].push(clean);
      callLog.push({ path: '/sheets/append', sheet, keys: Object.keys(row) });
      return json(res, 200, { ok: true, ...clean });
    }
    if (req.method === 'POST' && p === '/sheets/appendOrUpdate') {
      if (failWrites) return json(res, 500, { ok: false, error: 'injected write failure' });
      const { sheet, row, matchKey } = JSON.parse((await body(req)).toString() || '{}');
      // Update ONLY the keys the caller sent (never merge ''-filled rows over
      // stored rows -- that blanked columns in Sample 2 round 1).
      const i = sheets[sheet].findIndex((r) => String(r[matchKey]) === String(row[matchKey]));
      if (i >= 0) {
        for (const k of Object.keys(row)) sheets[sheet][i][k] = row[k];
        return json(res, 200, { ok: true, updated: true, ...sheets[sheet][i] });
      }
      const clean = cleanRow(sheet, row);
      sheets[sheet].push(clean);
      return json(res, 200, { ok: true, updated: false, ...clean });
    }
    // Add a goods receipt row (used by S10: the missing receipt arrives).
    if (req.method === 'POST' && p === '/seed/receipt') {
      const row = JSON.parse((await body(req)).toString() || '{}');
      sheets.Receipt_Lines.push(cleanRow('Receipt_Lines', row));
      return json(res, 200, { ok: true });
    }
    // Correct stored invoice rows (used by S12/S14: operator fixes values in Sheets).
    if (req.method === 'POST' && p === '/sheets/correct') {
      const { invoice_id, invoice_fields, lines } = JSON.parse((await body(req)).toString() || '{}');
      const inv = sheets.Invoices.find((r) => r.invoice_id === invoice_id);
      if (!inv) return json(res, 404, { ok: false, error: 'invoice not found' });
      if (invoice_fields) for (const k of Object.keys(invoice_fields)) inv[k] = invoice_fields[k];
      if (lines) {
        for (const fix of lines) {
          const ln = sheets.Invoice_Lines.find((r) => r.invoice_id === invoice_id && Number(r.line_no) === Number(fix.line_no));
          if (ln) for (const k of Object.keys(fix)) if (k !== 'line_no') ln[k] = fix[k];
        }
      }
      return json(res, 200, { ok: true });
    }
    // ---- Telegram mock ----
    if (req.method === 'POST' && p === '/telegram/sendMessage') {
      const b = JSON.parse((await body(req)).toString() || '{}');
      telegramLog.push({ chat_id: String(b.chat_id || ''), text: b.text || '', reply_markup: b.reply_markup || null });
      return json(res, 200, { ok: true, result: { message_id: telegramLog.length } });
    }
    if (req.method === 'GET' && p === '/telegram/getFile') {
      const fileId = u.searchParams.get('file_id') || '';
      const m = /^fixture:(.+)$/.exec(fileId);
      if (!m) return json(res, 404, { ok: false, error: 'unknown file_id' });
      const fp = path.join(BASE, 'fixtures', m[1] + '.png');
      if (!fs.existsSync(fp)) return json(res, 404, { ok: false, error: 'fixture not found' });
      const data = fs.readFileSync(fp);
      res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': data.length });
      return res.end(data);
    }
    // ---- Mock OCR: returns the fixture's ground truth (or its override) ----
    if (req.method === 'POST' && p === '/ocr') {
      const raw = await body(req);
      const hash = crypto.createHash('sha256').update(raw).digest('hex');
      const fx = FIXTURES[hash];
      if (!fx) return json(res, 200, { image_hash: hash, error: 'unknown_image', engine: 'mock-ocr' });
      const out = JSON.parse(JSON.stringify(fx.ground_truth));
      if (fx.mock_override) {
        for (const k of Object.keys(fx.mock_override)) out[k] = fx.mock_override[k];
      }
      out.image_hash = hash;
      out.engine = 'mock-ocr';
      out.confidence = fx.mock_override && fx.mock_override.confidence !== undefined ? fx.mock_override.confidence : 95;
      return json(res, 200, out);
    }
    return json(res, 404, { ok: false, error: 'not found' });
  } catch (e) {
    return json(res, 500, { ok: false, error: String(e && e.message || e) });
  }
});

server.listen(PORT, '127.0.0.1', () => console.log('sample3 mock on 7999'));
