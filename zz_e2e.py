#!/usr/bin/env python3
"""E2E combat validation: real OS keyboard/mouse into the focused editor GameView.
Phases: idle-ground stability -> hold W run smoothness+latency -> space switch -> LMB attack combo.
Samples live state between phases via runtime_eval."""
import json, subprocess, sys, time, os, shutil, re, ctypes

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

user32 = ctypes.windll.user32  # kept for foreground checks only

KEYC = {"w": "W", "s": "S", "a": "A", "d": "D", "space": "Space", "lshift": "LeftShift"}
def inj_key(k, down):
    code = KEYC[k]
    fn = "Press" if down else "Release"
    call("runtime_eval_noq" if False else "runtime_eval",
         {"code": f"XEngine.Runtime.InputInjector.{fn}(XEngine.Runtime.KeyCode.{code}); \"{fn} {code}\""}, 60)
def key_down(k): inj_key(k, True)
def key_up(k): inj_key(k, False)
def lmb_down(): call("runtime_eval", {"code": "XEngine.Runtime.InputInjector.SetMouseButton(0, true); \"LMB down\""}, 60)
def lmb_up(): call("runtime_eval", {"code": "XEngine.Runtime.InputInjector.SetMouseButton(0, false); \"LMB up\""}, 60)

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

def call(name, args, timeout=600):
    rid = send("tools/call", {"name": name, "arguments": args})
    reply = recv(rid, timeout)
    if reply is None: raise TimeoutError(name)
    res = reply.get("result")
    if reply.get("error"): raise RuntimeError(f"{name}: {reply['error']}")
    sc = (res or {}).get("structuredContent")
    if isinstance(sc, dict) and "path" in sc:
        return sc["path"]
    return "".join((c.get("text") or "") for c in (res or {}).get("content", []))

def ev(label, code, timeout=120):
    rid = send("tools/call", {"name": "runtime_eval", "arguments": {"code": code}})
    reply = recv(rid, timeout)
    if reply is None:
        print(f"[{label}] TIMEOUT")
        return ""
    err = reply.get("error")
    if err:
        if label[0].isupper() or len(err) < 90:
            pass
        txt_err = json.dumps(err)[:240]
        last_prints.append(txt_err) if False else None
        _ERRS.append((label, txt_err))
        return ""
    content = (reply.get("result") or {}).get("content", [])
    txt = "".join((c.get("text") or "") for c in content)
    if label[0].isupper():
        print(f"[{label}] {txt[:900]}")
    elif not txt or not txt.startswith(("idx=",)):
        _ERRS.append((label, txt[:200] or "EMPTY"))
    return txt

_ERRS: list[tuple[str, str]] = []

SAMPLE = '''
object pc = Scene.Current.AllObjects.SelectMany(o => o.GetComponents())
    .FirstOrDefault(c => c.GetType().Name == "PlayerController");
if (pc == null) return "PC=null";
System.Func<object, string, object> Memb = (o, n) => {
    var ty = o.GetType();
    var pi = ty.GetProperty(n);
    if (pi != null) return pi.GetValue(o);
    var fi = ty.GetField(n);
    return fi != null ? fi.GetValue(o) : null;
};
var pmRaw = Memb(pc, "Model");
if (pmRaw == null) return "PM=null";
int idx = (int)(Memb(pc, "CurrentModelIndex") ?? -1);
string st = (Memb(pmRaw, "CurrentState") ?? "?").ToString();
var mvObj = Memb(pc, "InputMove");
XEngine.Vector.Float2 mv = mvObj is XEngine.Vector.Float2 f2 ? f2 : new XEngine.Vector.Float2(-9,-9);
var go = ((XEngine.Runtime.MonoBehaviour)pmRaw).GameObject;
if (go == null) return "GO=null";
var a = go.GetComponent<XEngine.Runtime.Animator>();
var cc = go.GetComponent<XEngine.Runtime.CharacterController>();
var sb4 = new System.Text.StringBuilder();
sb4.Append("idx=").Append(idx).Append(" char=").Append(go.Name).Append(" st=").Append(st)
   .Append(" mv=(").Append(mv.X.ToString("F2")).Append(',').Append(mv.Y.ToString("F2")).Append(')');
if (a != null) {
    sb4.Append(" fsm=").Append(a.Runtime != null ? 1 : 0)
       .Append(" clip=").Append(a.CurrentClip != null ? a.CurrentClip.Name : "-");
    var info = a.GetCurrentAnimatorStateInfo();
    sb4.Append(" nT=").Append(info.normalizedTime.ToString("F3"));
}
if (cc != null) {
    var P = cc.Transform.Position;
    sb4.Append(" p=").Append(P.X.ToString("F4")).Append(',').Append(P.Y.ToString("F4")).Append(',')
       .Append(P.Z.ToString("F4")).Append(" gnd=").Append(cc.IsGrounded ? 1 : 0);
}
sb4.ToString()'''

