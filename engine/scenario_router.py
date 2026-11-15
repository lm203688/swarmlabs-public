#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线场景模糊路由 —— 让不知道 scenario_key 的调用方能进 82 个场景。

它解决什么问题
--------------
`vlab_contract` 的 scope 阶段要求调用方给出确切 `scenario_key`。账本里有 82 个
场景，但外部提交方几乎不可能知道 `micro_ecoli_glucose` 这个字符串——于是他们
落到 `discipline_onboarding_required`，76 个场景事实上只对知道 key 的人可达。

这是纯可用性问题，但代价是：产品宣称的覆盖范围里，有一大部分机器路径进不去。

设计纪律
--------
1. **离线、纯标准库**。不联网、不调任何模型——与内核「虚拟实验独立成立」
   是同一条纪律。路由器本身也必须是可离线复验的。
2. **不做量纲校验**。量纲由 `vlab_contract.goal_quantity_binding` 独立把关
   （那是拒收级红线）。路由器只做文本与专业域匹配，绝不越权给量纲结论——
   两条链路各管一段，才不会有一条悄悄失效而另一条也没发现。
3. **fail-closed**。低于 `MATCH_FLOOR` 一律返回空候选 + 可读诊断，
   绝不为了「看起来有结果」而硬凑一个低分场景。猜错的场景比明确的
   "找不到"更糟——前者会让一个 PASS 报告答非所问。
4. **确定性**。同一查询永远返回同一结果（分数降序 + key 升序打破平局）。
   路由器不可复算就等于不可审计。
5. **命中必须可解释**。每条候选都带 `matched_terms`，说明是靠哪几个词命中的。

用法
----
    import scenario_router as R
    hits = R.route({"discipline": "Microbiology",
                     "quantity": "biomass concentration",
                     "unit": "g/L",
                     "variables": [{"name": "temperature"}]}, top_k=3)
    if not hits["matches"]:
        print(hits["diagnostic"])          # 可读的未命中原因
    else:
        print(hits["matches"][0]["scenario_key"])

只读 `gate_ledger.json`，不写任何文件。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
LEDGER_CANDIDATES = (
    os.path.join(ROOT, "frontend_fixed", "reports", "gate_ledger.json"),
    os.path.join(ROOT, "data", "gate_ledger.json"),
)

#: 低于此分数的候选一律不返回。取 0.20 是有意偏严的：宁可空手而归，
#: 也不把调用方带到一个只是「沾边」的场景上。
MATCH_FLOOR = 0.20

#: 各信号权重（合计 1.0）。domain 与 quantity 是两个最强信号。
W_DOMAIN = 0.30
W_QUANTITY = 0.35
W_UNIT = 0.20
W_VARIABLE = 0.15
W_PUBLISHED = 0.15

#: discipline → key 前缀（前缀即账本 key 的下划线分段首词）。
#: 故意只覆盖能辩护的映射；覆盖不全时路由器会降低置信度而不是猜。
DISCIPLINE_PREFIXES: Dict[str, Sequence[str]] = {
    "microbiology": ("micro", "microbio"),
    "microbe": ("micro", "microbio"),
    "fermentation": ("micro", "microbio"),
    "biology": ("bio", "micro", "microbio", "eco", "neuro"),
    "biotech": ("bio", "micro", "microbio"),
    "ecology": ("eco", "bio"),
    "neuroscience": ("neuro", "bio"),
    "chemistry": ("chem", "cheminfo", "mol", "ode", "pde"),
    "chem": ("chem", "cheminfo", "mol"),
    "chemical engineering": ("chem", "cheminfo", "mol"),
    "cheminformatics": ("cheminfo", "mol"),
    "molecular": ("mol", "cheminfo"),
    "materials": ("chem", "cheminfo", "mol"),
    "physics": ("pde", "ode", "energy"),
    "energy": ("energy", "pde"),
    "optimization": ("opt", "surrogate", "quant", "resp"),
    "ml": ("surrogate", "llm", "quant", "opt", "resp"),
    "ai": ("llm", "surrogate", "quant", "opt", "resp"),
}

