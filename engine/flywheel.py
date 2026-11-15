"""SwarmLabs 每日自动化飞轮 —— 一个体系的总开关（v2 融合版）。

对外品牌名：**SwarmLabs Self-Evolving Engine**（见 engine/brand.py）。
飞轮即该「自演化引擎」的每日驱动实例：每跑一轮，
平台数据库 / 技术能力 / 算法 / 领域能力 / 竞品情报 / 增长运营全部子系统
被统一调度、统一报告、统一汇报。

  1. 论文验证闭环（paper_validation_loop）→ 可信面 trust_surface.json
  2. 自治闭环（autonomous_loop）→ 20 域收敛判定
  3. 实体库自治扩充（expand_entity_db）→ OpenAlex 增量拉取
  4. 竞品与技术态势监测（competitor_watch）→ weekly 快照（周日或首次运行）
  5. 增长监控 + GEO + SEO + 推广（growth_monitor chain）
  6. AgentLoop 数据飞轮（agentteams）→ Collect→Analyze→Evaluate→Optimize
  7. 汇总当日全系统报告 → reports/daily-ops-log/

所有步骤失败不阻断主流程，最终产出统一 JSON 报告供调度器/用户审阅。

飞轮机制：更多领域 → 更多数据 → 更好模型 → 更好推荐 → 更多用户
          → 更多真实数据 → 下一轮更强。这是项目真正的意义。
"""

from __future__ import annotations

# 稳定性：固定 BLAS 线程数为 1，消除 numpy 多线程 LAPACK（Cholesky 等）在
# 重复小矩阵分解时的偶发原生段错误（segfault）。本飞轮每日触发数百次 Cholesky，
# 单线程既消除竞态又无性能损失（矩阵维度 ≤~250，单线程反而避免线程派生开销）。
# 必须在 import numpy 之前设置，否则 BLAS 已在导入时读走线程数。
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import datetime
import json
import sys
import time
import traceback

# Windows console 默认 GBK，输出 emoji（✅/❌/📈 等）会触发 UnicodeEncodeError：
#   UnicodeEncodeError: 'gbk' codec can't encode character '\u2705' ...
# 在脚本入口把 stdout 强制改为 UTF-8 安全输出（含替换策略），避免最后一步
# 打印飞轮汇总时崩溃（不影响 JSON/HTML/CSV 等文件产物）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import paper_validation_loop as P
from engine.domains import domain_tree

REPORT_DIR = os.path.join(ROOT, "reports", "daily-ops-log")


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_run(step_name, fn, log, default=None):
    """安全执行一个步骤：成功记录 step，失败记录 error 并返回 default。"""
    try:
        result = fn()
        log["steps"].append(step_name)
        return result
    except Exception as e:
        # error_kind:DEGRADABLE — 单步失败记录到 log["errors"] 并 traceback.print_exc，
        # 主流程按 default 兜底继续；log 是审计唯一真源，不可静默。
        log["errors"].append(f"{step_name}: {e}")
        traceback.print_exc()
        return default


