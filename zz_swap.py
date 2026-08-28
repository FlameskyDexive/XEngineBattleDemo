#!/usr/bin/env python3
"""Material A/B: swap Toon material for DefaultLit and compare animation visibility."""
import json, subprocess, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

HAND_ALLY = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Test_")).First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var sk = an.Skeleton;'
    'XEngine.Vector.Transform? hd = null;'
    'for (int i = 0; i < sk.Bones.Length; i++) {'
    '  var b = sk.Bones[i];'
    '  if (b != null && b.GameObject != null && b.GameObject.Name.Contains("R Hand")) { hd = b; break; } }'
    'var w = hd != null ? hd.Position : new XEngine.Vector.Float3(999,999,999);'
    'var info = an.GetCurrentAnimatorStateInfo();'
    'return "nT=" + info.normalizedTime.ToString("F3") + " handW=(" + w.X.ToString("F3") + "," + w.Y.ToString("F3") + "," + w.Z.ToString("F3") + ")";')

SWAP = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_FsmDriven").First();'
    'var litShader = XEngine.Runtime.Resources.Shader.LoadDefault(XEngine.Runtime.Resources.DefaultShader.Standard);'
'var newMat = new XEngine.Runtime.Resources.Material(litShader);'
'newMat.Name = "ABTest";'
    'var lit = new AssetRef<Material>(newMat);'
    'int swapped = 0;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) {'
    '  c.Materials = new System.Collections.Generic.List<AssetRef<Material>> { lit };'
    '  swapped++; }'
    'return "swapped " + swapped + " SMRs to Standard";')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_swap_stdio.log", "ab")
def _drain():
    try:
        for line in proc.stdout:
            elog.write(line); elog.flush()
            s2 = line.decode("utf-8", errors="replace").strip()
            if not s2: continue
            try: m = json.loads(s2)
            except json.JSONDecodeError: continue
            if isinstance(m.get("id"), int):
                _responses[m["id"]] = m
    except Exception:
        pass
threading.Thread(target=_drain, daemon=True).start()

_id = 0
def call_eval(code, timeout=240):
    global _id
    _id += 1
    my = _id
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_eval", "arguments": {"code": code}}}).encode() + b"\n")
    proc.stdin.flush()
    dl = time.time() + timeout
    while time.time() < dl:
        if my in _responses:
            m = _responses.pop(my)
            try:
                c = ((m.get("result") or {}).get("content") or [])
                return "".join(x.get("text") or "" for x in c)
            except Exception:
                pass
        time.sleep(0.05)
    return "TIMEOUT"

def shoot(name):
    global _id
    my = _id + 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_screenshot", "arguments": {"width": 1280, "height": 720}}}).encode() + b"\n")
    proc.stdin.flush()
    _id = my
    dl = time.time() + 240
    while time.time() < dl:
        if my in _responses:
            m = _responses.pop(my)
            try:
                sc = ((m.get("result") or {}).get("structuredContent") or {})
                p2 = sc.get("path")
                if isinstance(p2, str) and p2.endswith(".png"):
                    dst = os.path.join(OUT, name)
                    shutil.copyfile(p2, dst)
                    print(f"[shot] {dst}", flush=True)
            except Exception:
                pass
            return
        time.sleep(0.05)

send0 = call_eval
send0('return "ready";')
print("[open]", send0('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");')[:60], flush=True)
time.sleep(3)
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999001, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(8)

# 1. Toon shot (baseline)
shoot("ab-toon.png")
time.sleep(0.5)
# 2. Swap to DefaultLit
print("[SWAP]", send0(SWAP)[:80], flush=True)
time.sleep(2)
shoot("ab-lit.png")
time.sleep(0.1)
eval_hand_1 = call_eval(HAND_ALLY)
time.sleep(0.6)
eval_hand_2 = call_eval(HAND_ALLY)
print("[lit hand1]", eval_hand_1[:150], flush=True)
print("[lit hand2]", eval_hand_2[:150], flush=True)
time.sleep(1)
shoot("ab-lit-2.png")

try:
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999002, "method": "tools/call",
        "params": {"name": "runtime_playmode", "arguments": {"action": "exit"}}}).encode() + b"\n")
    proc.stdin.flush()
except Exception:
    pass
time.sleep(5)
try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
