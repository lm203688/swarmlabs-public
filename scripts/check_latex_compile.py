#!/usr/bin/env python3
"""L-08: LaTeX compile CI check — structural validation of paper.tex.

Checks (no external TeX install needed):
  1. Environment matching (\\begin / \\end)
  2. Label / \\ref integrity
  3. Bibliography key usage
  4. No undefined / missing keys

Exit code: 0 = all pass, 1 = failure.
"""
from __future__ import annotations
import re, sys, pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAPER_TEX = ROOT / "outputs" / "paper-1-applied-intelligence" / "paper.tex"
PAPER_BIB = ROOT / "outputs" / "paper-1-applied-intelligence" / "references.bib"

failures: list[str] = []


def check_envs(tex: str) -> None:
    """Check \\begin / \\end environment matching."""
    begins = Counter(re.findall(r"\\begin\{(\w+)\}", tex))
    ends = Counter(re.findall(r"\\end\{(\w+)\}", tex))
    all_envs = sorted(set(list(begins.keys()) + list(ends.keys())))
    for env in all_envs:
        if begins[env] != ends[env]:
            failures.append(
                f"ENV MISMATCH: {env} begin={begins[env]} end={ends[env]}"
            )
    print(f"  environments: {len(all_envs)} types, {sum(begins.values())} begin, {sum(ends.values())} end")


def check_labels(tex: str) -> None:
    """Check \\label / \\ref / \\eqref integrity."""
    labels = set(re.findall(r"\\label\{([^}]+)\}", tex))
    # Collect both \ref and \eqref references
    refs = re.findall(r"\\(?:eq)?ref\{([^}]+)\}", tex)
    ref_set = set(refs)
    missing = ref_set - labels
    if missing:
        for m in sorted(missing):
            failures.append(f"MISSING LABEL: ref{{{m}}} has no \\label")
    unused = labels - ref_set
    if unused:
        for u in sorted(unused):
            failures.append(f"UNUSED LABEL: \\label{{{u}}} never referenced")
    print(f"  labels: {len(labels)}, refs: {len(refs)}, missing: {len(missing)}, unused: {len(unused)}")


def check_bib(tex: str, bib_path: pathlib.Path) -> None:
    """Check bibliography key usage."""
    if not bib_path.exists():
        failures.append(f"BIB NOT FOUND: {bib_path}")
        print(f"  bib: not found")
        return

    bib = bib_path.read_text(encoding="utf-8")
    bib_keys = set(re.findall(r"@[a-zA-Z]+\{([^,]+),", bib))

    # Find all \cite keys
    all_cited: set[str] = set()
    for m in re.finditer(r"\\cite[pt]?\{([^}]+)\}", tex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                all_cited.add(k)

    missing_bib = all_cited - bib_keys
    if missing_bib:
        for m in sorted(missing_bib):
            failures.append(f"MISSING BIB: \\cite{{{m}}} not in references.bib")

    unused_bib = bib_keys - all_cited
    print(
        f"  bib: {len(bib_keys)} entries, {len(all_cited)} cited, "
        f"missing: {len(missing_bib)}, unused: {len(unused_bib)}"
    )


def check_basic(tex: str) -> None:
    """Basic structural checks."""
    if r"\documentclass" not in tex:
        failures.append("MISSING \\documentclass")
    if r"\begin{document}" not in tex:
        failures.append("MISSING \\begin{document}")
    if r"\end{document}" not in tex:
        failures.append("MISSING \\end{document}")
    if r"\usepackage" not in tex:
        failures.append("MISSING \\usepackage (no packages loaded)")

    # Check for common typos
    if re.search(r"\\begin{[a-z]+\s*$", tex, re.MULTILINE):
        # Check for spaces in env names
        for m in re.finditer(r"\\begin\{([^}]+)\}", tex):
            env = m.group(1)
            if " " in env:
                failures.append(f"ENV NAME HAS SPACE: \\begin{{{env}}}")

    print(f"  basic: documentclass={'yes' if r'\\documentclass' in tex else 'NO'}, "
          f"begin/end document={'yes' if r'\\begin{document}' in tex and r'\\end{document}' in tex else 'NO'}")


def main() -> int:
    print("=" * 60)
    print("LaTeX compile check (L-08)")
    print("=" * 60)

    if not PAPER_TEX.exists():
        print(f"FAIL: paper.tex not found at {PAPER_TEX}")
        return 1

    tex = PAPER_TEX.read_text(encoding="utf-8")

    print("\n[1/4] Environment matching")
    check_envs(tex)

    print("\n[2/4] Label / ref integrity")
    check_labels(tex)

    print("\n[3/4] Bibliography")
    check_bib(tex, PAPER_BIB)

    print("\n[4/4] Basic structure")
    check_basic(tex)

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} issue(s) found")
        for f in failures:
            print(f"  - {f}")
        return 1
    else:
        print("PASS: all checks passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
