#!/usr/bin/env python3
"""Export check: import the REAL Invoice Processor workflows into local n8n
2.48.7 and export them back; report parameter/connection differences
(n8n-assigned webhookIds and credential ids are ignored). Mirrors Sample 2's
export check. Run: python3 export_check_invoice.py
Writes: exported-from-n8n/invoice-*.json (the exported copies).
"""
import json, pathlib, subprocess, urllib.request, urllib.error

N8N = "http://127.0.0.1:5678"
SAMPLE = pathlib.Path(__file__).parent
OUT = SAMPLE / "exported-from-n8n"
OUT.mkdir(exist_ok=True)

subprocess.run(["curl", "-s", "-m", "10", "-c", "/tmp/n8n-cookies.txt", "-X", "POST",
                N8N + "/rest/login", "-H", "Content-Type: application/json",
                "-d", json.dumps({"emailOrLdapLoginId": "test@example.local",
                                  "password": "TestLocal123!"})],
               check=True, capture_output=True)
COOKIE = None
for line in pathlib.Path("/tmp/n8n-cookies.txt").read_text().splitlines():
    if "n8n-auth" in line:
        COOKIE = "n8n-auth=" + line.split()[-1]

def n8n(method, path, data=None):
    r = urllib.request.Request(N8N + path,
                               data=json.dumps(data).encode() if data is not None else None,
                               headers={"Content-Type": "application/json", "Cookie": COOKIE},
                               method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        out = json.loads(resp.read() or b"{}")
        return out.get("data", out)

def clean_node(n):
    n = dict(n)
    n.pop("webhookId", None)
    n.pop("credentials", None)
    return n

FILES = ["invoice-matching-engine-workflow.json", "invoice-telegram-workflow.json"]
all_ok = True
for fname in FILES:
    src = json.loads((SAMPLE / fname).read_text())
    src.pop("meta", None)
    created = n8n("POST", "/rest/workflows", src)
    wid = created["id"]
    got = n8n("GET", f"/rest/workflows/{wid}")
    (OUT / fname).write_text(json.dumps(got, ensure_ascii=False, indent=2), encoding="utf-8")
    src_nodes = {n["name"]: clean_node(n) for n in src["nodes"]}
    got_nodes = {n["name"]: clean_node(n) for n in got["nodes"]}
    param_diffs = []
    for name, sn in src_nodes.items():
        gn = got_nodes.get(name)
        if gn is None:
            param_diffs.append(f"missing node {name}")
            continue
        if sn.get("parameters") != gn.get("parameters"):
            param_diffs.append(name)
        if sn.get("type") != gn.get("type") or sn.get("typeVersion") != gn.get("typeVersion"):
            param_diffs.append(name + " (type/version)")
        if sn.get("position") != gn.get("position"):
            param_diffs.append(name + " (position)")
    conn_same = src.get("connections") == got.get("connections")
    ok = not param_diffs and conn_same and set(src_nodes) == set(got_nodes)
    all_ok = all_ok and ok
    print(f"{fname}: nodes {len(src_nodes)} -> {len(got_nodes)} | "
          f"param diffs: {param_diffs or 'none'} | connections equal: {conn_same} | {'OK' if ok else 'DIFF'}")
print("ALL OK" if all_ok else "DIFFERENCES FOUND")
