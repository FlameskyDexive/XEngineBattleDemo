#!/usr/bin/env python3
"""Diagnose: how long does script compilation take, and do the Zonezero menus register?"""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0

def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params: msg["params"] = params
    if not notify:
        _id += 1; msg["id"] = _id
    proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n"); proc.stdin.flush()
    return _id if not notify else None

def recv(want_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = proc.stdout.readline()
        if not raw: return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError:
            print("[editor]", line[:200], flush=True); continue
        if msg.get("id") == want_id: return msg
    return None

def call_tool(name, args, timeout=900):
    rid = send("tools/call", {"name": name, "arguments": args})
    reply = recv(rid, timeout)
    if reply is None: raise TimeoutError(name)
    return reply["result"]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-diag2", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)

# Poll every 30s with a long per-call timeout; report when menus appear.
deadline = time.time() + 900
while time.time() < deadline:
    try:
        blob = json.dumps(call_tool("runtime_menu", {"action": "list"}, 900))
    except TimeoutError:
        print("[poll] menu list timed out", flush=True)
        continue
    if "Zonezero" in blob:
        print("[poll] ZONEZERO MENUS VISIBLE", flush=True)
        idx = blob.find("Zonezero")
        print("[menus]", blob[max(0, idx - 50):idx + 400], flush=True)
        break
    print("[poll] not yet", flush=True)
    time.sleep(20)
else:
    print("[poll] gave up waiting for menus", flush=True)

try:
    logs = call_tool("runtime_logs", {"minimumSeverity": "Warning"}, 120)
    print("[warnings]", json.dumps(logs)[:4000], flush=True)
except TimeoutError:
    print("[warnings] timed out", flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
