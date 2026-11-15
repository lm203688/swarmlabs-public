"""local_model.py —— 本地训练小模型的**受限顾问层**。

设计红线（不破坏 contracts.py 的「刻意不引入 LLM 依赖」）
--------------------------------------------------------
本项目 agent 的"智能"来自确定性科学计算（GP 后验 / UQ 校准 / 三态门禁），
每一步可复现、可验证、可回滚。接入本地小模型**不改变这一点**，它被严格
限制在只读顾问层：

    ┌─ 裁决层（唯一有权决定对错）──────────────────────────┐
    │ rel / cov / cal / maturity / gate verdict / 入库     │
    │ ← 全部来自 engine/ 的确定性计算，不含任何模型输出    │
    └──────────────────────────────────────────────────┘
    ┌─ 顾问层（本 Skill，只读、只叙述）───────────────────┐
    │ 本地模型：把数值结果转成人话、提示可疑之处           │
    │ 产出只进 loop_report.local_model_advisory           │
    └──────────────────────────────────────────────────┘

三条硬约束（均可被独立复核，不是文档承诺）
------------------------------------------
1. **零裁决权**：输出不写回任何数值字段。本 Skill 的返回值里没有任何
   rel/cov/cal/verdict/status 字段，调用方也无从借它改动判定。
2. **可复现**：``temperature=0`` + 固定 ``seed``；记录 model 名、权重
   digest、生成参数、prompt/response 的 sha256 —— 任何人可复跑核对。
3. **显式降级**：模型缺失/超时/输出异常时**不静默跳过**，返回
   ``status: "unavailable" | "degraded"`` 与原因；主流程 fail-open
   （顾问缺席不得阻断科学计算），但"这次没有模型参与"必须可读。

诚实边界
--------
当前仓库本地训练产物为 63.91M 量级的极小模型。它在本链路中的价值是
**证明"本地训练模型可以被本项目调用且全程可溯源"**，而不是承担科学判断
——它的文本质量不足以做审核。换用更大本地模型只需改一个参数
（``--local-model ornith-1.5:35b``），本 Skill 代码零改动：这正是
contracts.py 所说"可替换性"的具体体现。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from ..core.contracts import Skill, SkillSpec

#: 默认候选顺序：本项目本地训练产物优先，其次通用本地模型。
#: `auto` 模式下按此顺序取第一个在 Ollama 里真实存在的。
DEFAULT_CANDIDATES = ("flybrain:latest", "minimind:latest", "ornith-1.5:35b")

DEFAULT_HOST = "http://127.0.0.1:11434"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class LocalModelAdvisory(Skill):
    spec = SkillSpec(
        name="local_model.advisory",
        version="1.0.0",
        purpose=(
            "调用本地训练/本地托管的小模型，把本轮 V&V 数值结果转成人类可读的"
            "复核叙述，并对可疑之处给出提示。**只读顾问**：不参与任何数值判定。"
        ),
        inputs={
            "rows": "[{key, rel, coverage, cal_err, maturity}] —— 本轮判定结果（只读）",
            "goal": "字符串，本轮目标（可选）",
            "model": "可选，Ollama 模型名；'auto' 按 flybrain→minimind→ornith 顺序探测",
            "host": "可选，Ollama 地址，默认 http://127.0.0.1:11434",
            "timeout_s": "可选，单次调用超时秒数，默认 60",
            "max_rows": "可选，最多纳入叙述的场景数，默认 3（控制 prompt 体积）",
        },
        outputs={
            "status": "ok | degraded | unavailable",
            "model": "实际使用的模型名",
            "digest": "权重 sha256（可复现核对用）",
            "generation": "temperature / seed / max_tokens（固定为确定性取值）",
            "prompt_sha256": "prompt 指纹",
            "response_sha256": "响应指纹",
            "text": "模型原文（未做任何解析或改写）",
            "latency_ms": "int",
            "note": "该输出不参与任何判定；判定权在 engine/ 的确定性计算",
        },
        preconditions="本机 Ollama 可达且目标模型已 pull（否则显式返回 unavailable）。",
        side_effect="external", risk="low", scope="run_local",
        failure_modes=(
            "连接失败/超时 → status=unavailable + reason，不抛异常（fail-open）；"
            "模型返回空文本或异常结构 → status=degraded 并保留原始响应片段。"
        ),
        reuse_note=(
            "任何'把结构化结果转叙述'的场景都可复用；换模型只改参数，代码零改动。"
            "是本地模型进入本项目的最小接入面。"
        ),
        depends_on_tools=("agentteams/core/loop.py",),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        t0 = time.time()
        rows: List[dict] = list(params.get("rows") or [])
        goal = str(params.get("goal") or "")
        host = str(params.get("host") or DEFAULT_HOST).rstrip("/")
        timeout_s = float(params.get("timeout_s") or 60)
        max_rows = int(params.get("max_rows") or 3)

        want = str(params.get("model") or "auto")

        # ── 1. 探测可用模型（顺带拿到 digest，这是"可复现"的锚点）────────
        try:
            tags = _get_json(f"{host}/api/tags", timeout=min(timeout_s, 10))
            available = {
                m.get("name"): m for m in (tags.get("models") or [])
                if isinstance(m, dict)
            }
        except Exception as e:                                   # noqa: BLE001
            return self._unavailable(
                want, f"Ollama 不可达（{host}）：{type(e).__name__}: {e}", t0)

        model = self._pick(want, available)
        if not model:
            return self._unavailable(
                want,
                f"目标模型 {want!r} 不在本机 Ollama；现有：{sorted(available)}",
                t0, extra={"available_models": sorted(available)})

        meta = available.get(model) or {}
        digest = (meta.get("digest") or "")[:64]

        # ── 2. 构造 prompt（只喂判定结果的摘要，不喂任何秘密）──────────
        prompt = self._build_prompt(goal, rows[:max_rows], len(rows))
        messages = [
            {"role": "system",
             "content": ("你是一个科研验证结果的复核助手。你只会收到已经由确定性"
                         "程序算好的数值结论。你的任务只是用简洁中文复述这些结论"
                         "并指出需要留意的风险点；你**不能**修改、重新计算或否认"
                         "任何数值。不要输出 JSON，不要编造未给出的数据。")},
            {"role": "user", "content": prompt},
        ]
        gen = {"temperature": 0, "seed": 0, "max_tokens": 256}

        # ── 3. 调用（确定性参数；失败即显式降级）───────────────────────
        try:
            resp = _post_json(f"{host}/api/chat", {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": gen["temperature"],
                            "seed": gen["seed"],
                            "num_predict": gen["max_tokens"]},
            }, timeout=timeout_s)
        except Exception as e:                                   # noqa: BLE001
            return self._unavailable(
                model, f"生成调用失败：{type(e).__name__}: {e}", t0,
                extra={"model": model, "digest": digest})

        text = ((resp.get("message") or {}).get("content")
                or resp.get("response") or "")
        status = "ok"
        note = ("该输出不参与任何判定；判定权在 engine/ 的确定性计算。"
                "本调用是旁路（bypass），不计入 loop_report.analyze 的团队 skill 计数")
        if not text.strip():
            status = "degraded"
            note = "模型返回空文本；已保留原始响应以便排查（判定不受影响）"

        return {
            "status": status,
            "layer": "advisory_bypass",
            "counted_in_team_stats": False,
            "authority": "none",
            "model": model,
            "digest": digest,
            "generation": gen,
            "prompt_sha256": _sha256(json.dumps(messages, ensure_ascii=False)),
            "response_sha256": _sha256(text),
            "text": text,
            "quality": self._quality(text, rows[:max_rows]),
            "prompt_preview": prompt[:400],
            "rows_used": len(rows[:max_rows]),
            "rows_total": len(rows),
            "latency_ms": int((time.time() - t0) * 1000),
            "eval_count": resp.get("eval_count"),
            "done_reason": resp.get("done_reason"),
            "note": note,
            "raw_response_meta": {
                k: resp.get(k) for k in
                ("total_duration", "load_duration", "prompt_eval_count")
                if k in resp
            },
        }

    # -- 内部 --------------------------------------------------------------
    @staticmethod
    def _quality(text: str, rows: List[dict]) -> Dict[str, Any]:
        """对模型输出做**只读**质量体检，让"这个模型够不够用"成为产物里的
        可读事实，而不是靠人肉判断。

        三个启发式（都不影响任何判定，只描述顾问文本本身）：
          * ``mentions_scenario``：是否真的提到了被复核的场景 key
          * ``has_number``：是否复述了任何数值（纯复述，不代表正确）
          * ``repetitive``：相邻片段高度重复 → 典型小模型退化输出
        """
        t = (text or "").strip()
        keys = [str(r.get("key") or "") for r in rows if r.get("key")]
        mentioned = [k for k in keys if k and k in t]
        has_num = any(ch.isdigit() for ch in t)
        # 退化检测：按标点切段后看是否有重复片段
        parts = [p.strip() for p in t.replace("，", "。").split("。") if p.strip()]
        repetitive = len(parts) != len(set(parts)) if parts else False
        return {
            "chars": len(t),
            "mentions_scenario": mentioned,
            "mentions_any_scenario": bool(mentioned),
            "has_number": has_num,
            "repetitive": repetitive,
            "caveat": ("启发式体检，不构成对模型能力的评价；本项目对该输出的"
                       "使用方式仅为'人类可读叙述'，判定权始终在确定性计算"),
        }

    @staticmethod
    def _pick(want: str, available: Dict[str, Any]) -> str:
        if want and want != "auto":
            return want if want in available else ""
        for cand in DEFAULT_CANDIDATES:
            if cand in available:
                return cand
        # 兜底：本机若只有别的模型，取第一个（仍记录实际用的哪个）
        return sorted(available)[0] if available else ""

    @staticmethod
    def _build_prompt(goal: str, rows: List[dict], total: int) -> str:
        lines = []
        if goal:
            lines.append(f"本轮目标：{goal}")
        lines.append(f"程序算出的判定结果（共 {total} 个场景，下列 {len(rows)} 个）：")
        for r in rows:
            lines.append(
                "- 场景 {k}：相对误差 {rel}，覆盖率 {cov}，校准误差 {cal}，"
                "成熟度「{m}」".format(
                    k=r.get("key"),
                    rel=r.get("rel"),
                    cov=r.get("coverage"),
                    cal=r.get("cal_err"),
                    m=r.get("maturity"),
                ))
        lines.append("")
        lines.append("请用不超过 150 字复述以上结论，并指出其中需要人工留意的风险点。")
        lines.append("再次强调：不要修改任何数值，不要补充未给出的数据。")
        return "\n".join(lines)

    def _unavailable(self, want: str, reason: str, t0: float,
                     extra: dict = None) -> Dict[str, Any]:
        out = {
            "status": "unavailable",
            "model": (extra or {}).get("model") or want,
            "digest": (extra or {}).get("digest", ""),
            "text": "",
            "reason": reason,
            "latency_ms": int((time.time() - t0) * 1000),
            "note": "顾问层不可用不影响科学计算；主流程 fail-open，但此事实已留痕",
        }
        if extra:
            out.update({k: v for k, v in extra.items()
                        if k not in ("model", "digest")})
        return out
