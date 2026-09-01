#!/usr/bin/env python3
"""M2 toon acceptance: regen zonezero native assets (mats now Default/Toon), rebuild combat
scene, capture GameView screenshots on BOTH backends (GL default, --graphics=vulkan)."""
import json, subprocess, sys, time, os, shutil, argparse

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

def run_backend(tag, extra_args, shots):
    proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT] + extra_args,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=os.path.dirname(EDITOR))
    _id = 0
    def send(method, params=None, notify=False):
        nonlocal _id
        msg = {"jsonrpc": "2.0", "method": method}
        if params: msg["params"] = params
        if not notify:
            _id += 1; msg["id"] = _id
        proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n"); proc.stdin.flush()
        return _id if not notify else None
    def recv(want_id, timeout=600):
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = proc.stdout.readline()
            if not raw: return None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line: continue
            try: msg = json.loads(line)
            except json.JSONDecodeError: continue
            if msg.get("id") == want_id: return msg
        return None
    def call_tool(name, args, timeout=600):
        rid = send("tools/call", {"name": name, "arguments": args})
        reply = recv(rid, timeout)
        if reply is None: raise TimeoutError(name)
        return reply["result"]

    rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "zz-m2-" + tag, "version": "1"}})
    if recv(rid, 300) is None:
        print(f"[{tag}] INIT_FAILED"); proc.kill(); return False
    send("notifications/initialized", notify=True)
    time.sleep(40)

    try:
        # regenerate native assets (YSA mats -> Default/Toon) + rebuild the combat scene
        print(f"[{tag}][gen]", json.dumps(call_tool("runtime_menu",
            {"action": "invoke", "path": "Zonezero/Generate Native Assets"}, 1200))[:150], flush=True)
        time.sleep(3)
        print(f"[{tag}][build]", json.dumps(call_tool("runtime_menu",
            {"action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 900))[:150], flush=True)
        time.sleep(3)
        # open + focus the game view
        call_tool("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, 120)
        time.sleep(4)
        for shot_name in shots:
            shot = call_tool("runtime_screenshot", {"width": 1280, "height": 720}, 240)
            blob = json.dumps(shot)
            paths = [f for f in blob.split('"') if f.endswith(".png")]
            if paths:
                src = max(paths, key=os.path.getmtime)
                dst = os.path.join(OUT, shot_name)
                shutil.copyfile(src, dst)
                print(f"[{tag}] saved {shot_name} {os.path.getsize(dst)}B", flush=True)
        logs = call_tool("runtime_logs", {"count": 400, "level": "Error"}, 120)
        errs = json.dumps(logs)
        print(f"[{tag}][errors] {errs[:400]}", flush=True)
    finally:
        try:
            proc.stdin.close(); proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return True

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gl", "vk"], required=True)
    args = ap.parse_args()
    if args.backend == "gl":
        run_backend("gl", [], ["m2-toon-gl.png"])
    else:
        run_backend("vk", ["--graphics=vulkan"], ["m2-toon-vk.png"])