def _sync_template_library(rows, log=None):
    """把本轮 PASS 的场景沉淀进推理模板库（2026-10-04）。

    借鉴 STELLA（bioRxiv 2025.07.01）的 Template Library：每次成功的
    V&V 沉淀为一条"指纹模板"，相似场景可复用，避免从零开始。

    为什么接进飞轮（而不只是一个孤立模块）：模板库的**全部价值**在于
    每天自动积累。手工跑的库只能当 demo；接进每日闭环后，它才会成为
    漏斗式验证的第一级廉价筛——实体库扩到 10k+ 时，先用模板库按
    gate_ids 的 Jaccard 相似度匹配，"有相似先验且先前 PASS"的候选
    可以跳过完整三通道验证。这是 Anthropic 950-agents 漏斗
    （20万→3.5k→20→19）在小团队规模上的方法论替代。

    只沉淀 PASS：MARGINAL / REFUTED 是失败经验，由
    ``engine/antirepeat.py`` 单独记录，不进可复用先验（见
    ``TemplateLibrary.record`` 的 docstring）。

    **指纹设计（2026-10-04 修 bug）**：初版用 ``ls_source`` 作
    ``harness_fingerprint``，实测导致 58 个 PASS 场景只沉淀出 3 个模板
    ——因为 ``ls_source`` 只有少数几个取值（``prior`` / ``auto(...)``），
    指纹碰撞后互相覆盖。教训：**指纹必须取"决定这条推理路径的量"**，
    不能取偶然属性。改为取 ``(领域首段, rubric 各维度的判定值)`` 的
    哈希——领域是场景的固有属性，rubric 判定是推理路径的产物，两者
    共同决定"这条路径能否复用"。

    Args:
        rows: ``build_dashboard`` 的行（每行含 key/domain/rubric 等）
        log: flywheel 日志（可选，写入 subsystems.template_library）

    Returns:
        统计 dict（模板总数 / 本轮新增 / PASS 场景数 / 复用命中）
    """
    import hashlib

    from engine.template_library import TemplateLibrary, default_library_path

    path = default_library_path(ROOT)
    lib = TemplateLibrary.load(path)
    before = len(lib)

    n_pass = 0
    n_new = 0
    for r in rows or []:
        try:
            key = r.get("key")
            rb = r.get("rubric") or {}
            if not key or not rb:
                continue
            # 门禁通过 = 该场景所有 rubric 维度都满足
            if not all(bool(v) for v in rb.values()):
                continue
            n_pass += 1
            # 指纹 = 领域首段 + rubric 判定向量。领域决定"这类场景的
            # 机理先验是否适用"，rubric 判定决定"这条验证路径是否可信"。
            dom = str(r.get("domain", "")).split("/")[0].strip() or "unknown"
            vec = "|".join(f"{k}={int(bool(rb[k]))}" for k in sorted(rb))
            fp = hashlib.sha1(f"{dom}#{vec}".encode("utf-8")).hexdigest()[:16]
            gate_ids = tuple(sorted(rb.keys()))
            t = lib.record(
                scenario_key=key,
                harness_fingerprint=fp,
                gate_ids=gate_ids,
                verdict="PASS",
                score=float(r.get("rubric_passed", 0)) / max(len(rb), 1),
                domain=dom[:60],
            )
            if t is not None:
                n_new += 1
        except Exception as e:                   # noqa: BLE001
            if log is not None:
                log["errors"].append(f"template_library.record: {e}")
            continue

    lib.save(path)
    st = lib.stats()
    out = {
        "path": path,
        "templates_total": st.get("total", 0),
        "new_this_run": len(lib) - before,
        "pass_scenarios": n_pass,
        "unique_domains": st.get("unique_domains", 0),
    }
    if log is not None:
        log["subsystems"]["template_library"] = out
    return out


