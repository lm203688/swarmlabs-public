"""Patch data/trust_surface.json with honest metadata + trust labels.

**注意：本脚本已退化为幂等守卫。** 诚实元数据的口径真源已上移到
``engine/trust_honesty.py``，由生成器 ``scripts/paper_validation_loop.py::
trust_rows`` 在写盘前直接调用，本脚本只对「已存在但缺字段的旧产物」做一次性
补齐，并保留幂等性检查。

**为什么还需要它：** DayVerify 定时任务（12:30）重建 ``trust_surface.json``
时，生成器现在会自带 ``honesty``/``evidence_type``；本脚本作为第二道防线，
覆盖「生成器跑在旧代码上」或「产物被其他流程覆写」的窗口期。

用法：
    python scripts/patch_trust_surface_honesty.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "trust_surface.json"

# 单一真源：schema_version / ref_quality / attach_honesty / needs_patch / summary_line
sys.path.insert(0, str(ROOT))
from engine.trust_honesty import (  # noqa: E402
    SCHEMA_VERSION,
    attach_honesty,
    needs_patch,
    summary_line,
)


def main() -> None:
    if not SRC.exists():
        print(f"[FATAL] {SRC} not found; run the generator first", file=sys.stderr)
        return 2
    d = json.loads(SRC.read_text(encoding="utf-8"))

    if not needs_patch(d):
        print(f"already patched ({len(d.get('rows') or [])} rows, schema v{SCHEMA_VERSION}); skipping")
        return 0

    attach_honesty(d)
    SRC.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"patched {len(d.get('rows') or [])} rows (schema v{SCHEMA_VERSION})")
    print(summary_line(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