#: 英文停用词。中文靠下面的 CJK 处理，不进这个表。
STOPWORDS = frozenset("""
a an the of for in on at to and or with from by is are was were be been
the as into per vs versus about using use used model models prediction
predictive simulate simulation scenario scenario- based data set
""".split())

#: 中文停用词。domain 字段里有中文（如「微生物 / E. coli 批次培养」），
#: 不处理会让「微生物」这类词污染所有候选的得分。
ZH_STOP = ("微生物", "培养", "场景", "仿真", "模型", "模拟", "动力学", "效应")

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


# ------------------------------------------------------------------ 文本切分


def tokenize(text: Any) -> List[str]:
    """把一段自由文本切成可比较的 token。

    返回两类 token：拉丁词（转小写、去停用词）与 CJK 片段（去停用词）。
    希腊字母与单位符号（μmax、h⁻¹、g/L）按拉丁规则处理——量名里的
    `μmax` 是路由要匹配的关键信号，不能丢。
    """
    if not text:
        return []
    s = str(text).lower()
    out: List[str] = []
    # 拉丁词：保留 μ 这类希腊字母（它们在 \u0370-\u03ff，需显式纳入）
    for m in re.finditer(
        r"[A-Za-z\u0370-\u03ff][A-Za-z0-9\u0370-\u03ff+≤≥]{2,}", s
    ):
        w = m.group(0).strip("+≤≥ ")
        if len(w) >= 3 and w not in STOPWORDS:
            out.append(w)
    for m in _CJK_RE.finditer(s):
        w = m.group(0)
        if w not in ZH_STOP:
            out.append(w)
    return out


def key_tokens(key: str) -> List[str]:
    """场景 key 的 token 集合。`micro_ecoli_glucose` -> [micro, ecoli, glucose]。

    下划线分段本身就是有意义的信号（micro = 微生物域），所以先按 `_` 拆，
    再对每段做普通分词。

    额外去掉维度后缀（`ackley2d` -> `ackley`）：账本 key 里 `1d`/`2d`/`6d`
    是维度标注，不是名称的一部分。不去掉的话，查询「ackley function」
    永远匹配不上 `opt_ackley2d`——token 不相等，路由器就会漏掉一个
    明明存在的场景。注意 `logD` 不受影响（没有数字前缀），它的 D/S
    是有意义的区分符。
    """
    out: List[str] = []
    for part in str(key).split("_"):
        if part:
            out.append(re.sub(r"[0-9]+[a-z]?$", "", part.lower()))
    return [t for t in out if len(t) >= 2]


_UNIT_RE = re.compile(r"[A-Za-zµ\u03bc]{1,4}[\u207B\u00B9-\u00BC⁻¹²³]?"
                      r"[/][A-Za-zµ\u03bc]{1,4}[\u207B\u00B9-\u00BC⁻¹²³]?")
_UNIT_BARE_RE = re.compile(
    r"(?<![A-Za-z])(?:degC|degF|°C|°F|g/L|mg/L|kg/m³|m²/s|h⁻¹|s⁻¹|"
    r"mol/L|mol/g|nm|µm|mm|cm|km|Pa|bar|atm|J/g|kJ/kg|eV|pH)\b")


def normalize_unit(u: Any) -> str:
    """单位规范化：小写、去空格、统一希腊字母与上标。

    刻意保守——只做大写归一和空格去除。mg/L 与 g/L **不**归并为同量纲，
    那是 `goal_quantity_binding` 的职责，路由器不归并。
    """
    if not u:
        return ""
    s = str(u).strip().lower().replace(" ", "")
    return s.replace("μ", "µ").replace("°", "deg")