def phase(label, seconds, gap=0.25, quiet=True):
    print(f"--- {label}")
    rows = []
    t_end = time.time() + seconds
    i = 0
    while time.time() < t_end:
        out = ev(f"s{i}" if quiet else f"S{i}", SAMPLE)
        mm = re.search(r"p=([-0-9.e]+),([-0-9.e]+),([-0-9.e]+)", out)
        if mm:
            rows.append((time.time(), [float(v) for v in mm.groups()], out))
        elif i < 3 or 'rr' not in out and i % 5 == 0:
            print(f"   raw[{i}]: {out[:220]}")
        i += 1
        time.sleep(gap)
    return rows

try:
    rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "zz-e2e", "version": "1"}})
    recv(rid, 300); send("notifications/initialized", notify=True)
    time.sleep(40)

    # Regenerate native assets (clean controllers: no empty default state), then build+load
    # the combat demo scene through the project's own pipeline — deterministic vs boot races.
    # The user-script assembly may finish compiling seconds after boot: retry until the menu
    # service can see it.
    def invoke_retry(path, timeout=1200, tries=6):
        last = ""
        for k in range(tries):
            r = call("runtime_menu", {"action": "invoke", "path": path}, timeout)
            if "not found" not in r:
                return r
            last = r
            print(f"[retry {k+1}] {path} not visible yet", flush=True)
            time.sleep(15)
        raise RuntimeError(f"menu never appeared: {last[:100]}")

    print("[gen]", json.dumps(invoke_retry("Zonezero/Generate Native Assets"))[:150], flush=True)
    time.sleep(3)
    print("[build]", json.dumps(invoke_retry("Zonezero/Build Combat Demo Scene", timeout=900))[:150], flush=True)
    time.sleep(5)

    # Assert we're really in the combat scene before entering play.
    got = ev("ASSERT", '''
object pc = Scene.Current.AllObjects.SelectMany(o => o.GetComponents())
    .FirstOrDefault(c => c.GetType().Name == "PlayerController");
pc == null ? "NO PLAYERCONTROLLER" : "PC ok, chars=" +
    string.Join(",", Scene.Current.AllObjects.SelectMany(o => o.GetComponents())
        .Where(c => c.GetType().Name == "PlayerModel")
        .Select(m => ((XEngine.Runtime.MonoBehaviour)m).GameObject.Name))''')
    if "NO PLAYERCONTROLLER" in got:
        raise RuntimeError("combat scene not loaded")

    rid = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
    recv(rid, 300)
    # open + focus game view so SendInput reaches the right window
    call("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, 120)
    time.sleep(6)

    hwnd = user32.GetForegroundWindow()
    print("[fg]", bool(hwnd))

    # Phase A: idle ground stability (no keys)
    idle_rows = phase("PHASE-A idle ground stability", 6)

    # Phase B: hold W — run forward
    print("--- PHASE-B hold W (run)")
    run_rows = []
    key_down('w'); t0 = time.time(); first_move_t = None; prev_p = None
    while time.time() - t0 < 8:
        out = ev(f"r{len(run_rows)}", SAMPLE)
        mm = re.search(r"p=([-0-9.e]+),([-0-9.e]+),([-0-9.e]+)", out)
        nm = re.search(r"clip=(\S+)", out)
        if mm:
            p = [float(v) for v in mm.groups()]
            now = time.time()
            if prev_p is not None and first_move_t is None:
                dxz = ((p[0]-prev_p[0])**2 + (p[2]-prev_p[2])**2) ** 0.5
                if dxz > 0.004: first_move_t = now - t0
            prev_p = p
            run_rows.append((now, p, out))
        time.sleep(0.22)
    key_up('w')
    print(f"[latency] first horizontal move after {first_move_t:.2f}s" if first_move_t else "[latency] NO MOVEMENT detected")

    def analyze(rows, tag):
        ys = [r[1][1] for r in rows]
        dzs = []
        for i in range(1, len(rows)):
            dt = rows[i][0] - rows[i-1][0]
            dx = rows[i][1][0] - rows[i-1][1][0]
            dz = rows[i][1][2] - rows[i-1][1][2]
            dy = rows[i][1][1] - rows[i-1][1][1]
            dzs.append((dx/dt if dt else 0, dy/dt if dt else 0, dz/dt if dt else 0, rows[i][2]))
        hs = [abs(v[0])+abs(v[2]) for v in dzs]
        vy = [abs(v[1]) for v in dzs]
        y_range = max(ys)-min(ys) if ys else 0
        if not hs:
            print(f"[{tag}] NO SAMPLES")
            return
        stuck = sum(1 for h in hs if h < 0.004)
        spikes = sum(1 for v in vy if v > 0.5)
        print(f"[{tag}] n={len(rows)} Y-range={y_range:.4f}m still-samples={stuck}/{len(hs)} "
              f"|v| median={sorted(hs)[len(hs)//2]:.2f} max={max(hs) if hs else 0:.2f} bigDY={spikes}")
        signs = [1 if (v[0]+v[2]) > 0.004 else (-1 if (v[0]+v[2]) < -0.004 else 0) for v in dzs]
        flips = sum(1 for i in range(1,len(signs)) if signs[i]!=0 and signs[i-1]!=0 and signs[i]!=signs[i-1])
        print(f"[{tag}] direction-flips={flips}")
        import collections as C
        sts = C.Counter(re.search(r" st=(\S+)", r[2]).group(1) if re.search(r" st=(\S+)", r[2]) else "?" for r in rows)
        mvs = C.Counter((re.search(r" mv=\((-?[0-9.]+),(-?[0-9.]+)\)", r[2]).groups() if re.search(r" mv=\(-?[0-9.]+,-?[0-9.]+\)", r[2]) else ("?","?")) for r in rows)
        clips = C.Counter((re.search(r" clip=(\S+)", r[2]).group(1)) if re.search(r" clip=(\S+)", r[2]) else "?" for r in rows)
        fsms = C.Counter(("fsm="+re.search(r"fsm=(\d)", r[2]).group(1)) if re.search(r"fsm=(\d)", r[2]) else "fsm=?" for r in rows)
        print(f"[{tag}] states={dict(sts)}")
        print(f"[{tag}] inputs={dict(mvs)}")
        print(f"[{tag}] clips={dict(clips)}")
        print(f"[{tag}] {dict(fsms)}")

    analyze(run_rows, "run")
    analyze(idle_rows, "idle")

    # Phase C: space switch
    print("--- PHASE-C space switch")
    key_down('space'); time.sleep(0.08); key_up('space')
    sw_rows = phase("after-space", 4)

    # screenshot
    sp = call("runtime_screenshot", {"width": 1280, "height": 720}, 240)
    if isinstance(sp, str) and sp.endswith(".png"):
        shutil.copyfile(sp, os.path.join(OUT, "zz-e2e-mid.png")); print("[shot] mid ok")

    # Phase D: LMB attacks (combo) on new character
    print("--- PHASE-D attack combo")
    for k in range(4):
        lmb_down(); time.sleep(0.06); lmb_up()
        time.sleep(0.55)
    atk_rows = phase("after-attacks", 3)

    last_states = [r[2][:110] for r in (idle_rows[-1:] + run_rows[-1:] + sw_rows[-1:] + atk_rows[-1:])]
    print("[last-sample-by-phase]")
    for s in last_states: print("  ", s)

    print("[ERRS]", len(_ERRS))
    for lb, tx in _ERRS[:6]:
        print(f"  {lb}: {tx}")

    sp2 = call("runtime_screenshot", {"width": 1280, "height": 720}, 240)
    if isinstance(sp2, str) and sp2.endswith(".png"):
        shutil.copyfile(sp2, os.path.join(OUT, "zz-e2e-final.png")); print("[shot] final ok")

    rid = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "exit"}})
    recv(rid, 120)
finally:
    key_up('w')
    try:
        proc.stdin.close(); proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
