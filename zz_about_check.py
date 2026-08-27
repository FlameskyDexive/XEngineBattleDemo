#!/usr/bin/env python3
"""About-dialog functional check."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0

def send(method, params=None):
    global _id
    _id += 1
    msg = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params: msg["params"] = params
    proc.stdin.write(json.dumps(msg).encode() + b"\n"); proc.stdin.flush()
    return _id

def recv(want_id, timeout=300):
    dl = time.time() + timeout
    while time.time() < dl:
        raw = proc.stdout.readline()
        if not raw: return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: m = json.loads(line)
        except json.JSONDecodeError: continue
        if m.get("id") == want_id: return m
    return None

def ev(label, code, timeout=120):
    rid = send("tools/call", {"name": "runtime_eval", "arguments": {"code": code}})
    r = recv(rid, timeout)
    txt = ""
    try:
        c = ((r or {}).get("result") or {}).get("content") or []
        txt = "".join(x.get("text") or "" for x in c)
    except Exception:
        pass
    err = (r or {}).get("error")
    if not txt and err: txt = "ERR " + json.dumps(err)[:200]
    print(f"[{label}] {txt[:300]}", flush=True)

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(42)

ev("ABOUT-MENU-LIST", 'string.Join("|", XEngine.Editor.Core.MenuRegistry.RootMenus.Select(m => m.Label + (m.HasSubItems ? "(+)" : "")))')
ev("ABOUT-INVOKE",
    '(XEngine.Editor.Core.MenuRegistry.RootMenus.Last(m => m.Label == "About").SubItems[0].OnClick ?? throw new Exception("no action")).Invoke(); "clicked"')
time.sleep(1.2)
ev("OPEN?", '"Modal.IsOpen=" + XEngine.OrigamiUI.Modal.IsOpen')
ev("CLOSE", 'XEngine.OrigamiUI.Modal.Pop(); "popped"')

try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
