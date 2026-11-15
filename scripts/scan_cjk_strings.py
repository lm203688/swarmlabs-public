"""Scan a JS file with a real char-level lexer and extract every string
literal that contains CJK / full-width characters.

Regex-based extraction is unreliable here (misses single-quoted strings,
strings containing the other quote char, and escaped sequences), so this
walks the source tracking code / string / comment state.

Usage:
    python scripts/scan_cjk_strings.py <file> [--json out.json]
"""

from __future__ import annotations

import bisect
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
FULLWIDTH = re.compile(r"[！-～＀-￯　-〿]")


def scan(src: str):
    """Return a list of (offset, literal_text, quote_char)."""
    out = []
    i, n = 0, len(src)
    state = "code"
    quote = None
    start = 0
    buf: list[str] = []

    while i < n:
        c = src[i]
        if state == "code":
            if c == "/" and i + 1 < n and src[i + 1] == "/":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == "/" and i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "\"'`":
                state, quote, start, buf = "str", c, i, []
                i += 1
                continue
            i += 1
            continue

        # inside a string literal
        if c == "\\":
            buf.append(src[i : i + 2])
            i += 2
            continue
        if c == quote:
            out.append((start, "".join(buf), quote))
            state, quote = "code", None
            i += 1
            continue
        if c == "\n" and quote != "`":
            # unterminated on this line: not a real literal
            state, quote = "code", None
            i += 1
            continue
        buf.append(c)
        i += 1

    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    src = path.read_text(encoding="utf-8")

    lits = scan(src)
    line_starts = [0] + [i + 1 for i, ch in enumerate(src) if ch == "\n"]

    def line_of(off: int) -> int:
        return bisect.bisect_right(line_starts, off)

    hits = [
        (line_of(o), t, q)
        for o, t, q in lits
        if CJK.search(t) or FULLWIDTH.search(t)
    ]

    uniq: dict[str, int] = {}
    for ln, t, _q in hits:
        uniq.setdefault(t, ln)

    print(f"file: {path}")
    print(f"total string literals: {len(lits)}")
    print(f"literals containing CJK/full-width: {len(hits)}")
    print(f"unique: {len(uniq)}")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        data = [{"line": ln, "text": t} for t, ln in sorted(uniq.items(), key=lambda kv: kv[1])]
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"wrote {out}")
    else:
        for t, ln in sorted(uniq.items(), key=lambda kv: kv[1]):
            print(f"{ln:6d}  {t}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
