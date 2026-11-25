#!/usr/bin/env python3
"""Invoice OCR HTTP service (port 7998, stdlib only).
POST /ocr with raw image bytes -> structured invoice fields + SHA-256
image_hash (success AND error responses). GET /health.
Same contract as Sample 2's ocr-service.py, adapted for invoices.
"""
import hashlib
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from invoice_extract import extract

PORT = 7998


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        d = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/ocr":
            return self._send(404, {"ok": False, "error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        h = hashlib.sha256(body).hexdigest()
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            os.write(fd, body)
            os.close(fd)
            try:
                r = extract(path)
            except Exception as e:
                return self._send(500, {"image_hash": h, "error": str(e), "engine": "tesseract"})
            r["image_hash"] = h
            r.pop("raw_text", None)
            return self._send(200, r)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    # Bind all interfaces (0.0.0.0), not loopback: the documented Docker
    # setup reaches this service via host.docker.internal, which cannot
    # connect to a 127.0.0.1-only listener. Local use is unaffected.
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
