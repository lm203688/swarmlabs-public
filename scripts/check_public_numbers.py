"""Fail-closed gate: every number on a public page must trace to an artifact.

Run:  python scripts/check_public_numbers.py [--json]

Exit codes
----------
0  all quoted numbers are sourced and agree with data/public_facts.json
1  any finding at all: MISMATCH (page disagrees with the artifact),
   UNSOURCED (term has no artifact behind it), COUNT-DISAGREEMENT (artifacts
   that all call themselves "published scenario total" differ), or a registry
   that silently lost scenarios during the build (see below). This gate has no
   separate severity levels — every finding is fatal.

Wired into CI (`.github/workflows/ci-cd.yml`, two required jobs — not
continue-on-error). This docstring used to say "Deliberately NOT wired into CI
yet": that was true while the gate was still red, and it silently became a lie
the moment the job was added. Kept as a warning — a gate that misdescribes its
own enforcement is worse than no comment, because the next reader trusts it.

Silent-truncation guard (the reason this gate is not just a spell-checker): the
scenario registry is rebuilt from whatever engines import successfully, so a
rebuild on an interpreter missing rdkit silently drops the 2 `mol_*` scenarios —
including `mol_suzuki_yield`, the only REFUTED (hard-blocking) scenario on the
platform. The public page then truthfully reports "79 PASS / 2 MARGINAL / 0
REFUTED", i.e. "this platform has never been wrong", and the honesty
differentiator disappears without anyone editing a word. Consequences baked in:
a non-empty `dropped_scenarios` is fatal, and the total is cross-checked against
an independent registry (data/scenario_library_density.json) rather than against
`registry_total`, which is derived from the same truncated source and would
therefore agree with itself while both are wrong.

Why this gate exists: see the docstring of scripts/build_public_facts.py.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "data" / "public_facts.json"
PAGES = ROOT / "frontend_fixed"

SCRIPT_BLOCK = re.compile(r"<script[^>]*>.*?</script>", re.S | re.I)
STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def visible_text(html: str) -> str:
    """Text a human actually reads — strips <script>/<style> and tags.

    Stripping <script> matters: trust-dashboard.html embeds the whole trust
    surface JSON inline, and scanning it produces thousands of false hits.
    """
    t = SCRIPT_BLOCK.sub(" ", html)
    t = STYLE_BLOCK.sub(" ", t)
    t = TAG.sub(" ", t)
    return re.sub(r"[ \t]+", " ", t)


def num(s: str) -> int:
    return int(s.replace(",", "").replace("+", ""))


def check_page(name: str, text: str, facts: dict, undefined: dict) -> list[dict]:
    out: list[dict] = []

    def rec(kind, term, quoted, expected, why):
        out.append({"page": name, "kind": kind, "term": term,
                    "quoted": quoted, "expected": expected, "why": why})

    total = facts["validated_scenarios"]["value"]
    micro = facts["microbio_scenarios"]["value"]
    defined = facts.get("defined_scenarios", {}).get("value")

    # --- 1) "<N> scenarios" / "<N> 个场景" -------------------------------
    # 三个口径都必须能区分开：
    #   N validated scenarios -> validated_scenarios（62，已发布 V&V 报告）
    #   N defined scenarios   -> defined_scenarios（72，代码里有、报告未发布）
    #   N microbial scenarios -> microbio_scenarios（52，子集）
    for m in re.finditer(r"(\d[\d,]*)\+?\s*(scenarios?|个场景)", text, re.I):
        q = num(m.group(1))
        ctx = text[max(0, m.start() - 110): m.end() + 80].lower()
        if q == total:
            continue
        if q == micro and re.search(r"microbial|微生物|fermentation|发酵", ctx):
            continue  # 明确的微生物子集口径，允许
        if (defined is not None and q == defined
                and re.search(r"defined|registered|注册|代码|code|not yet|尚无报告", ctx)):
            continue  # 明确标注「已定义但未发布报告」的口径，允许
        rec("MISMATCH", m.group(2), q, total,
            f"页面写 {q}，产物 total={total}；若此处确指微生物子集应显式写"
            f"「microbial / 微生物」以便与总数区分")

    # --- 2) verdict triples "53 / 4 / 5" near verdict wording ------------
    for m in re.finditer(
        r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)(?=.{0,80}?(?:verdict|V&amp;V|V&V|PASS|判定))",
        text, re.I | re.S,
    ):
        got = tuple(num(g) for g in m.groups())
        want = (facts["verdict_pass"]["value"],
                facts["verdict_marginal"]["value"],
                facts["verdict_refuted"]["value"])
        if got != want:
            rec("MISMATCH", "PASS/MARGINAL/REFUTED", "/".join(map(str, got)),
                "/".join(map(str, want)),
                "判定分布已收口（见 engine-kit/skills/vv-gate/references/"
                "gate-semantics.md 的 pre/post 2026-09-15 对照表）；此页仍是旧口径")

    # --- 3) "N REFUTED" --------------------------------------------------
    for m in re.finditer(r"(\d+)\s*of\s*(\d+)\s*scenarios?\s*are\s*REFUTED", text, re.I):
        got = (num(m.group(1)), num(m.group(2)))
        want = (facts["verdict_refuted"]["value"], total)
        if got != want:
            rec("MISMATCH", "N of M REFUTED", f"{got[0]} of {got[1]}",
                f"{want[0]} of {want[1]}", "同上：旧口径")

    # --- 4) "engine" / "data points" family ------------------------------
    # 三类都要区分开，否则会把三种不同语义的量混成一个模糊的「引擎数」：
    #   N engine modules      -> 必须等于 engine_modules（模块数，可发布）
    #   N pending engines     -> 必须等于 gap / 领域 / 合计 之一（须标 pending）
    #   N engines /           -> 无产物支撑，永远失败
    #   N experiment engines
    # 正则用 [ \t] 而不是 \s：visible_text 会把相邻行压平，\s 会让
    # 「10,000 credits」和下一行的「All engines」拼成一个假引用。
    pend_total = facts.get("pending_engines_total", {}).get("value")
    pend_by = facts.get("pending_engines_by_domain", {}).get("value") or {}
    pend_gap = facts.get("pending_engines_gap", {}).get("value")
    mod_n = facts.get("engine_modules", {}).get("value")
    val_n = facts.get("validated_scenarios", {}).get("value")
    kg_n = facts.get("knowledge_graph_entities", {}).get("value")
    sp_n = facts.get("store_points_total", {}).get("value")

    for m in re.finditer(r"(?<![A-Za-z])(\d[\d,\.]*)\s*(?:\+|K\+|k\+)?[ \t]*"
                         r"([A-Za-z\-]+(?:[ \t]+[A-Za-z\-]+){0,3})", text, re.I):
        quoted, term = m.group(0).strip(), m.group(2)
        tl = term.lower()
        if "engine" not in tl and "data points" not in tl:
            continue
        ctx = text[max(0, m.start() - 320): m.end() + 160].lower()
        # 4a0) 紧邻前一个字符是引号 = 我们在校引别人的原话（常见于
        #      批评性对比文），不是本方宣称
        if text[m.start() - 1] in chr(34) + chr(8220) + chr(8221) + chr(8216) + chr(8217) + chr(12300) + chr(12301) + chr(39):
            continue

        # 4a) 引述的第三方主张 —— 竞品对比表里写别人家的数字不算我们发布的数字
        if re.search(r"claim(ed|s)?[ \t,;:—-]|claims? to|声称|宣称|据称|according to", ctx):
            continue
        # 4b) 商业条款里的权益 —— Free/Pro/Team 的引擎权益是合同用语，不是
        #     「已交付引擎数」的宣称，不做产物溯源。
        if re.search(r"\bplan\b|credits?|per day|/day|unlock|entitlement|tier", ctx):
            continue
        # 4c) 知识图谱实体 —— 与「验证数据点」语义不同，不允许互换
        if "entit" in tl:
            if kg_n is not None and num(m.group(1)) != kg_n:
                rec("MISMATCH", "structured research entities", quoted, kg_n,
                    f"data/entity_store.json#counts.TOTAL_ENTITIES = {kg_n}；"
                    "实体库规模不得写成 validation data points")
            continue
        # 4d) 引擎模块
        if "module" in tl:
            if mod_n is not None and num(m.group(1)) != mod_n:
                rec("MISMATCH", "engine modules", quoted, mod_n,
                    f"engine/*.py = {mod_n} 个模块；计数的是模块而非已验证场景")
            continue
        # 4e) 待实现引擎 —— 允许，但必须紧邻 pending/backlog 字样，且数字要对
        if re.search(r"pending|backlog|开发中|计划中|未实现", ctx):
            q = num(m.group(1))
            # gap 口径优先（"pending engines in gap categories"），
            # 再按领域匹配（如 "PENDING Energy 12 engine"），最后退到合计
            if pend_gap is not None and re.search(r"gap|uncovered|空白", ctx):
                want = pend_gap
            else:
                want = None
                for dom, cnt in pend_by.items():
                    if dom and dom.lower() in ctx:
                        want = cnt
                        break
            if want is None:
                want = pend_total
            if want is not None and q != want:
                rec("MISMATCH", "pending engines", quoted, want,
                    "pending 引擎数须等于 scenario_library_density.json 的对应项"
                    "（gap 口径 / 领域口径 / 合计）")
            continue
        # 4f) 剩余一切「N engines / N experiment engines」—— 无产物支撑
        if "engine" in tl:
            rec("UNSOURCED", term, quoted, "—",
                undefined.get("engines", {}).get(
                    "reason", "该名词没有任何产物支撑"))
            continue
        # 4g) 数据点
        rec("UNSOURCED", term, quoted, "—",
            undefined.get("validation_data_points", {}).get(
                "reason", f"该名词没有任何产物支撑（可辩护的同类量是 "
                          f"store_points_total={sp_n} 与 "
                          f"knowledge_graph_entities={kg_n}，语义不同不得互换）"))

    # --- 6) held-out / 外推压力测试的 REFUTED —— 诚实失败的真凭据在这里
    rh = facts.get("heldout_robustness_refuted", {}).get("value")
    ex = facts.get("extrapolation_stress_refuted", {}).get("value")
    for m in re.finditer(r"(?<![A-Za-z])(\d+)\s+are\s+REFUTED\s+under\s+"
                         r"(robustness|extrapolation)", text, re.I):
        q, kind = num(m.group(1)), m.group(2).lower()
        want = rh if kind == "robustness" else ex
        if want is not None and q != want:
            rec("MISMATCH", f"{kind} held-out REFUTED", q, want,
                "主账本 REFUTED 为 0，失败集中在留出集与外推压力测试；这里引用错数"
                "等于把诚实失败藏起来")

    # --- 5) "<N> validated scenarios" must equal the report index --------
    for m in re.finditer(r"(\d[\d,]*)\s*(?:\+)?\s*validated scenarios", text, re.I):
        if num(m.group(1)) != val_n:
            rec("MISMATCH", "validated scenarios", m.group(1), val_n,
                f"reports/index.json#total = {val_n}；若指代码里已定义但未发布报告的"
                f"场景数，请改用 pending 口径或明确标注")

    # --- 7) "N stored calibration points" --------------------------------
    # 这个数此前完全没有门禁覆盖：index.html 长期写 158 而实际是 161。
    # 它是最容易被读者当成「数据量」误读的数，必须能溯源。
    for m in re.finditer(r"(\d[\d,]*)\s*(?:\+)?\s*stored calibration points", text, re.I):
        q = num(m.group(1))
        if sp_n is not None and q != sp_n:
            rec("MISMATCH", "stored calibration points", q, sp_n,
                "唯一可辩护的「数据量」口径 = sum(trust_surface.rows[].n_store)")
        elif sp_n is None:
            rec("UNSOURCED", "stored calibration points", q, "—",
                "事实表缺 store_points_total，无法溯源")

    return out


def _claim_text_violations(name: str, text: str, facts: dict, where: str) -> list[dict]:
    """对一段「会对外发布、但不在可见正文里」的文本做数字口径校验。

    这类文本有两个来源，共同点是 `visible_text()` 看不到它们：
      - <script> 块（运行时渲染进 DOM 的文案）
      - <meta> / og: / twitter: 的 content（搜索引擎与 GEO 爬虫读的就是这个）

    2026-09-21 实测的两个盲区：
      - lab.html 引导弹窗写 "62 Validated Scenarios on 54 Engine Modules"，
        而账本是 82/57 —— 在 <script> 里，门禁 PASS。
      - meta description 里的数字此前**完全**无覆盖，而本站的分发主渠道
        正是 GEO，爬虫读的恰恰是这些字段。

    为避免误报，只匹配**断言式短语**（"N validated scenarios" /
    "N engine modules" / "N stored calibration points" / "N engines"），
    不匹配裸数字：数据载荷里出现的数不是宣称。
    """
    out: list[dict] = []
    val_n = facts.get("validated_scenarios", {}).get("value")
    mod_n = facts.get("engine_modules", {}).get("value")
    sp_n = facts.get("store_points_total", {}).get("value")
    defined = facts.get("defined_scenarios", {}).get("value")
    micro = facts.get("microbio_scenarios", {}).get("value")

    def rec(term, quoted, expected, why):
        out.append({"page": name, "kind": "SCRIPT-CLAIM" if where == "script" else "META-CLAIM",
                    "term": f"{term} (in <{where}>)", "quoted": quoted,
                    "expected": expected, "why": why})

    for m in re.finditer(r"(\d[\d,]*)\+?[ \t]*validated scenarios", text, re.I):
        q = num(m.group(1))
        if val_n is not None and q != val_n:
            rec("validated scenarios", m.group(0), val_n,
                "此处会对外发布（爬虫/运行时渲染），同样受口径门禁约束，"
                "不得停留在旧版本")
    for m in re.finditer(r"(\d[\d,]*)\+?[ \t]*engine modules", text, re.I):
        q = num(m.group(1))
        if mod_n is not None and q != mod_n:
            rec("engine modules", m.group(0), mod_n, "engine/*.py 模块数")
    for m in re.finditer(r"(\d[\d,]*)\+?[ \t]*stored calibration points", text, re.I):
        q = num(m.group(1))
        if sp_n is not None and q != sp_n:
            rec("stored calibration points", m.group(0), sp_n,
                "唯一可辩护的数据量口径")
    # verdict triples "PASS n / m / k"
    for m in re.finditer(r"(\d+)\s*PASS\s*,\s*(\d+)\s*MARGINAL\s*,\s*(\d+)\s*REFUTED", text, re.I):
        got = tuple(num(g) for g in m.groups())
        want = (facts["verdict_pass"]["value"], facts["verdict_marginal"]["value"],
                facts["verdict_refuted"]["value"])
        if got != want:
            rec("verdict triple", m.group(0), "/".join(map(str, want)), "判定分布已收口")
    for m in re.finditer(r"(\d[\d,]*)\+?[ \t]*(?:experiment |backend )?engines\b", text, re.I):
        q = num(m.group(1))
        ctx = text[max(0, m.start() - 140): m.end() + 90].lower()
        if re.search(r"pending|backlog|planned|credits?|plan\b|tier", ctx):
            continue
        if q in (val_n, mod_n, sp_n, defined, micro):
            continue
        rec("engines", m.group(0), "—",
            "没有任何产物生成「已交付引擎数量」；合法替代是 "
            f"engine modules={mod_n}")
    return out


def check_script_claims(name: str, html: str, facts: dict) -> list[dict]:
    """<script> 块内的声明式数字（运行时会渲染给用户看）。"""
    out: list[dict] = []
    for block in SCRIPT_BLOCK.findall(html):
        out.extend(_claim_text_violations(name, block, facts, "script"))
    return out


def check_meta_claims(name: str, html: str, facts: dict) -> list[dict]:
    """<meta> / og / twitter 的 content —— 爬虫读的就是这里。"""
    out: list[dict] = []
    chunks = []
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        if not re.search(r"(description|og:|twitter:)", tag, re.I):
            continue
        for c in re.finditer(r"content=[\"']([^\"']*)[\"']", tag, re.I):
            chunks.append(c.group(1))
    # JSON-LD 里的 description/headline 同样是给机器读的
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
                         html, re.S | re.I):
        chunks.append(m.group(1))
    for ch in chunks:
        out.extend(_claim_text_violations(name, ch, facts, "meta"))
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    d = json.loads(FACTS.read_text(encoding="utf-8"))
    flat = {k: v for k, v in d["facts"].items() if v["value"] is not None}
    undefined = d["undefined_claims"]

    # ---- 1) 页面 vs 事实表 ------------------------------------------------
    rows: list[dict] = []
    time_stamped: list[str] = []
    for p in sorted(PAGES.rglob("*.html")):
        rel = p.relative_to(ROOT).as_posix()
        raw = p.read_text(encoding="utf-8", errors="ignore")
        text = visible_text(raw)
        hits = check_page(rel, text, flat, undefined)
        # <script> 内的声明式数字（会渲染进 DOM 给用户看）+ meta/OG/JSON-LD
        # （爬虫读的字段）各校一遍 —— 这两处 visible_text() 都看不见
        hits += check_script_claims(rel, raw, flat)
        hits += check_meta_claims(rel, raw, flat)
        if not hits:
            continue
        # 带自我日期标注的编辑性长文（如 2026-09-12 的失败率分析）算历史记录，
        # 但必须真的在正文里写明 as-of 日期，否则仍按失败处理。
        if "/blog/" in rel and re.search(
            r"as of|on 20\d\d-\d\d|20\d\d-\d\d-\d\d (实测|实测)|实测\)|on \d{4}-\d{2}-\d{2}/\d{2}",
            text, re.I,
        ):
            time_stamped.append(rel)
            continue
        rows.extend(hits)

    # ---- 0) 事实表自身是否新鲜 + 跨产物场景数是否一致 ----------------------
    # 这一层是必须的：上面的 check_page 只校验「页面 vs public_facts」。
    # 若 public_facts 自己就是旧的，页面越听话、越整齐，越是集体假绿。
    # 2026-09-21 实测：public_facts 停在 62 而产物已是 82，页面全写 62，
    # 于是门禁报 PASS —— 一个「所有页面都对，但全都在说上个版本」的假绿。
    stale = verify_fact_sources(d)
    disagree = verify_scenario_count_agreement(d)

    if as_json:
        print(json.dumps({"violations": rows, "time_stamped_ok": time_stamped,
                          "stale_facts": stale, "count_disagreement": disagree},
                         ensure_ascii=False, indent=2))
        return 1 if (rows or stale or disagree) else 0

    print("=" * 78)
    print("公开页数字口径门禁 (fail-closed)   facts: data/public_facts.json")
    print("=" * 78)
    if not rows:
        print("\nPASS — 所有公开页引用的数字均可溯源且与产物一致")
    for r in rows:
        print(f"\n[{r['kind']}] {r['page']}")
        print(f"    引用  {r['term']} = {r['quoted']}   应为 {r['expected']}")
        print(f"    原因  {r['why']}")
    for s in stale:
        print(f"\n[STALE-FACT] {s['facts_file']} :: {s['fact']}")
        print(f"    事实表写 {s['in_facts']}，但其 source 指向的产物现在是 {s['in_artifact']}")
        print(f"    来源  {s['source']}")
        print(f"    原因  事实表本身已经过期 —— 先跑 scripts/build_public_facts.py")
    for s in disagree:
        print(f"\n[COUNT-DISAGREEMENT] {s['fact']}")
        print(f"    值    {json.dumps(s['values'], ensure_ascii=False)}")
        print(f"    原因  {s['why']}")
    if time_stamped:
        print(f"\n[INFO] 时间戳长文，按历史记录放行（正文已自证 as-of 日期）: {time_stamped}")
    n_mis = sum(1 for r in rows if r["kind"] == "MISMATCH")
    n_uns = sum(1 for r in rows if r["kind"] == "UNSOURCED")
    # 合计必须覆盖所有 kind。此前只数 MISMATCH/UNSOURCED，于是 SCRIPT-CLAIM
    # 能让门禁变红而合计仍打印 0/0/0/0 —— 报告自己说谎，等于门禁失效。
    n_other = len(rows) - n_mis - n_uns
    kinds_other = sorted({r["kind"] for r in rows
                          if r["kind"] not in ("MISMATCH", "UNSOURCED")})
    bad = bool(rows or stale or disagree)
    print(f"\n合计: {n_mis} MISMATCH / {n_uns} UNSOURCED / "
          f"{n_other} {'+'.join(kinds_other) if kinds_other else 'OTHER'} / "
          f"{len(stale)} STALE-FACT / {len(disagree)} COUNT-DISAGREEMENT"
          f"  ->  exit {1 if bad else 0}")
    return 1 if bad else 0


_RX_COUNT = re.compile(r"^count\((?P<inner>.+)\)$", re.S)
_RX_SUM = re.compile(r"^sum\((?P<inner>.+)\)$", re.S)
_RX_HIST = re.compile(r"^histogram\((?P<inner>.+)\)$", re.S)
_RX_HISTALIAS = re.compile(r"^histogram_alias\((?P<inner>.+)\)$", re.S)
_RX_CEXC = re.compile(r"^count_excluding\((?P<inner>.+)\)$", re.S)


def _expand(src: str, last_dir: str | None) -> str:
    if src.startswith("…") or src.startswith("..."):
        return (last_dir or "") + src[1:]
    return src


def _dir_of(path: str) -> str:
    """目录前缀（带尾部 `/`）。

    `…index.json#counts.PASS` 省略的是 `frontend_fixed/reports/`，
    不是 `frontend_fixed/reports/index.json`。此前把**完整文件名**当成
    `last_path` 存下来，展开成 `frontend_fixed/reports/index.jsonindex.json#...`
    这种不存在的路径 ⇒ 9 条 facts 全部误报 KeyError，而这些数字其实完全一致。
    """
    p = path.replace("\\", "/").rstrip("/")
    return (p.rsplit("/", 1)[0] + "/") if "/" in p else ""


def _split_ptr(expr: str):
    if "#" not in expr:
        return None, None
    path, dotted = expr.split("#", 1)
    return path.strip(), dotted.strip()


def _walk(obj, dotted: str):
    cur = obj
    for seg in [x for x in dotted.split(".") if x.strip()]:
        cur = cur[int(seg)] if isinstance(cur, list) else cur[seg.strip()]
    return cur


def _walk_projection(obj, prefix: str):
    """`rows[]` 形态：返回 (list, 字段后缀)。"""
    base, _, suffix = prefix.partition("[]")
    cur = _walk(obj, base) if base else obj
    return cur, suffix.lstrip(".")


def _histogram_source(inner: str, last_path: str | None):
    """`histogram(<path>#<projection>[])` → {值: 出现次数}。"""
    inner = _expand(inner, last_path)
    path, dotted = _split_ptr(inner)
    if path is None:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        lst, field = _walk_projection(obj, dotted)
        if not isinstance(lst, list):
            return None, None
        out: dict = {}
        for item in lst:
            v = _walk(item, field) if field else item
            out[str(v)] = out.get(str(v), 0) + 1
        return path, out
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"


def _histogram_alias_source(inner: str, last_dir: str | None):
    """`histogram_alias(<path>#<projection>[], <alias json>)` → {桶: 计数}。

    桶规则固定：取字段值在 `/` 之前的首段（两端空白去掉），
    再用来源字符串**显式声明**的别名表做一次映射。

    别名表不允许在校验器里另存一份——事实表与校验器各持一份映射、
    改一边不改另一边，就是又一次口径漂移（与 57→64 那次同源）。
    """
    inner = _expand(inner, last_dir)
    depth = 0
    cut = -1
    for i, ch in enumerate(inner):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            cut = i
            break
    if cut < 0:
        return None, None
    proj_expr, alias_expr = inner[:cut].strip(), inner[cut + 1:].strip()
    try:
        aliases = json.loads(alias_expr)
    except Exception:
        return None, None
    if not isinstance(aliases, dict):
        return None, None
    path, dotted = _split_ptr(proj_expr)
    if path is None:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        lst, field = _walk_projection(obj, dotted)
        if not isinstance(lst, list):
            return None, None
        out: dict = {}
        for item in lst:
            raw = _walk(item, field) if field else item
            head = str(raw or "").split("/", 1)[0].strip()
            bucket = aliases.get(head, head)
            out[bucket] = out.get(bucket, 0) + 1
        return path, out
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"


def _count_excluding_source(inner: str, last_path: str | None):
    """`count_excluding(<path>#<projection>[], [<excluded values>])` → 计数。

    第二个参数是 **JSON 数组字面量**，必须显式写出被排除的取值 ——
    排除规则不可推断，只能由来源字符串声明。
    """
    inner = _expand(inner, last_path)
    # 在**顶层逗号**处切成「投影」与「排除列表」
    depth = 0
    cut = -1
    for i, ch in enumerate(inner):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            cut = i
            break
    if cut < 0:
        return None, None
    proj_expr = inner[:cut].strip()
    excl_expr = inner[cut + 1:].strip()
    try:
        excluded = json.loads(excl_expr)
    except Exception:
        return None, None
    excluded = [excluded] if not isinstance(excluded, list) else excluded
    path, dotted = _split_ptr(proj_expr)
    if path is None:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        lst, field = _walk_projection(obj, dotted)
        if not isinstance(lst, list):
            return None, None
        n = 0
        for item in lst:
            v = _walk(item, field) if field else item
            if not any(v == e for e in excluded):
                n += 1
        return path, n
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"


def _count_source(src: str, last_path: str | None):
    """`count(<glob>)` / `count(<path>#<pointer>)` —— 两种都无歧义。

    2026-09-21 实测的盲区：`engine_modules` 的来源写的是 `engine/*.py`（glob），
    旧实现见到 `*` 就整条跳过 ⇒ **这条事实永远不参与陈旧性检查**。
    于是引擎从 57 涨到 64 时，事实表仍写 57，前端 5 处照抄 57，而门禁报 0 违规
    —— 「事实表自身陈旧」这一类被静默放过了。

    修法不是放宽（"看到 * 就猜是文件数"），而是**新增显式可求值形式**：
    来源必须写成 `count(...)`，与纯描述性来源区分开。

    L-01 (2026-10-17)：`engine/*.py excluding __init__.py` 是唯一显式排除形式，
    必须委派到 scripts/engine_module_count.py（唯一真源），而不是让 glob
    自己数出 101（含 __init__.py）。这样 public_facts / build_frontend_metrics /
    本门禁三处对"引擎模块数"的看法严格一致，再也不会 100 vs 101 打架。
    """
    inner = _expand(src, last_path)
    # 显式排除形式：`<glob> excluding <name>` —— 直接委派到唯一真源
    if " excluding " in inner and inner.startswith("engine/*.py"):
        import sys as _sys
        _scripts_dir = str((ROOT / "scripts").resolve())
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from engine_module_count import engine_module_count as _ec  # noqa: E402
        return f"count({inner})", _ec()
    if "*" in inner:
        import glob as _glob
        n = len(_glob.glob(str(ROOT / inner), recursive=True))
        return f"count({inner})", n
    path, dotted = _split_ptr(inner)
    if path is None:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        cur = _walk(obj, dotted)
        if isinstance(cur, (list, dict, str)):
            return path, len(cur)
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"
    return None, None


def _sum_source(src: str, last_path: str | None):
    """`sum(<path>#<pointer>)` —— 三种形态：

    - 带 `[]` 投影（`rows[].n_store`）：对列表逐项取字段求和；
    - 解析结果是 dict：对其**数值型** value 求和（非数值跳过）；
    - 解析结果是 list：对其数值项求和。

    三种形态由**解析结果的实际结构**唯一决定，不含猜测。
    """
    inner = _expand(src, last_path)
    path, dotted = _split_ptr(inner)
    if path is None:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"
    try:
        if "[]" in dotted:
            lst, field = _walk_projection(obj, dotted)
            if not isinstance(lst, list):
                return None, None
            total = 0
            for item in lst:
                v = _walk(item, field) if field else item
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    total += v
            return path, total
        cur = _walk(obj, dotted)
        if isinstance(cur, dict):
            nums = [v for v in cur.values()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
        elif isinstance(cur, list):
            nums = [v for v in cur
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
        else:
            return None, None
        return path, sum(nums)
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"


def _eval_source(src: str, last_path: str | None):
    """闭集：`count(...)` / `sum(...)` / 纯 `path#pointer`。其余跳过。"""
    if not src:
        return None, None
    m = _RX_COUNT.match(src.strip())
    if m:
        # 传入的是括号**内**的表达式 —— 传整个 `count(...)` 会让 glob 变成
        # `count(engine/*.py)` 这种诡异 pattern，匹配到 0 个文件（本轮实测的 bug）。
        return _count_source(m.group("inner"), last_path)
    m = _RX_SUM.match(src.strip())
    if m:
        return _sum_source(m.group("inner"), last_path)
    m = _RX_HIST.match(src.strip())
    if m:
        return _histogram_source(m.group("inner"), last_path)
    m = _RX_HISTALIAS.match(src.strip())
    if m:
        return _histogram_alias_source(m.group("inner"), last_path)
    m = _RX_CEXC.match(src.strip())
    if m:
        return _count_excluding_source(m.group("inner"), last_path)
    return _resolve_pointer(src, last_path)


def _resolve_pointer(src: str, last_path: str | None):
    """把 'path#a.b.c' 解析成 (path, value)。无法解析时返回 (None, None)。

    只处理可机械求值的形态；纯描述性来源、以及**未显式写成 **
    `count(...)` / `sum(...)` 的聚合/glob 一律跳过 —— 宁可少校验，
    也不要造出一个自己会猜的校验器。
    """
    if not src or src.startswith("sum(") or "*" in src:
        return None, None
    # '[]' 是数组投影（如 rows[].domain），是聚合语义而非标量指针 ——
    # 本函数不猜聚合方式，直接跳过；要校验必须显式写成 sum(...)。
    if "[]" in src:
        return None, None
    if src.startswith("…") or src.startswith("..."):
        src = (last_path or "") + src[1:]
    if "#" not in src:
        return None, None
    path, dotted = src.split("#", 1)
    path = path.strip()
    if not path:
        return None, None
    p = ROOT / path
    if not p.exists():
        return path, KeyError(path)
    try:
        if p.suffix == ".json":
            obj = json.loads(p.read_text(encoding="utf-8"))
            cur = obj
            for seg in dotted.split("."):
                seg = seg.strip()
                if not seg:
                    continue
                if isinstance(cur, list):
                    cur = cur[int(seg)]
                else:
                    cur = cur[seg]
            return path, cur
    except Exception as e:  # noqa: BLE001
        return path, f"<unresolved: {type(e).__name__}: {e}>"
    return None, None


def verify_fact_sources(doc: dict) -> list[dict]:
    """事实表里每条 fact 都声明了 source；这里把它读回来核对。

    这是「单一真源」的闭环：source 不是给人看的注释，是可执行指针。
    """
    out: list[dict] = []
    last_dir: str | None = None
    for name, meta in (doc.get("facts") or {}).items():
        src = meta.get("source") or ""
        # 聚合形式（count/sum/histogram/count_excluding/...）里包着的是括号内的
        # 表达式；`…` 简写的省略前缀必须记**目录**而非文件名（见 _dir_of）。
        inner = src[src.index("(") + 1: src.rindex(")")] if "(" in src else src
        if inner.startswith("frontend_fixed/") or inner.startswith("data/"):
            last_dir = _dir_of(inner.split("#", 1)[0])
        path, got = _eval_source(src, last_dir)
        if path is None or (isinstance(got, str) and got.startswith("<unresolved")):
            if isinstance(got, str) and got.startswith("<unresolved"):
                out.append({"facts_file": "data/public_facts.json", "fact": name,
                            "in_facts": meta.get("value"), "in_artifact": got,
                            "source": src})
            elif src:
                # 来源非空、却求值不了 —— 显式记为「不可求值」而不是静默放过。
                # 静默跳过正是 2026-09-21 那次盲区（engine_modules 写 57 而实际 64）
                # 能被放过的原因。要让盲区**可见**，哪怕暂时修不掉。
                out.append({"facts_file": "data/public_facts.json", "fact": name,
                            "in_facts": meta.get("value"),
                            "in_artifact": "<source not evaluable>",
                            "source": src})
            continue
        if isinstance(got, (int, float)) and isinstance(meta.get("value"), (int, float)):
            if got != meta["value"]:
                out.append({"facts_file": "data/public_facts.json", "fact": name,
                            "in_facts": meta["value"], "in_artifact": got, "source": src})
        elif isinstance(got, dict) and isinstance(meta.get("value"), dict):
            if got != meta["value"]:
                out.append({"facts_file": "data/public_facts.json", "fact": name,
                            "in_facts": meta["value"], "in_artifact": got, "source": src})
        elif (isinstance(meta.get("value"), (int, float))
              and not isinstance(got, (int, float, dict))):
            # 值是一侧标量、算出来是另一侧容器 ⇒ 口径不一致，不是"跳过"
            out.append({"facts_file": "data/public_facts.json", "fact": name,
                        "in_facts": meta.get("value"),
                        "in_artifact": f"<type mismatch: {type(got).__name__}>",
                        "source": src})
    return out


def verify_scenario_count_agreement(doc: dict) -> list[dict]:
    """同一个「已发布场景总数」不许在几个产物里给出几个答案。

    2026-09-21 实测到的分裂态：报告库 82 / 可信面 62 / 密度表 72 / 主账本 80。
    它们各自都有正确的生成脚本，问题在于没有人负责「它们必须相等」。
    这个函数就是那个负责人。

    注意：密度表的 `total_scenarios_loaded` 是**注册表口径**（代码里已定义），
    本来就允许大于已发布数，所以不进「必须相等」这一组——但差值必须能被
    index.json 里的 `optional_deps.dropped_molecular_scenarios` 解释，
    否则按「静默截断」报出。
    """
    out: list[dict] = []
    probes = {
        "reports/index.json#total": (ROOT / "frontend_fixed/reports/index.json", ["total"]),
        "gate_ledger#counts(sum)": (ROOT / "frontend_fixed/reports/gate_ledger.json", ["counts"]),
        "trust_surface#overall.scenarios": (ROOT / "data/trust_surface.json", ["overall", "scenarios"]),
    }
    vals: dict[str, object] = {}
    for label, (p, keys) in probes.items():
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            for k in keys:
                obj = obj[k]
        except Exception:  # noqa: BLE001
            continue
        if label.endswith("(sum)") and isinstance(obj, dict):
            obj = sum(int(v) for v in obj.values() if isinstance(v, int))
        vals[label] = obj

    # 注册表口径（密度表）比已发布多很正常——那是「代码里已定义」。
    # 但它必须 ≥ 已发布，且差值要能被「可选依赖丢弃」解释。
    dens_p = ROOT / "frontend_fixed/reports/scenario_library_density.json"
    if dens_p.exists():
        try:
            dens = json.loads(dens_p.read_text(encoding="utf-8")).get("total_scenarios_loaded")
        except Exception:  # noqa: BLE001
            dens = None
        if isinstance(dens, int):
            vals["scenario_library_density#total_scenarios_loaded(注册表口径)"] = dens
            pub = vals.get("reports/index.json#total")
            idx_p = ROOT / "frontend_fixed/reports/index.json"
            dropped: list = []
            reg_total = None
            if idx_p.exists():
                try:
                    m = json.loads(idx_p.read_text(encoding="utf-8"))
                    reg_total = m.get("registry_total")
                    dropped = (((m.get("optional_deps") or {})
                                .get("dropped_molecular_scenarios")) or [])
                except Exception:  # noqa: BLE001
                    pass
            if isinstance(pub, int) and dens < pub:
                out.append({
                    "fact": "registry vs published scenarios",
                    "values": {"registry": dens, "published": pub},
                    "why": "注册表里已定义的场景数不该少于已发布报告数——"
                           "说明发布产物覆盖了一个注册表里不存在的集合",
                })

            # ---- 自报注册表口径 vs 独立注册表口径 ---------------------------
            # index.json 的 registry_total 是由 len(all_scenarios()) 算出的，而那个
            # 注册表**本身是可选的**：rdkit 缺失时它少注册 2 个分子场景，于是
            # registry_total 跟着一起塌。密度表是另一个生成器独立记录的注册表口径，
            # 二者分裂即证明本次构建被环境截断过——自报值不可单独采信。
            if isinstance(reg_total, int) and reg_total != dens:
                out.append({
                    "fact": "registry caliber split (index vs density)",
                    "values": {"index.json#registry_total": reg_total,
                               "scenario_library_density#total_scenarios_loaded": dens},
                    "why": "两个产物各自声明「代码里已定义的场景总数」，必须相等。"
                           "index.json 的自报值来自可能被截断的注册表，不能单独作为"
                           "「应当发布多少」的判据；分裂说明本次构建用的是残缺环境。",
                })

            # ---- 静默截断守卫（判据换成独立来源）--------------------------
            # 2026-09-23 实测的假绿：rdkit 缺失时 index.json#registry_total 也塌成
            # 80，于是旧判据 `reg_total > pub`（80 > 80）为假，整段守卫**被短路**——
            # 它在唯一该触发的场景里是死代码。唯一的 REFUTED 场景（mol_suzuki_yield）
            # 就这样从公开账本里消失，线上变成「78 PASS / 2 MARGINAL / 0 REFUTED」。
            # 因此判据改用独立的密度表口径，且「已声明丢弃」不再依赖总数比较。
            ref_reg = dens if isinstance(dens, int) else reg_total
            if isinstance(ref_reg, int) and isinstance(pub, int) and ref_reg > pub:
                if len(dropped) != ref_reg - pub:
                    out.append({
                        "fact": "silently dropped scenarios",
                        "values": {"registry_total": ref_reg, "published_total": pub,
                                   "dropped_declared": list(dropped),
                                   "declared_count": len(dropped),
                                   "expected_dropped": ref_reg - pub},
                        "why": "发布账本比注册表少场景，却没有在 index.json 里说明是哪几个、"
                               "为什么丢。2026-09-21 实测：rdkit 缺失时两个 mol_* 场景被静默抹掉，"
                               "其中就包含唯一的 REFUTED——诚实失败的公开记录被环境依赖悄悄删掉了",
                    })

            # ---- 「自报丢弃」本身即硬失败，不看总数是否相等 -----------------
            # 为什么不能靠「counts 自洽」放行：rdkit 缺失时 index.json 的 total 与
            # registry_total 都塌成 80，两侧自洽只证明「两个数来自同一个残缺注册表」，
            # 完全不能证明发布完整。少发场景的账本不是有效发布产物——尤其当被丢掉的
            # 是唯一那条 REFUTED 时：一个「零失败」的公开账本，是把诚实失败记录
            # 悄悄删掉的结果，而公开失败记录正是本站相对竞品的核心差异点。
            # 修法（可执行、不靠自觉）：用带 rdkit 的解释器重建 ——
            #   ~/.workbuddy/binaries/python/envs/default/Scripts/python scripts/build_reports.py
            if dropped:
                out.append({
                    "fact": "incomplete build declared (dropped scenarios)",
                    "values": {"dropped": list(dropped), "published_total": pub,
                               "index_registry_total": reg_total, "density_total": dens},
                    "why": "index.json 自己声明本次构建丢弃了场景（可选依赖缺失）。"
                           "这不是警告而是阻断：用带 rdkit 的解释器重跑 build_reports.py 后重建账本，"
                           "丢弃列表必须为空。若确实要发布缩减包，必须先显式改注册表，"
                           "不允许环境差异静默决定对外的场景数。",
                })
    uniq = {x for k, x in vals.items() if "注册表口径" not in k}
    if len(uniq) > 1:
        out.append({
            "fact": "scenario count",
            "values": {k: x for k, x in vals.items() if "注册表口径" not in k},
            "why": "这几个产物都自称是「已发布场景总数」，必须相等。若确实存在不同口径"
                   "（例如可信面的 rubric 子集），必须显式改名而不是共用「场景总数」；"
                   "历史事故见 scripts/paper_validation_loop.py::_build_science_scenarios 的 docstring。",
        })
    # 事实表还必须与它们一致（注册表口径不参与：它本就允许大于已发布数）
    v = (doc.get("facts") or {}).get("validated_scenarios", {}).get("value")
    comparable = {k: x for k, x in vals.items() if "注册表口径" not in k}
    if v is not None and comparable and any(v != x for x in comparable.values()):
        out.append({
            "fact": "validated_scenarios vs artifacts",
            "values": {"public_facts": v, **vals},
            "why": "事实表里的 validated_scenarios 与任一产物不一致",
        })
    return out


if __name__ == "__main__":
    sys.exit(main())