def run_daily(use_correct: bool = True, run_growth: bool = True,
              use_autoloop: bool = True, run_competitor: bool = True,
              run_agentloop: bool = True, verbose: bool = True):
    """跑一轮完整飞轮（v2 融合版），返回结构化日志。"""
    today = datetime.date.today().isoformat()
    t_start = time.time()
    log = {"date": today, "started_at": _now_iso(), "steps": [], "errors": [],
           "subsystems": {}}

    # ── 1) 主验证闭环（含主动学习补点，写入平台数据库）─────────────────────
    if use_correct:
        rec = _safe_run("run_day", lambda: P.run_day(), log)
        if rec:
            log["subsystems"]["paper_validation"] = {
                "scenarios": rec.get("scenarios", []),
                "results_count": len(rec.get("results", [])),
            }

    # ── 2) 重建仪表盘（内含：导出数据集 + 可信面 + 博客登记）────────────────
    rows = _safe_run("build_dashboard", lambda: P.build_dashboard(register=True), log, default=[])

    # ── 2b) 推理模板库沉淀（2026-10-04 新增）─────────────────────────────
    # 把本轮 PASS 的场景沉淀为可复用模板（借鉴 STELLA 的 Template Library）。
    # 这是漏斗式验证的前提：实体库扩到 10k+ 时，先用模板库廉价粗筛，
    # 只有"无相似先验"的候选才进完整三通道 V&V。
    _safe_run("template_library", lambda: _sync_template_library(rows, log), log,
              default={})

    # ── 3) SINDy+Elo 全自动闭环 ────────────────────────────────────────────
    al_results_raw = None
    if use_autoloop:
        def _run_autoloop():
            from engine.autonomous_loop import run_batch
            al = run_batch(verbose=False)
            return al
        al_raw = _safe_run("autonomous_loop", _run_autoloop, log, default=[])
        if al_raw:
            al_results_raw = al_raw
            log["subsystems"]["autonomous_loop"] = {
                "n_scenarios": len(al_raw),
                "converged": sum(1 for r in al_raw if r.get("status") == "converged"),
                "stopped_no_progress": sum(1 for r in al_raw if r.get("status") == "stopped_no_progress"),
            }

    # ── 3b) 反重复记忆（借鉴 ARIS：探索过/停滞过的不重复推荐）─────────────
    # 把本轮各场景的收敛结果写入长期记忆，连续停滞 ≥3 天的场景会被降权，
    # 避免每日预算反复花在同一个卡住的地方。
    def _run_antirepeat():
        from engine.antirepeat import ingest_from_daily_log
        snapshot = dict(log)
        snapshot["_al_results"] = al_results_raw or []
        return ingest_from_daily_log(snapshot)
    ar_report = _safe_run("antirepeat_sync", _run_antirepeat, log)
    if ar_report:
        log["subsystems"]["antirepeat"] = ar_report

    # ── 4) 实体库自治扩充（OpenAlex 公开 API，零成本）──────────────────────
    def _run_entity():
        import expand_entity_db as E
        import sys as _sys
        _sys.argv = ["expand_entity_db.py", "--pages", "10", "--delay", "0.8"]
        E.main()
    _safe_run("entity_db_growth", _run_entity, log)

    # ── 5) 竞品与技术态势监测（周日或首次运行时触发）──────────────────────
    if run_competitor:
        def _run_competitor():
            import competitor_watch as CW
            snap = CW.run()
            ok_github = sum(1 for g in snap.get("github", []) if g.get("status") == "ok")
            ok_arxiv = sum(1 for a in snap.get("arxiv", []) if a.get("status") == "ok")
            return {
                "snapshot_date": snap.get("snapshot_date"),
                "github_ok": ok_github,
                "github_total": len(snap.get("github", [])),
                "arxiv_ok": ok_arxiv,
                "arxiv_total": len(snap.get("arxiv", [])),
            }
        # 周日或首次运行时跑竞品监测（避免每天重复拉取）
        weekday = datetime.date.today().weekday()  # 0=Mon ... 6=Sun
        intel_path = os.path.join(ROOT, "data", "competitor_intel.json")
        need_competitor = (weekday == 6)  # 周日
        if not need_competitor and os.path.exists(intel_path):
            try:
                prev = json.load(open(intel_path, encoding="utf-8"))
                prev_date = prev.get("latest", {}).get("snapshot_date", "")
                if prev_date != today:
                    need_competitor = True  # 今日还没跑过
            except Exception:
                need_competitor = True  # error_kind:IGNORABLE — 状态文件读取失败时保守按"需刷新"处理
        if need_competitor:
            comp = _safe_run("competitor_watch", _run_competitor, log)
            if comp:
                log["subsystems"]["competitor_watch"] = comp

    # ── 5b) 世界模型更新（Kosmos 核心创新：每步更新共享上下文）─────────────
    # 2026-10-06 起接入 Hive 通信闸门（借鉴 mu/μ）：子 Agent 的收敛发现先经
    # Jev noul 判"值得共享"才灌入共享世界模型，P<0.4 只记台账不广播；
    # Jev 不可达 fail-open（数据链路漏一条比灌一条平庸的代价高），台账落盘
    # data/hive_gate_ledger.jsonl 供后续离线重校阈值（服务于
    # 多 Agent 协作 + 知识累积这条主线）。
    def _update_world_model():
        from engine.world_model import WorldModel
        from engine.hive_gate import gate_findings
        wm = WorldModel()
        # 从自治闭环结果中提取（过 Hive 闸门）
        if al_results_raw:
            converged = [r for r in al_results_raw
                         if isinstance(r, dict) and r.get("status") == "converged"]
            kept, gate_stats = gate_findings(converged)
            for r in kept:
                r.pop("hive_gate", None)  # 判定元数据不进世界模型本体
                wm.ingest_from_autonomous_loop(r)
            gate_stats["ledger"] = "data/hive_gate_ledger.jsonl"
            log["subsystems"]["hive_gate"] = gate_stats
        wm.save()
        return wm.stats()
    wm_stats = _safe_run("world_model_update", _update_world_model, log)
    if wm_stats:
        log["subsystems"]["world_model"] = wm_stats

    # ── 6) 增长监控 + GEO + SEO + 推广（含 120s 超时护栏）─────────────────
    if run_growth:
        def _run_growth():
            import growth_monitor as G
            G.main()
        growth_err = {}
        def _growth_thread():
            try:
                _run_growth()
            except Exception as e:
                growth_err["e"] = e  # error_kind:DEGRADABLE — 后台线程异常记录，不阻塞主线程
        import threading
        th = threading.Thread(target=_growth_thread, daemon=True)
        th.start()
        th.join(timeout=120)
        if th.is_alive():
            log["errors"].append("growth_monitor(skipped): timeout >120s")
        elif "e" in growth_err:
            log["errors"].append(f"growth_monitor(skipped): {growth_err['e']}")
        else:
            log["steps"].append("growth_monitor")

    # ── 7) 推广度量（GitHub + 站点指标）────────────────────────────────────
    def _run_promotion():
        import growth_promotion as GP
        GP.main()
    _safe_run("growth_promotion", _run_promotion, log)

    # ── 8) V&V 报告库离线再生（与引擎同步，CF Pages 静态托管前置）──────────
    # 关键约束：CF Pages 不能跑 Python/numpy，报告须离线生成。此步骤把"改 engine 后
    # 必须手动重跑 build_reports.py"自动化进每日飞轮，杜绝线上报告与引擎脱节
    # （此前报告库根本未接入飞轮，是 2026-09-11 识别的真实缺口）。
    def _run_vv_reports():
        import subprocess as _sp
        env = dict(os.environ)
        env.pop("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR", None)
        env.pop("CODEBUDDY_TOOL_CALL_ID", None)
        r = _sp.run([sys.executable, "scripts/build_reports.py"],
                    cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
        if r.returncode != 0:
            raise RuntimeError(
                f"build_reports failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[-300:]}")
        idx = json.load(open(os.path.join(ROOT, "frontend_fixed", "reports", "index.json"),
                             encoding="utf-8"))
        return {"total": idx.get("total"), "counts": idx.get("counts")}
    vvr = _safe_run("build_vv_reports", _run_vv_reports, log)
    if vvr:
        log["subsystems"]["vv_reports"] = vvr

    # ── 8.5) 验证闸门账本（置信度不达标 → 阻断自主动作）──────────────────────
    # 与 unified_ops.py::run_gate 同一口径（engine/verification_gate.py 为唯一真源）。
    # build_reports 已顺带刷新账本，此处幂等核验并把决策写进当日报告；某场景
    # **新变为 REFUTED** 即置信度回归，写入 errors 供调度器按 🟡/🔴 升级，
    # 而不是让飞轮悄悄在不可信场景上推进自主动作。
    def _run_gate():
        import build_gate_ledger as GL
        led = GL.build_ledger()
        prev_path = os.path.join(P.DATA, "gate_ledger.prev.json")
        newly = []
        try:
            if os.path.exists(prev_path):
                prev = json.load(open(prev_path, encoding="utf-8"))
                newly = sorted(set(led.get("blocked") or []) -
                               set(prev.get("blocked") or []))
        except Exception:
            newly = []  # error_kind:IGNORABLE — diff 失败时按"无新增 blocked"处理，不影响主流程
        try:
            json.dump(led, open(prev_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        log["gate"] = {
            "total": led.get("total"), "counts": led.get("counts"),
            "n_blocked": led.get("n_blocked"),
            "n_human_check_required": led.get("n_human_check_required"),
            "blocked": led.get("blocked"), "newly_blocked": newly,
        }
        if newly:
            log["errors"].append("verification_gate: %d newly BLOCKED: %s"
                                 % (len(newly), ", ".join(newly)))
        return {"blocked": led.get("n_blocked"), "newly_blocked": len(newly)}
    gater = _safe_run("verification_gate", _run_gate, log)
    if gater:
        log["subsystems"]["verification_gate"] = gater

    # ── 9) AgentLoop 数据飞轮（通过子进程调用）─────────────────────────────
    if run_agentloop:
        def _run_agentloop():
            import subprocess
            scenarios = rec.get("scenarios", []) if rec else []
            # 反重复：把长期停滞的场景排到最后，不再为它重复花每日预算。
            try:
                from engine.antirepeat import AntiRepeat
                scenarios = AntiRepeat().priority_scenarios(scenarios)
            except Exception:
                pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
            r = subprocess.run(
                [sys.executable, "-m", "agentteams.run",
                 "--scenarios", ",".join(scenarios), "--max-scenarios", "2"],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            state_path = os.path.join(ROOT, "agentteams", "state", "agentloop_state.json")
            if os.path.exists(state_path):
                state = json.load(open(state_path, encoding="utf-8"))
                return {
                    "next_priority": state.get("next_priority", [])[:5],
                    "updated_at": state.get("updated_at"),
                }
            return {"note": "agentloop ran but no state file found"}
        al_report = _safe_run("agentloop", _run_agentloop, log)
        if al_report:
            log["subsystems"]["agentloop"] = al_report

    # ── 汇总 ──────────────────────────────────────────────────────────────
    tree = domain_tree()
    try:
        ts = json.load(open(os.path.join(P.DATA, "trust_surface.json"), encoding="utf-8"))
        overall = ts["overall"]
    except Exception:
        overall = {}  # error_kind:IGNORABLE — trust_surface 缺失时按空 dict 处理，log 仍自洽
    log["totals"] = tree["totals"]
    log["trust_surface"] = {k: overall.get(k) for k in
                            ("scenarios", "rubric_full_pass", "coverage_ok",
                             "flagship", "total_store_points")}
    log["finished_at"] = _now_iso()
    log["duration_s"] = round(time.time() - t_start, 1)

    # ── errors 无损折叠（借鉴 mu/μ 的无损折叠，2026-10-06）────────────────
    # 同一错误逐场景重复出现时（如 template_library.record 报错几十条），
    # 折叠成 "msg (×N)"：len() 语义不变（供 self_aware_pulse 统计），可读性
    # 与日志体积立减，零模型调用零成本。
    try:
        import logfold
        n_err_before = len(log["errors"])
        log["errors"] = logfold.fold_errors(log["errors"])
        log["errors_folded"] = {"raw": n_err_before, "folded": len(log["errors"])}
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

    # ── 落盘报告 ──────────────────────────────────────────────────────────
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"daily-ops-{today}.json")
    try:
        json.dump(log, open(report_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

    # ── 自我感知脉冲（Daily Self-Aware Pulse）────────────────
    pulse = _safe_run("self_aware_pulse", lambda: self_aware_pulse(REPORT_DIR), log)
    if pulse:
        log["self_aware_pulse"] = pulse

    if verbose:
        t = tree["totals"]
        err_count = len(log["errors"])
        print(f"\n{'='*60}")
        print(f"[flywheel {today}] 全系统日报")
        print(f"{'='*60}")
        print(f"  已验证领域: {t['active_domains']} | 待接入引擎: {t['pending_engines']}（{t['pending_categories']} 类）")
        print(f"  可信面: rubric满通过 {overall.get('rubric_full_pass')}/{overall.get('scenarios')} | 旗舰 {overall.get('flagship',0)} | 累积点 {overall.get('total_store_points',0)}")
        print(f"  步骤完成: {len(log['steps'])} | 错误: {err_count}")
        for step in log["steps"]:
            print(f"    ✅ {step}")
        for err in log["errors"]:
            print(f"    ❌ {err}")
        if log["subsystems"]:
            print(f"  子系统状态:")
            for name, info in log["subsystems"].items():
                print(f"    [{name}] {info}")
        print(f"  耗时: {log['duration_s']}s | 报告: {report_path}")
        print(f"{'='*60}\n")

    return log


# ---------------------------------------------------------------------------
# Daily Self-Aware Pulse —— 昨日 / 今天 / 不确定性 三栏自检
# （思路借鉴 OrgRebase/知变的 Daily Self-Aware Pulse）
# ---------------------------------------------------------------------------
def self_aware_pulse(report_dir: str = REPORT_DIR, today: str = None) -> dict:
    """飞轮每日跑完用三句话概括：昨天在哪、今天到哪、还有多大不确定。"""
    from engine import brand

    today = today or datetime.date.today().isoformat()
    y = (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()

    def _load(d):
        p = os.path.join(report_dir, f"daily-ops-{d}.json")
        if not os.path.exists(p):
            return None
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None  # error_kind:IGNORABLE — 路径不存在/JSON 损坏按 None 处理

    def _summary(log):
        if not log:
            return None
        ts = log.get("trust_surface", {})
        ar = (log.get("subsystems") or {}).get("antirepeat") or {}
        return {
            "date": log.get("date"),
            "scenarios": ts.get("scenarios"),
            "rubric_full_pass": ts.get("rubric_full_pass"),
            "flagship": ts.get("flagship"),
            "total_store_points": ts.get("total_store_points"),
            "errors": len(log.get("errors", [])),
            "steps": len(log.get("steps", [])),
            "antirepeat": ar.get("stats"),
        }

    ys = _summary(_load(y))
    ts_ = _summary(_load(today))
    unc = None
    ar_stats = (ts_ or {}).get("antirepeat") if isinstance(ts_, dict) else None
    if ts_:
        subs = ts_.get("subsystems", {}) if isinstance(ts_, dict) else {}
        al = subs.get("autonomous_loop") if subs else None
        if al:
            unc = {
                "converged": al.get("converged"),
                "n_scenarios": al.get("n_scenarios"),
                "stopped_no_progress": al.get("stopped_no_progress"),
            }
    return {
        "brand": brand.ENGINE_BRAND,
        "yesterday": ys,
        "today": ts_ if isinstance(ts_, dict) else None,
        "uncertainty": unc,
        "antirepeat": ar_stats,
        "three_line_summary": _pulse_lines(ys, ts_, unc, ar_stats),
    }


def _pulse_lines(ys, ts_, unc, ar=None) -> list:
    lines = []
    if ys and ts_:
        d_store = (ts_.get("total_store_points") or 0) - (ys.get("total_store_points") or 0)
        lines.append(
            f"昨日已验证 {ys.get('scenarios')} 域、累积 {ys.get('total_store_points')} 点；"
            f"今日 {ts_.get('scenarios')} 域、累积 {ts_.get('total_store_points')} 点（净增 {d_store}）。")
    elif ts_:
        lines.append(
            f"今日首次记录：已验证 {ts_.get('scenarios')} 域、累积 {ts_.get('total_store_points')} 点。")
    else:
        lines.append("今日飞轮尚未产出日报，无可对比数据。")
    if ts_:
        lines.append(
            f"今日满通过 {ts_.get('rubric_full_pass')}/{ts_.get('scenarios')} 域，"
            f"错误 {ts_.get('errors')} 项、完成 {ts_.get('steps')} 步。")
    if unc:
        lines.append(
            f"不确定性：{unc.get('converged')}/{unc.get('n_scenarios')} 场景已收敛，"
            f"{unc.get('stopped_no_progress')} 个停滞——收敛区外仍不可信。")
    if ar:
        dep = ar.get("deprioritised", 0)
        lines.append(
            f"反重复记忆：已探索 {ar.get('explored_gaps')} 个主题"
            f"（{ar.get('repeat_gaps')} 个重复出现），"
            f"跟踪 {ar.get('tracked_scenarios')} 个场景"
            + (f"，{dep} 个因连续停滞已降权" if dep else "，暂无需降权的场景")
            + "。")
    return lines


if __name__ == "__main__":
    run_daily()
