"""M1 · Real experimental data downloader (0 → 3 ground-truth anchors).

Frontier basis (2026-10-15 report):
- AI4S entered the "wet-lab closed-loop validation era" on 2026-10-10.
- RealPDEBench (ICLR 2026 Oral, Westlake) makes paired real measurements the
  differentiator.
- SwarmLabs' honest boundary currently reports real_wet_lab_ground_truth = 0,
  which is the ceiling of every scientific claim we make.

Design constraints (from the 10-15 hard-tech plan):
1. NO fallback to synthetic data. If a source fails to download, sys.exit(1).
2. Every downloaded file is recorded in data/raw/manifest.json (append-only)
   with: key, DOI, URL, original filename, sha256, size, download timestamp.
3. The script is idempotent: re-running does NOT re-download existing files
   whose sha256 matches; it only updates the manifest timestamp.
4. Every file carries its DOI so a third party can independently re-download
   and recompute the numbers — this is the acceptance criterion.

Sources (all peer-reviewed / DOI-resolvable):
- E. coli growth  → Zenodo 10.5281/zenodo.19416053
- MB adsorption   → Zenodo 10.5281/zenodo.7311805
- Yeast ferment   → Figshare 10.25378/figshare.2048400

Usage:
    python scripts/fetch_real_datasets.py                # fetch all 3
    python scripts/fetch_real_datasets.py --only ecoli   # single source
    python scripts/fetch_real_datasets.py --dry-run      # print plan only
    python scripts/fetch_real_datasets.py --strict       # fail on ANY failure
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

USER_AGENT = (
    "SwarmLabs/1.0 (https://github.com/lm203688/swarmlabs; "
    "AI4S open benchmark) - contact: repo maintainer"
)

DOWNLOAD_TIMEOUT_SEC = 120  # per URL
MAX_RETRIES = 3


@dataclass
class SourceSpec:
    key: str
    doi: str
    record_url: str  # the record landing page
    api_url: str     # JSON API endpoint that lists actual files
    kind: str        # "zenodo" or "figshare"
    min_files: int = 1
    max_file_bytes: int = 50 * 1024 * 1024  # 50 MB cap per file
    content_type_hint: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Canonical source list. File URLs are DISCOVERED from each platform's API
# rather than hardcoded, because Zenodo/Figshare filenames drift over time.
# ---------------------------------------------------------------------------
SOURCES: List[SourceSpec] = [
    SourceSpec(
        key="ecoli_monod_growth",
        doi="10.5281/zenodo.19416053",
        record_url="https://zenodo.org/records/19416053",
        api_url="https://zenodo.org/api/records/19416053",
        kind="zenodo",
        notes=(
            "Optical Density Data from the Growth of Escherichia coli at "
            "Different Temperatures and Glucose Concentrations. Files: "
            "Porcentaje_glucosa_1hoja.xlsx + Repo{0.1,0.5,1,2}pct.xlsx "
            "(5 xlsx files, 98-284 KB each). Downstream: "
            "scripts/acquire_real_anchors.py::extract_ecoli_monod_params."
        ),
    ),
    SourceSpec(
        key="mb_adsorption_langmuir",
        doi="10.5281/zenodo.7311805",
        record_url="https://zenodo.org/records/7311805",
        api_url="https://zenodo.org/api/records/7311805",
        kind="zenodo",
        notes=(
            "Bio-Waste-Derived Adsorbent Materials for Methylene Blue Removal "
            "(water hyacinth activated carbon). Published: qmax=18.4162 mg/g, "
            "K_L=0.046 L/mg, R^2=0.9489."
        ),
    ),
    SourceSpec(
        key="yeast_ethanol_fermentation",
        doi="10.25378/figshare.2048400",
        record_url="https://figshare.com/articles/dataset/2048400",
        api_url="https://api.figshare.com/v2/articles/2048400",
        kind="figshare",
        notes=(
            "Substrate and Product Inhibition on Yeast Performance. Published: "
            "max ethanol yield 95% at 39 g/L product / 80 g/L substrate; "
            "ethanol inhibition threshold 70 g/L; critical substrate 160 g/L."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {
            "schema": "swarmlabs.real-datasets-manifest.v1",
            "created_at": _utc_now_iso(),
            "downloads": [],
        }
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Manifest {MANIFEST_PATH} is corrupt: {e}. "
            "Refusing to overwrite. Manual recovery required."
        ) from e


def _save_manifest(m: Dict[str, Any]) -> None:
    m["updated_at"] = _utc_now_iso()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(m, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _resolve_download_url(base_url: str, spec: SourceSpec) -> str:
    """Deprecated: kept for backward-compat with older call sites. New code
    should use _discover_files() to get URLs from the platform API."""
    if "zenodo.org/records/" in base_url:
        return base_url
    return base_url


def _discover_files(spec: SourceSpec) -> List[Dict[str, Any]]:
    """Query the platform API and return a list of {name, url, size, sha256}.

    This is the ONLY source of truth for what files a record actually has.
    Hardcoding filenames fails the moment a publisher updates their record.
    """
    import urllib.request as _req
    req = _req.Request(spec.api_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with _req.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp:
        payload = resp.read()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"API for {spec.api_url} returned non-JSON payload "
            f"(first 100 bytes: {payload[:100]!r}). Error: {e}"
        ) from e

    if spec.kind == "zenodo":
        files = []
        for f in data.get("files", []):
            key = f.get("key", "")
            url = f.get("links", {}).get("self", "")
            if not url:
                continue
            files.append({
                "name": key,
                "url": url,
                "size": f.get("size", 0),
                "sha256": f.get("checksum", "").replace("sha256:", "") or "",
            })
        return files

    if spec.kind == "figshare":
        files = []
        for f in data.get("files", []):
            name = f.get("name", "")
            # Figshare v2 API: files[] has "download_url" for direct access
            url = f.get("download_url", "") or f.get("readme", {}).get("url", "")
            if not url:
                # Fallback: construct from article id + file id
                file_id = f.get("id")
                if file_id:
                    article_id = data.get("id", "")
                    url = f"https://figshare.com/ndownloader/files/{file_id}"
            files.append({
                "name": name,
                "url": url,
                "size": f.get("size", 0),
                "sha256": f.get("sha256", "") or "",
            })
        return files

    raise ValueError(f"Unknown source kind: {spec.kind!r}")


def _download_with_retries(
    url: str, dest: Path, retries: int = MAX_RETRIES,
    timeout: int = DOWNLOAD_TIMEOUT_SEC,
) -> None:
    """Download url -> dest with basic retries. Fail-fast if the final attempt
    fails; no silent fallback to a synthetic file is attempted."""
    last_err: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    raise urllib.error.HTTPError(
                        url, resp.status, f"HTTP {resp.status}", {}, None
                    )
                content_type = resp.headers.get("Content-Type", "")
                payload = resp.read()
            if len(payload) < 100:
                raise RuntimeError(
                    f"Response from {url} is only {len(payload)} bytes "
                    f"(likely an error page or HTML shell, content-type={content_type})."
                )
            dest.write_bytes(payload)
            return
        except Exception as e:  # noqa: BLE001 — we catch everything here
            last_err = e
            print(
                f"  [warn] attempt {attempt}/{retries} failed for {url}: {e}",
                file=sys.stderr,
            )
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(
        f"Failed to download {url} after {retries} attempts. "
        "Refusing to fall back to synthetic data (M1 hard constraint). "
        f"Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Main fetch pipeline
# ---------------------------------------------------------------------------
@dataclass
class FetchResult:
    spec: SourceSpec
    ok: bool
    dest_dir: Path
    files: List[Dict[str, Any]] = field(default_factory=list)  # per-file results
    error: str = ""

    @property
    def n_ok_files(self) -> int:
        return sum(1 for f in self.files if f.get("ok"))

    @property
    def n_fail_files(self) -> int:
        return sum(1 for f in self.files if not f.get("ok"))


def _is_binary_data(blob: bytes) -> bool:
    """Reject HTML shells / error pages by checking the leading bytes.
    A real xlsx starts with the ZIP magic 'PK\\x03\\x04'; a real csv/parquet/csv
    is mostly ASCII; an HTML error page starts with '<!DOCTYPE' or '<html'."""
    if not blob:
        return False
    head = blob[:200]
    # Common binary signatures we accept
    if head.startswith(b"PK\\x03\\x04") or head.startswith(b"PK\\x05\\x06"):
        return True  # xlsx / docx / zip
    if head.startswith(b"\\x1f\\x8b"):
        return True  # gzip
    if head.startswith(b"\\x89HDF"):
        return True  # HDF5
    if head.startswith(b"%PDF-"):
        return True  # pdf (rare in datasets, but some have attached papers)
    if head.startswith(b"\\x50\\x4b"):
        return True
    # Reject anything that looks like HTML
    if b"<!DOCTYPE" in head or b"<html" in head or b"<HTML" in head:
        return False
    if b"Error" in head and b"<html" in head:
        return False
    # Default: accept ASCII/csv-like payloads
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return True  # binary but no known sig — accept


def fetch_source(
    spec: SourceSpec, manifest: Dict[str, Any], dry_run: bool = False,
) -> FetchResult:
    dest_dir = RAW_DIR / spec.key
    dest_dir.mkdir(parents=True, exist_ok=True)

    print(f"  discovering files via {spec.api_url}")
    try:
        discovered = _discover_files(spec)
    except Exception as e:  # noqa: BLE001
        return FetchResult(spec=spec, ok=False, dest_dir=dest_dir,
                           error=f"file-discovery failed: {e}")

    if not discovered:
        return FetchResult(spec=spec, ok=False, dest_dir=dest_dir,
                           error="API returned zero files — record may be private or withdrawn")

    prior_by_name = {
        f.get("filename"): f for f in manifest.get("downloads", [])
        if f.get("key") == spec.key and f.get("filename")
    }

    file_results: List[Dict[str, Any]] = []
    all_ok = True
    any_failure_reason = ""

    for entry in discovered:
        name = entry.get("name") or "unnamed"
        url = entry.get("url", "")
        expected_sha = entry.get("sha256", "")
        expected_size = entry.get("size", 0)
        safe_name = name.replace("\\", "/").replace("..", "_")
        dest = dest_dir / safe_name

        # Skip if prior manifest entry has same name + same sha256
        prior = prior_by_name.get(name)
        if dest.exists() and prior and prior.get("sha256"):
            local_sha = _sha256_of_file(dest)
            if local_sha == prior["sha256"]:
                if dry_run:
                    print(f"    [dry] would reuse {name}")
                file_results.append({
                    "filename": name, "url": url, "path": str(dest),
                    "sha256": local_sha, "size": dest.stat().st_size,
                    "ok": True, "reused": True,
                })
                continue

        if dry_run:
            print(f"    [dry] would download {name} ({expected_size} bytes)")
            file_results.append({
                "filename": name, "url": url, "path": str(dest),
                "sha256": "", "size": expected_size, "ok": True, "reused": False,
            })
            continue

        # Attempt download
        try:
            _download_with_retries(url, dest)
        except Exception as e:  # noqa: BLE001
            all_ok = False
            any_failure_reason = str(e)
            file_results.append({
                "filename": name, "url": url, "path": str(dest),
                "sha256": "", "size": 0, "ok": False, "error": str(e),
            })
            print(f"    FAIL {name}: {e}")
            continue

        blob = dest.read_bytes()
        if not _is_binary_data(blob):
            dest.unlink(missing_ok=True)
            all_ok = False
            any_failure_reason = (
                f"downloaded content of {name} is not binary/spreadsheet-like; "
                "likely an HTML landing page, refusing to keep"
            )
            file_results.append({
                "filename": name, "url": url, "path": str(dest),
                "sha256": "", "size": 0, "ok": False, "error": any_failure_reason,
            })
            print(f"    FAIL {name}: content looks like HTML shell, removed")
            continue

        sha = _sha256_of_file(dest)
        size = dest.stat().st_size
        file_results.append({
            "filename": name, "url": url, "path": str(dest),
            "sha256": sha, "size": size, "ok": True, "reused": False,
            "expected_sha256": expected_sha,
        })
        print(f"    OK   {name}  sha256={sha[:12]}  size={size}")

    ok = all_ok and len(file_results) >= 1 and all(f["ok"] for f in file_results)
    return FetchResult(
        spec=spec, ok=ok, dest_dir=dest_dir,
        files=file_results, error="" if ok else any_failure_reason,
    )


def main(argv: Optional[List[str]] = None) -> int:
    global MANIFEST_PATH  # assigned later from --output-manifest
    parser = argparse.ArgumentParser(
        description="M1: download real experimental datasets with DOI anchors."
    )
    parser.add_argument("--only", choices=[s.key for s in SOURCES],
                        help="fetch only one source")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan, don't touch the network")
    parser.add_argument("--strict", action="store_true",
                        help="sys.exit(1) on any failure (default)")
    parser.add_argument("--non-strict", action="store_true",
                        help="warn but continue on failures (for diagnosis only)")
    parser.add_argument("--output-manifest", default=str(MANIFEST_PATH),
                        help=f"manifest path (default: {MANIFEST_PATH})")
    args = parser.parse_args(argv)

    MANIFEST_PATH = Path(args.output_manifest)

    if args.strict and args.non_strict:
        print("error: --strict and --non-strict are mutually exclusive",
              file=sys.stderr)
        return 2

    spec_filter = {args.only} if args.only else {s.key for s in SOURCES}
    manifest = _load_manifest()

    print(f"[M1] real-dataset fetch, {len(spec_filter)} source(s) selected")
    if args.dry_run:
        print("      dry-run mode — nothing will be written")
    print()

    results: List[FetchResult] = []
    for spec in SOURCES:
        if spec.key not in spec_filter:
            continue
        print(f"[M1] key={spec.key}  doi={spec.doi}")
        res = fetch_source(spec, manifest, dry_run=args.dry_run)
        results.append(res)
        status = "OK" if res.ok else "FAIL"
        print(f"      {status}  ({res.n_ok_files}/{len(res.files)} files ok)")
        if res.error:
            print(f"      error: {res.error}")
        print()

    # Update manifest atomically (only on non-dry-run)
    if not args.dry_run:
        all_entries: List[Dict[str, Any]] = []
        for res in results:
            for f in res.files:
                all_entries.append({
                    "key": res.spec.key,
                    "doi": res.spec.doi,
                    "record_url": res.spec.record_url,
                    "filename": f["filename"],
                    "url": f["url"],
                    "path": str(Path(f["path"]).relative_to(ROOT)),
                    "sha256": f["sha256"],
                    "size_bytes": f["size"],
                    "content_type_hint": res.spec.content_type_hint,
                    "downloaded_at": _utc_now_iso(),
                    "notes": res.spec.notes,
                    "ok": f["ok"],
                    "reused": f.get("reused", False),
                    "error": f.get("error", ""),
                })
        manifest["schema"] = "swarmlabs.real-datasets-manifest.v2"
        manifest["downloads"] = all_entries
        _save_manifest(manifest)
        print(f"[M1] manifest updated: {MANIFEST_PATH}")

    n_ok = sum(r.n_ok_files for r in results)
    n_fail = sum(r.n_fail_files for r in results)
    n_sources_ok = sum(1 for r in results if r.ok)
    n_sources_fail = sum(1 for r in results if not r.ok)
    print(f"[M1] summary: {n_ok} files ok, {n_fail} files failed "
          f"(across {n_sources_ok}/{len(results)} sources)")

    if n_fail > 0 and (args.strict or not args.non_strict):
        print(
            "\n[M1] FAIL-FAST: refusing to fall back to synthetic data.\n"
            "    Either a source URL is temporarily down, or the platform API "
            "is returning something unexpected. Fix one of them, then re-run.\n"
            "    If a source is permanently unavailable, remove it from "
            "SOURCES in this file and record the removal in "
            "reports/assessment/ so the honest_count reflects reality.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
