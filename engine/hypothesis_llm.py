"""LLM 假设层 —— 真实调用远端 LLM 生成候选机理（OpenAI 兼容网关）。

诚实定位：LLM **不负责验证**（那是 SINDy + GP + Elo 的事，零幻觉），只负责
「扩写候选假设空间」——给出若干**可检验**的符号律候选，交由下游 Elo 锦标赛 +
GP 对等验证淘汰。这是 Co-Scientist「生成候选」环节的诚实分工版本。

三段式（借鉴 OptED / SVP，2026-10-15 补齐）
--------------------------------------------
「LLM 生成 → 工具调用 → 规则约束」——避免让 LLM 单点决策：

1. **生成段** (hypothesize)：LLM 出候选符号律（保持原语义，零幻觉边界）。
2. **工具调用段** (tool_calls)：拿到候选后**用**真实工具校验（SINDy / GP /
   Elo），LLM 只做**候选提议**，判定权在工具。
3. **规则约束段** (rule_check / stage_gate)：确定性判据过滤，fail-closed。

网关：OpenAI 兼容推理后端（地址由环境变量 ATEX_BASE_URL 注入，默认 deepseek-chat）。
凭证：环境变量 ATEX_API_KEY（或 OPENAI_API_KEY）。
无凭证 / 不可达时**优雅降级**返回 None，闭环仍由 SINDy 主导（离线可用）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

DEFAULT_BASE = "http://localhost:8420/v1"  # 占位，真实地址由 ATEX_BASE_URL 注入
DEFAULT_MODEL = "deepseek-chat"


class LLMHypothesizer:
    def __init__(self, base_url=None, model=None, api_key=None):
        self.base_url = (base_url or os.environ.get("ATEX_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("ATEX_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ATEX_API_KEY") or os.environ.get("OPENAI_API_KEY")

    def hypothesize(self, context: str, n: int = 3):
        """给定观测背景 context，返回 n 条候选符号律（字符串列表）。

        无凭证或调用失败 → 返回 None（调用方回退到 SINDy 主导）。
        """
        if not self.api_key:
            return None
        prompt = (
            "You are a physical-modeling assistant. Given the experimental context below, "
            f"propose {n} candidate symbolic laws y = f(x) that could explain it. "
            "Each must be a concise, testable mathematical expression using x0, x1, ... "
            "and standard functions (sin, cos, exp, log, +, -, *, /, ^). "
            "Return strictly a JSON array of strings, no prose.\n\n"
            f"Context:\n{context}"
        )
        try:
            import urllib.request
            import ssl

            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 400,
            }
            hdr = {"Content-Type": "application/json"}
            if self.api_key:
                hdr["Authorization"] = f"Bearer {self.api_key}"
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(body).encode(),
                headers=hdr,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                resp = json.loads(r.read().decode())
            content = resp["choices"][0]["message"]["content"]
            # 提取 JSON 数组
            start = content.find("[")
            end = content.rfind("]")
            if start == -1 or end == -1:
                return None
            arr = json.loads(content[start:end + 1])
            return [str(a) for a in arr][:n] if isinstance(arr, list) else None
        except Exception:
            return None

    def context_from(self, X, y, domain_key=""):
        """把一次观测集压成 LLM 可读的简短背景串。"""
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        lines = [f"Domain: {domain_key}", f"n_samples={len(y)}", "samples (x -> y):"]
        k = min(len(y), 8)
        idx = np.linspace(0, len(y) - 1, k).astype(int)
        for i in idx:
            xs = ", ".join(f"{v:.3g}" for v in X[i])
            lines.append(f"  [{xs}] -> {y[i]:.4g}")
        return "\n".join(lines)


# ===========================================================================
# 三段式补充：stage_gate / tool_calls / rule_check（2026-10-15 OptED 借鉴）
# ===========================================================================
# 设计要点：LLM 只做候选提议；**判定权**永远在工具与规则手上。
# 三段任一 fail → 整条假设拒收，不假装有验证。

#: 阶段：HYPOTHESIZE / TOOL_CALL / RULE_CHECK / ADMISSION
#: 每阶段必须产出可判定的布尔结论，禁止「模糊通过」。
PHASES: Tuple[str, ...] = ("HYPOTHESIZE", "TOOL_CALL", "RULE_CHECK", "ADMISSION")


@dataclass
class StageVerdict:
    """一个阶段的判定结果。"""
    phase: str
    passed: bool
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "passed": bool(self.passed),
            "reason": self.reason,
            "evidence": self.evidence,
        }


# --- 规则约束段：确定性判据 ----------------------------------------------

#: 允许出现的符号；LLM 输出的假设若含未列符号 → 规则段直接拒收。
_ALLOWED_CHARS = set("x0123456789 y=+-*/().^, ")
#: 允许出现的函数名（前缀）；其它 → 拒收。
_ALLOWED_FUNCS: Tuple[str, ...] = (
    "sin", "cos", "tan", "exp", "log", "abs", "sqrt", "min", "max",
)
#: 假设字符串长度上限（过长通常含未闭合括号 / 多余修饰）
MAX_LEN = 256


def _rule_eval(expr: str) -> Tuple[bool, str]:
    """规则段的静态检查。返回 (是否通过, 失败原因)。"""
    if not isinstance(expr, str) or not expr.strip():
        return False, "empty_expr"
    if len(expr) > MAX_LEN:
        return False, f"too_long({len(expr)}>{MAX_LEN})"
    # 允许 x0..x9, 数字, 空格, 基本运算符
    stripped = expr.replace(" ", "")
    for ch in stripped:
        if ch not in _ALLOWED_CHARS and not ch.isalnum():
            return False, f"forbidden_char({ch!r})"
    # 检查函数名前缀
    lowered = expr.lower()
    for tok in stripped.split("("):
        # 找括号前的标识符
        prefix = ""
        for ch in reversed(tok):
            if ch.isalnum() or ch == "_":
                prefix = ch + prefix
            else:
                break
        if prefix:
            if not prefix[0].isalpha() and prefix not in ("x0","x1","x2","x3","x4","x5","x6","x7","x8","x9"):
                return False, f"unknown_token({prefix!r})"
            if prefix not in _ALLOWED_FUNCS and not prefix.startswith("x") and prefix not in ("y",):
                return False, f"unknown_function({prefix!r})"
    # 括号配对
    if stripped.count("(") != stripped.count(")"):
        return False, "unbalanced_parens"
    return True, "ok"


def rule_check(hypotheses: Sequence[str]) -> Tuple[List[str], List[StageVerdict]]:
    """规则约束段：把 LLM 产出的候选用确定性判据过滤一遍。

    Args:
        hypotheses: LLM 返回的符号律字符串列表

    Returns:
        (通过的候选, 每个候选的 StageVerdict 列表)
    """
    kept: List[str] = []
    verdicts: List[StageVerdict] = []
    for h in hypotheses:
        ok, why = _rule_eval(h)
        verdicts.append(StageVerdict("RULE_CHECK", ok, why))
        if ok:
            kept.append(h)
    return kept, verdicts


# --- 工具调用段：让工具做判定 --------------------------------------------


def tool_calls(
    hypotheses: Sequence[str],
    X: np.ndarray,
    y: np.ndarray,
    evaluator: Optional[Callable[[str], float]] = None,
) -> Tuple[List[Tuple[str, float]], List[StageVerdict]]:
    """工具调用段：对候选符号律用**真实工具**打分（默认 R² 拟合度）。

    如果提供了 evaluator 就用它，否则用最小二乘/直接代入尝试评估（不评估会
    导致「LLM 提议 → 无评判」= 幻觉放大）。

    Args:
        hypotheses: 已通过 rule_check 的符号律
        X, y:       观测数据
        evaluator:  可选的符号律 → R² 评估函数（默认走 _safe_eval）

    Returns:
        ((expr, r2) 列表, 每候选的 StageVerdict)
    """
    X = np.atleast_2d(np.asarray(X, float))
    y = np.asarray(y, float).ravel()
    eval_fn = evaluator or _safe_eval_r2
    scored: List[Tuple[str, float]] = []
    verdicts: List[StageVerdict] = []
    for h in hypotheses:
        try:
            r2 = eval_fn(h, X, y)
            ok = np.isfinite(r2) and r2 > -1.0  # R² 允许负值，但不能 -inf
            verdicts.append(StageVerdict("TOOL_CALL", ok, f"r2={r2:.4f}"))
            scored.append((h, float(r2)))
        except Exception as e:  # 工具段 fail-closed
            verdicts.append(StageVerdict("TOOL_CALL", False, f"eval_error:{type(e).__name__}"))
    return scored, verdicts


def _safe_eval_r2(expr: str, X: np.ndarray, y: np.ndarray) -> float:
    """安全求值：把 y = <expr> 里的 x0..xn 替成实际变量、算预测 R²。

    **绝不** eval 用户字符串：仅允许 x0..x9 与白名单函数，AST 白名单遍历。
    """
    import ast
    # 剥掉 "y =" 前缀
    s = expr.strip()
    if s.lower().startswith("y="):
        s = s[2:]
    elif s.lower().startswith("y ="):
        s = s[3:]
    code = s.strip().replace("^", "**")
    if not code:
        return -float("inf")
    try:
        tree = ast.parse(f"({code})", mode="eval")
        # 白名单校验
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if not (node.id.startswith("x") and node.id[1:].isdigit()
                        and 0 <= int(node.id[1:]) < X.shape[1]):
                    raise ValueError(f"unknown name: {node.id}")
            elif isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise ValueError("only Name calls allowed")
                if node.func.id not in ("sin","cos","exp","log","sqrt","abs","min","max"):
                    raise ValueError(f"unknown function: {node.func.id}")
            elif isinstance(node, (ast.BinOp, ast.Add, ast.Sub, ast.Mult,
                                    ast.Div, ast.Pow, ast.USub, ast.UAdd,
                                    ast.Load, ast.Constant, ast.Expression)):
                pass
            elif isinstance(node, (ast.Num,)):  # 兼容旧 numpy
                pass
            else:
                raise ValueError(f"forbidden node: {type(node).__name__}")
        # 构造求值环境：数组级 numpy 函数
        env = {f"x{i}": X[:, i] for i in range(X.shape[1])}
        env["sin"] = np.sin; env["cos"] = np.cos; env["exp"] = np.exp
        env["log"] = np.log; env["sqrt"] = np.sqrt; env["abs"] = np.abs
        env["min"] = np.minimum; env["max"] = np.maximum
        pred = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, env)
        pred = np.asarray(pred, float)
        if pred.shape != y.shape:
            raise ValueError(f"shape mismatch: pred={pred.shape} y={y.shape}")
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        if ss_tot == 0:
            return 0.0
        return 1.0 - ss_res / ss_tot
    except Exception as _e:
        # DEBUG: 记录一次异常原因，避免"静默失败"陷阱
        import sys
        if os.environ.get("HYP_LLM_DEBUG"):
            print(f"[_safe_eval_r2] expr={expr!r} err={type(_e).__name__}: {_e}", file=sys.stderr)
        return -float("inf")


def _to_scalar(args):
    """把 list of arrays 转成 list of scalars（Python builtin 用）。"""
    return [a[0] if isinstance(a, np.ndarray) and a.size == 1 else a for a in args]


def float_or_array(x):
    return x


# --- 阶段门禁：串起三段，fail-closed ------------------------------------


def stage_gate(
    hypothesizer: LLMHypothesizer,
    X: np.ndarray,
    y: np.ndarray,
    domain_key: str = "",
    n_candidates: int = 3,
    min_r2: float = 0.6,
    evaluator: Optional[Callable[[str], float]] = None,
) -> Dict[str, Any]:
    """三段式门禁：hypothesize → rule_check → tool_calls → admission。

    Args:
        hypothesizer:   LLM 假设生成器（无凭证时降级为空候选）
        X, y:           观测数据
        domain_key:     领域标识（进 context）
        n_candidates:   请求的候选数
        min_r2:         工具段通过 R² 门槛（默认 0.6，保守）
        evaluator:      自定义符号律评分器（默认 R²）

    Returns:
        {
          "phases": [StageVerdict, ...],
          "admitted": [...],           # 三段全过的候选
          "passed_admission": bool,    # 是否有候选过 admission
          "llm_available": bool,       # 是否真调用了 LLM
        }
    """
    context = hypothesizer.context_from(X, y, domain_key)
    raw = hypothesizer.hypothesize(context, n=n_candidates)
    llm_available = raw is not None

    phases: List[StageVerdict] = []
    if raw is None:
        phases.append(StageVerdict("HYPOTHESIZE", False, "llm_unavailable"))
        return {
            "phases": phases,
            "admitted": [],
            "passed_admission": False,
            "llm_available": False,
        }

    phases.append(StageVerdict("HYPOTHESIZE", True, f"n={len(raw)}"))

    kept, rule_verdicts = rule_check(raw)
    phases.extend(rule_verdicts)
    if not kept:
        return {
            "phases": phases,
            "admitted": [],
            "passed_admission": False,
            "llm_available": True,
        }

    scored, tool_verdicts = tool_calls(kept, X, y, evaluator=evaluator)
    phases.extend(tool_verdicts)

    admitted = [h for (h, r2) in scored if np.isfinite(r2) and r2 >= min_r2]
    for h in admitted:
        phases.append(StageVerdict("ADMISSION", True, "r2_ok"))

    return {
        "phases": phases,
        "admitted": admitted,
        "passed_admission": len(admitted) > 0,
        "llm_available": True,
    }


__all__ = [
    "LLMHypothesizer", "DEFAULT_BASE", "DEFAULT_MODEL",
    "PHASES", "StageVerdict",
    "rule_check", "tool_calls", "stage_gate",
]
