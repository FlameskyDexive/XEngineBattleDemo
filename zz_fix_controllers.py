#!/usr/bin/env python3
"""Strip motion-less leftover 'New State' states from generated .controller Echo assets
and repoint DefaultStateName at Idle. Complements the ZonezeroControllerGenerator fix."""
import re

PATHS = [
    r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/Arts/PlayerModel/Anbi/Anbi.controller",
    r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/Arts/PlayerModel/Corlin/Corin.controller",
    r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/Arts/PlayerModel/Nicole/Nike.controller",
    r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/Arts/PlayerModel/Corin/Corin.controller",
]

def splice_array(txt: str):
    sm = re.search(r'"States":\s*\{', txt)
    if not sm:
        return None
    am = re.compile(r'"\$values":\s*\[').search(txt, sm.end())
    arr_start = am.end()
    d = 1
    j = arr_start
    in_str = False
    esc = False
    BS = chr(92)
    while j < len(txt) and d > 0:
        c = txt[j]
        if in_str:
            if esc:
                esc = False
            elif c == BS:
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '[':
                d += 1
            elif c == ']':
                d -= 1
        j += 1
    return arr_start, j - 1

for p in PATHS:
    try:
        txt = open(p, encoding="utf-8").read()
    except FileNotFoundError:
        print("skip missing", p)
        continue
    span = splice_array(txt)
    if span is None:
        print("pattern miss", p)
        continue
    a, b = span
    body = txt[a:b]
    objs = []
    depth = 0
    start = None
    for k, ch in enumerate(body):
        if ch == '{':
            if depth == 0:
                start = k
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                objs.append(body[start:k + 1])
                start = None

    def name_of(o):
        mm = re.search(r'"Name":\s*"([^"]*)"', o)
        return mm.group(1) if mm else None

    kept = [o for o in objs if name_of(o) != "New State"]
    removed = len(objs) - len(kept)
    nb = ""
    for kk, o in enumerate(kept):
        nb += ("," if kk else "") + "\n      " + o
    nb += "\n    "
    txt2 = txt[:a] + nb + txt[b:]
    txt2 = txt2.replace('"DefaultStateName": "New State"', '"DefaultStateName": "Idle"')
    open(p, "w", encoding="utf-8", newline="\n").write(txt2)
    tag = p.replace(chr(92), "/").split("/")[-2]
    print(f"{tag}: total={len(objs)} removed={removed} now={len(kept)} default->Idle")
