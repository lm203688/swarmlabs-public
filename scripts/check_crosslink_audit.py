"""Cross-link audit gate — prevents orphan pages across nav/sitemap/llms.

Purpose
=======
Any HTML page committed under ``frontend_fixed/`` (top-level) must be reachable
from all three canonical surfaces:

1. **navigation** — ``frontend_fixed/index.html`` has an ``href="/<slug>"`` (or
   ``href="/<slug>.html"``) link.
2. **sitemap**    — ``frontend_fixed/sitemap.xml`` lists the page URL.
3. **llms**       — ``frontend_fixed/llms.txt`` lists the page URL.

Any page missing from one or more surfaces is a hard fail. This is the CI
gate version of the manual "孤儿资产" audit done at PR-23; the goal is to
catch new pages before they go live as unreachable orphans.

Rules
-----
- Scan ``frontend_fixed/*.html`` for top-level pages (subdirectories like
  ``blog/`` are out of scope here — they have their own routing).
- Normalize slug: ``foo.html`` → ``foo``, ``index.html`` → ``/``.
- Index page itself is exempt from sitemap (already listed as ``/``).
- Small utility/redirect pages can be declared in ``EXEMPT`` with a comment.

Exit codes
----------
- 0: every page appears in nav + sitemap + llms.
- 1: at least one page missing from one or more surfaces.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend_fixed"

# Pages exempted from specific surfaces. Keys are filenames; values are lists of
# surfaces ("nav", "sitemap", "llms") that this page is intentionally absent from.
# Every exemption must be justified inline.
EXEMPT: dict[str, list[str]] = {
    # Auth entry — deliberately not in sitemap (avoid bot indexing login) and not
    # in llms.txt (auth UI is not AI-summarizable content).
    "login.html": ["sitemap", "llms"],
    # Interactive playground — internal tool, not a documented public page.
    # Not in nav either (not a primary entry point).
    "demo.html": ["nav", "sitemap", "llms"],
    # Swagger UI — auto-generated OpenAPI playground. In sitemap for API consumers,
    # linked from api-reference (not primary nav). Not in llms.txt (not prose content).
    "swagger-ui.html": ["nav", "llms"],
    # Legal boilerplate — indexed in sitemap for SEO/robots, but not in llms.txt
    # because terms/privacy are not AI-summarizable content.
    "terms.html": ["llms"],
    "privacy.html": ["llms"],
}


def slug_from_filename(filename: str) -> str:
    """`foo.html` -> `foo`, `index.html` -> `/`."""
    if filename == "index.html":
        return "/"
    if filename.endswith(".html"):
        return filename[: -len(".html")]
    return filename


def nav_links(html: str) -> set[str]:
    """Extract every ``href="/..."`` from index.html as a normalized slug set."""
    links: set[str] = set()
    for m in re.finditer(r'href="(/[^"\s>]+)"', html):
        raw = m.group(1).strip()
        # strip query / hash
        raw = raw.split("?", 1)[0].split("#", 1)[0]
        # normalize to slug (strip leading /, strip trailing .html)
        if raw in ("", "/"):
            continue
        s = raw.lstrip("/")
        if s.endswith(".html"):
            s = s[: -len(".html")]
        links.add(s)
    return links


def sitemap_slugs(xml: str) -> set[str]:
    """Extract every <loc> URL as a normalized slug set.

    Sitemap URLs look like ``https://swarmlabs.tools/kpi-dashboard``.
    Splitting on ``/`` with maxsplit=3 gives ``['https:', '', 'swarmlabs.tools', 'kpi-dashboard']``;
    the domain is at index 2, the path is at index 3.
    """
    slugs: set[str] = set()
    for m in re.finditer(r"<loc>([^<]+)</loc>", xml):
        url = m.group(1).strip()
        if "://" in url:
            parts = url.split("/", 3)
            # e.g. ['https:', '', 'swarmlabs.tools', 'kpi-dashboard']
            if len(parts) < 3 or parts[2] != "swarmlabs.tools":
                continue  # external / wrong host
            path = parts[3] if len(parts) > 3 else ""
        else:
            path = url
        path = path.strip("/")
        if not path or path.endswith(".md") or path.endswith(".txt"):
            continue
        slugs.add(path)
    return slugs


def llms_slugs(md: str) -> set[str]:
    """Extract every https://swarmlabs.tools/<slug> mention as a slug set."""
    slugs: set[str] = set()
    for m in re.finditer(r"https?://swarmlabs\.tools/([A-Za-z0-9_.\-/]+)", md):
        path = m.group(1).strip("/")
        if not path or path.endswith(".md") or path.endswith(".txt"):
            continue
        slugs.add(path)
    return slugs


def main() -> int:
    if not FRONTEND.exists():
        print("[FAIL] frontend_fixed/ directory not found", file=sys.stderr)
        return 1

    pages = sorted(FRONTEND.glob("*.html"))
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    sitemap_xml = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")
    llms_txt = (FRONTEND / "llms.txt").read_text(encoding="utf-8")

    nav = nav_links(index_html)
    sm = sitemap_slugs(sitemap_xml)
    ll = llms_slugs(llms_txt)

    failures: list[str] = []
    passes = 0
    exempted = 0

    print(f"[crosslink] scanned {len(pages)} top-level HTML pages in frontend_fixed/")
    print(f"[crosslink] nav={len(nav)} sitemap={len(sm)} llms={len(ll)}")
    print()

    for page in pages:
        slug = slug_from_filename(page.name)
        exempt_surfaces = EXEMPT.get(page.name, [])
        if exempt_surfaces:
            exempted += 1
        if slug == "/":
            # index.html itself is the root; it's the sitemap root, no per-surface check
            print(f"[PASS] index.html (root)")
            passes += 1
            continue

        missing: list[str] = []
        if "nav" not in exempt_surfaces and slug not in nav:
            missing.append("nav")
        if "sitemap" not in exempt_surfaces and slug not in sm:
            missing.append("sitemap")
        if "llms" not in exempt_surfaces and slug not in ll:
            missing.append("llms")

        if missing:
            failures.append(f"{page.name}: missing from {', '.join(missing)}")
            exempt_note = f"  (exempt from: {', '.join(exempt_surfaces)})" if exempt_surfaces else ""
            print(f"[FAIL] {page.name}{exempt_note}  — missing from: {', '.join(missing)}")
        else:
            passes += 1
            exempt_note = f"  (exempt from: {', '.join(exempt_surfaces)})" if exempt_surfaces else ""
            print(f"[PASS] {page.name}{exempt_note}")

    print()
    print(f"[crosslink] {passes} passed, {exempted} exempted, {len(failures)} failed")

    if failures:
        print()
        print("[crosslink] Fix by adding each missing page to:")
        print("  - frontend_fixed/index.html nav (or footer)")
        print("  - frontend_fixed/sitemap.xml")
        print("  - frontend_fixed/llms.txt")
        print()
        print("[crosslink] OR add an entry to EXEMPT in this script with justification.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