def extract_units(text: Any) -> set:
    """从一段文本里抽出所有出现过的单位写法。

    覆盖 `g/L` 这类比值写法与 `h⁻¹` 这类上标写法。返回规范化后的集合。
    """
    if not text:
        return set()
    s = str(text)
    out = set()
    for m in _UNIT_RE.finditer(s):
        n = normalize_unit(m.group(0))
        if len(n) >= 3:
            out.add(n)
    for m in _UNIT_BARE_RE.finditer(s):
        n = normalize_unit(m.group(0))
        if n:
            out.add(n)
    return out


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """交并比。两集合都为空时返回 0.0——空集合不该产生「完全匹配」。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _overlap_ratio(query: Sequence[str], hay: Sequence[str]) -> float:
    """查询词中有多少比例出现在候选里。

    用这个而不是 Jaccard：查询通常只有 1–3 个词，而候选的 token 集合可能
    有十几个。Jaccard 会把「命中了全部 2 个查询词」压成 2/17 ≈ 0.12，
    而那是该给高分的情形。
    """
    q = [t for t in query if t]
    if not q:
        return 0.0
    hs = set(hay)
    return sum(1 for t in q if t in hs) / len(q)


# ------------------------------------------------------------------ 索引


def load_ledger(path: Optional[str] = None) -> Optional[dict]:
    for p in ([path] if path else LEDGER_CANDIDATES):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def build_scenario_index(ledger: Optional[dict] = None) -> Dict[str, dict]:
    """为每个场景预计算路由特征。

    索引条目键集固定，调用方不必写 `if in`：
      scenario_key / prefix / domain / domain_tokens / key_tokens /
      published_tokens / unit_corpus / verdict / gate / anchor_grade /
      replication_passed
    """
    led = ledger if ledger is not None else load_ledger()
    out: Dict[str, dict] = {}
    if not led:
        return out
    grades = _anchor_grades()
    for key, entry in (led.get("scenarios") or {}).items():
        entry = entry or {}
        domain = entry.get("domain") or ""
        published = entry.get("published") or ""
        out[key] = {
            "scenario_key": key,
            "prefix": key.split("_")[0],
            "domain": domain,
            "domain_tokens": key_tokens(key) + tokenize(domain),
            "key_tokens": key_tokens(key),
            "published_tokens": tokenize(published),
            # 单位语料：key + domain + published 全文里的单位写法。
            # rationale 不含（它只讲 R²/coverage，没有量纲信息）。
            "unit_corpus": extract_units(key + " " + domain + " " + published),
            "verdict": entry.get("verdict"),
            "gate": entry.get("gate"),
            # tie-break 用：分数并列时优先证据强的场景，而不是字母序
            "anchor_grade": grades.get(key),
            "replication_passed": bool(entry.get("replication_passed", False)),
        }
    return out


_WET_LAB_ANCHOR_JSON = os.path.join(
    ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")
_ANCHOR_GRADE_CACHE: Optional[Dict[str, str]] = None


def _anchor_grades() -> Dict[str, str]:
    """按 scenario_key 读湿实验锚定分级（AGREE/NEAR/CONTRADICTED/UNANCHORED）。

    只读、失败即空字典。用途仅是分数并列时的 tie-break——
    一个有 AGREE 锚点的场景比字母序更靠前是值得辩护的。
    """
    global _ANCHOR_GRADE_CACHE
    if _ANCHOR_GRADE_CACHE is not None:
        return _ANCHOR_GRADE_CACHE
    out: Dict[str, str] = {}
    try:
        with open(_WET_LAB_ANCHOR_JSON, encoding="utf-8") as f:
            d = json.load(f)
        for item in (d.get("scenarios") or []):
            k = item.get("scenario_key") if isinstance(item, dict) else None
            if isinstance(k, str) and k:
                out[k] = item.get("grade") or ""
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    _ANCHOR_GRADE_CACHE = out
    return out


_INDEX_CACHE: Optional[Dict[str, dict]] = None


def scenario_index(force: bool = False) -> Dict[str, dict]:
    """进程内缓存的索引。82 个场景逐次重建索引没有必要。"""
    global _INDEX_CACHE
    if force or _INDEX_CACHE is None:
        _INDEX_CACHE = build_scenario_index()
    return _INDEX_CACHE


# ------------------------------------------------------------------ 打分


def _domain_score(discipline: str, prefix: str, domain_tokens: Sequence[str]) -> float:
    """专业域匹配：显式映射 > 前缀互含 > domain 文本词重叠。"""
    if not discipline:
        return 0.0
    d = str(discipline).strip().lower()
    mapped = DISCIPLINE_PREFIXES.get(d)
    if mapped and prefix in mapped:
        return 1.0
    # 反向：discipline 文本里含前缀（"cheminformatics" -> "chem"）
    if prefix in d and len(prefix) >= 3:
        return 0.6
    if d in prefix and len(d) >= 4:
        return 0.5
    return _overlap_ratio(tokenize(d), list(domain_tokens)) * 0.7


def route(query: Dict[str, Any], top_k: int = 3,
          index: Optional[Dict[str, dict]] = None) -> dict:
    """按查询找候选场景。

    query 字段（全部可选，缺字段时对应信号得 0 分而不是报错）：
      discipline      专业域（如 "Microbiology"）
      quantity        要验证的量（如 "biomass concentration"）
      unit            量的单位——仅用于诊断输出，**不参与打分**
      variables       [{"name": ...}, ...] 设计空间变量名

    返回键集固定：
      matches       [{scenario_key, score, matched_terms, verdict, gate}]
      match_floor   阈值
      best_score    最高分（无候选时为 0.0）
      hit           是否至少有一个候选
      diagnostic    人读的诊断说明（命中与未命中都写）
    """
    idx = index if index is not None else scenario_index()
    discipline = str(query.get("discipline") or "").strip()
    quantity = str(query.get("quantity") or "").strip()
    unit = str(query.get("unit") or "").strip()
    q_tokens = tokenize(quantity)
    q_unit = normalize_unit(unit)
    var_names = [str(v.get("name") or "").strip()
                 for v in (query.get("variables") or [])
                 if isinstance(v, dict)]
    var_tokens = tokenize(" ".join(var_names))

    scored: List[dict] = []
    for key, feat in idx.items():
        s_dom = _domain_score(discipline, feat["prefix"], feat["domain_tokens"])
        s_qty = _overlap_ratio(q_tokens, feat["key_tokens"])
        s_pub = _overlap_ratio(q_tokens, feat["published_tokens"])
        s_var = _overlap_ratio(var_tokens, feat["key_tokens"])

        # 单位：查询单位是否出现在该场景的单位语料里。精确子串匹配，
        # 不做量纲换算（mg/L ≠ g/L 是 goal_quantity_binding 的事）。
        s_unit = 1.0 if (q_unit and q_unit in feat["unit_corpus"]) else 0.0

        # quantity 与 published 是两个不同强度的文本信号，取加权而非加法
        s_txt = 0.7 * s_qty + 0.3 * s_pub

        score = (W_DOMAIN * s_dom + W_QUANTITY * s_txt
                 + W_UNIT * s_unit + W_VARIABLE * s_var)

        terms: List[str] = []
        if s_dom >= 1.0:
            terms.append(f"domain:{feat['prefix']}")
        elif s_dom >= 0.5:
            terms.append(f"domain~{feat['prefix']}")
        qty_hits = sorted(set(q_tokens) & set(feat["key_tokens"]))
        if qty_hits:
            terms.append("quantity:" + ",".join(qty_hits))
        pub_hits = sorted(set(q_tokens) & set(feat["published_tokens"]))
        if pub_hits and not qty_hits:
            terms.append("published:" + ",".join(pub_hits[:3]))
        if s_unit:
            terms.append(f"unit:{q_unit}")
        var_hits = sorted(set(var_tokens) & set(feat["key_tokens"]))
        if var_hits:
            terms.append("variable:" + ",".join(var_hits))

        # --- 硬约束：纯 domain 命中不足以路由 ---
        # 「Microbiology + biomass」若只靠 discipline 前缀命中，会把调用方
        # 带到一个与生物量无关的 micro_* 场景。宁可空手，不猜。
        if s_txt == 0.0 and s_unit == 0.0 and s_var == 0.0:
            continue
        if not terms or score < MATCH_FLOOR:
            continue

        scored.append({
            "scenario_key": key,
            "score": round(score, 4),
            "matched_terms": terms,
            "signals": {
                "domain": round(s_dom, 4),
                "quantity": round(s_txt, 4),
                "unit": round(s_unit, 4),
                "variable": round(s_var, 4),
            },
            "verdict": feat["verdict"],
            "gate": feat["gate"],
        })

    # tie-break 需要的索引：anchor 分级与复算通过状态
    _GRADE_INDEX = {k: (v.get("anchor_grade") or "")
                    for k, v in idx.items()}
    _REPL = {k: bool(v.get("replication_passed")) for k, v in idx.items()}

    # 确定性排序：分数降序 → 证据强度（anchor grade）→ 复算通过 → key 升序。
    # 分数并列时**不按字母序**：那会把正确答案排到候选窗口之外，
    # 而证据强弱的次序是可辩护的。
    _GRADE_RANK = {"AGREE": 3, "NEAR": 2, "CONTRADICTED": 0, "": 1, None: 1}
    scored.sort(key=lambda m: (
        -m["score"],
        -_GRADE_RANK.get(_GRADE_INDEX.get(m["scenario_key"]), 1),
        0 if _REPL.get(m["scenario_key"]) else 1,
        m["scenario_key"],
    ))
    top = scored[:top_k]
    best = top[0]["score"] if top else 0.0

    if top:
        diag = (f"命中 {len(top)} 个候选，最佳 {top[0]['scenario_key']} "
                f"score={best}；依据 {', '.join(top[0]['matched_terms'])}。"
                f"单位 {unit!r} 只做字面匹配，"
                "量纲等价性（mg/L ≡ g/L）由 "
                "vlab_contract.goal_quantity_binding 独立把关。")
    else:
        parts = []
        if not discipline and not q_tokens and not var_tokens and not q_unit:
            parts.append("查询为空（discipline/quantity/unit/variables 全部缺失）")
        elif q_unit:
            parts.append("查询单位在全部场景的单位语料中都没出现过")
        else:
            parts.append("没有任何候选达到 MATCH_FLOOR=%.2f" % MATCH_FLOOR)
        parts.append(f"索引中有 {len(idx)} 个场景")
        if not discipline:
            parts.append("未给 discipline，专业域信号缺失")
        parts.append("这不是错误，是诚实的未命中——硬凑一个低分场景"
                     "会让交付的报告答非所问")
        diag = "；".join(parts) + "。"

    return {
        "matches": top,
        "match_floor": MATCH_FLOOR,
        "best_score": best,
        "hit": bool(top),
        "diagnostic": diag,
    }


def route_from_work_order(wo: Dict[str, Any], top_k: int = 3) -> dict:
    """从一份 vlab work order 直接路由（取 goal 与 design_space）。"""
    goal = wo.get("goal") or {}
    ds = wo.get("design_space") or {}
    return route({
        "discipline": wo.get("discipline"),
        "quantity": goal.get("quantity"),
        "unit": goal.get("unit"),
        "variables": ds.get("variables") or [],
    }, top_k=top_k)


# ------------------------------------------------------------------ CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="离线场景模糊路由（只读 gate_ledger.json）")
    p.add_argument("--discipline")
    p.add_argument("--quantity")
    p.add_argument("--unit")
    p.add_argument("--variable", action="append", default=[])
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--index-only", action="store_true",
                   help="只打印索引规模，不路由")
    a = p.parse_args(argv)

    idx = scenario_index()
    print(f"index: {len(idx)} scenarios  match_floor={MATCH_FLOOR}")
    if a.index_only:
        return 0

    out = route({
        "discipline": a.discipline,
        "quantity": a.quantity,
        "unit": a.unit,
        "variables": [{"name": v} for v in a.variable],
    }, top_k=a.top_k)
    print(f"hit={out['hit']}  best_score={out['best_score']}")
    if out["matches"]:
        for m in out["matches"]:
            print(f"  {m['score']:.4f}  {m['scenario_key']:28s} "
                  f"{m['verdict']:9s} {', '.join(m['matched_terms'])}")
    else:
        print("  (no candidate)")
    print("diagnostic:", out["diagnostic"])
    return 0 if out["hit"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MATCH_FLOOR", "W_DOMAIN", "W_QUANTITY", "W_UNIT", "W_VARIABLE",
    "W_PUBLISHED", "DISCIPLINE_PREFIXES", "STOPWORDS",
    "tokenize", "key_tokens", "normalize_unit", "extract_units",
    "load_ledger", "build_scenario_index",
    "scenario_index", "route", "route_from_work_order", "main",
]
