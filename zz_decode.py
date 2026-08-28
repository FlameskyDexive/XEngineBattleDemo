#!/usr/bin/env python3
"""Decode the escaped error line from zz_swap8.log (full-log context)."""
import re

log = open('diag/zz_swap8.log', encoding='utf-8', errors='replace').read()
BS = chr(92)
pattern = re.compile(re.escape(BS + 'u') + r'([0-9a-fA-F]{4})')

i = log.find('CS0234')
seg = log[max(0, i - 80):i + 300]
decoded = pattern.sub(lambda m: chr(int(m.group(1), 16)), seg)
print(decoded)
