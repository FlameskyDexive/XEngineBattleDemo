#!/usr/bin/env python3
"""Deterministic repro: drive PreviewRenderer exactly like ThumbnailGenerator.GenerateFor3D
does (SetupForPrefab → Render), save the RT as PNG for Anbi + Claymore prefabs."""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"
os.makedirs(OUT, exist_ok=True)

RENDER_CODE = """
var e = XEngine.Editor.EditorAssetBackend.Instance.GetEntry("ZZZ/Prefab/Anbi.prefab");
var a = XEngine.Runtime.AssetDatabase.Get(e.Guid);
var prefab = a as XEngine.Runtime.Resources.PrefabAsset;
if (prefab == null) return "not-prefab:" + (a == null ? "null" : a.GetType().Name);
using (var preview = new XEngine.Editor.GUI.PreviewRenderer(256, 256))
{
    preview.SetupForPrefab(prefab);
    preview.Render();
    var rt = preview.Result;
    if (rt == null || rt.MainTexture == null) return "no-rt";
    int w = rt.Width, h = rt.Height;
    byte[] px = new byte[w * h * 4];
    rt.MainTexture.GetData<byte>(px);
    System.IO.File.WriteAllBytes(@"OUTDIR\\anbi-preview-raw.bin", px);
    return "rendered " + w + "x" + h;
}
""".replace("OUTDIR", OUT.replace("\\", "\\\\"))

CLAYMORE_CODE = RENDER_CODE.replace("Anbi.prefab", "Claymore.prefab").replace("anbi-preview-raw.bin", "claymore-preview-raw.bin")

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

def eval_code(code, timeout=300):
    return json.dumps(call_tool("runtime_eval", {"code": code}, timeout))[:400]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-repro", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[anbi]", eval_code(RENDER_CODE), flush=True)
print("[claymore]", eval_code(CLAYMORE_CODE), flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
