#!/usr/bin/env python3
"""Stamp Unity globalScale 100 onto engine PlayerModel FBX metas (Echo unitScale)."""
import os
import re

root = r"F:\Git\XEngine\ZonezeroTestProject\Assets\ZZZ\Arts\PlayerModel"
n = 0
for dirpath, _, files in os.walk(root):
    for f in files:
        if not f.lower().endswith(".fbx.meta"):
            continue
        path = os.path.join(dirpath, f)
        text = open(path, encoding="utf-8").read()
        if '"unitScale": 1F' not in text:
            m = re.search(r'"unitScale":\s*[^,\n]+', text)
            print("skip", path, m.group(0) if m else "NO SCALE")
            continue
        text2 = text.replace('"unitScale": 1F', '"unitScale": 100F')
        open(path, "w", encoding="utf-8", newline="\n").write(text2)
        n += 1
print("patched", n)
for name in ("Anbi.FBX.meta", "Corin.FBX.meta", "Nostradamus.FBX.meta"):
    for dirpath, _, files in os.walk(root):
        if name in files:
            t = open(os.path.join(dirpath, name), encoding="utf-8").read()
            print(name, re.search(r'"unitScale":\s*[^,\n]+', t).group(0))
clay = r"F:\Git\XEngine\ZonezeroTestProject\Assets\ZZZ\Arts\EnemyModel\Claymore\Claymore.fbx.meta"
t = open(clay, encoding="utf-8").read()
print("Claymore", re.search(r'"unitScale":\s*[^,\n]+', t).group(0))
