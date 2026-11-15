"""Tensor ↔ (sample_id, λ) 密码学绑定（CycloneDX ML-BOM 风格，B6）。

背景（arXiv:2608.20439, 2026-08）
----------------------------------
综述指出：ML 系统常见的「张量重连攻击」是——把两个不同参数配置（λ）下训练/
预测出来的同一批样本的 tensor 直接串起来跑下游。攻击者通过「错位张量拼合」
骗过验证器，因为验证器只看「样本 id 匹配」而没看「张量是否真的出自该样本
在该参数配置下的计算」。

正确防御
--------
把每个 tensor 在生成时立刻和 (sample_id, λ_fingerprint) 绑定成不可伪造的
签名对。任何下游访问必须携带正确的 (sample_id, λ)，否则拒绝。这就像给
「张量」加一个内容寻址的身份证，CycloneDX 4.0 起在 ML-BOM 组件里正式
承认这个字段。

本模块实现什么
--------------
1. `TensorBinding`：把 tensor 内容 hash 到 (sample_id, λ) 组合，产出
   canonical 指纹；后续可验证「这个 tensor 是不是真从该 (sample_id, λ)
   产出的」。
2. 支持 3 种边界条件声明（uniform / periodic / reflective），因为
   边界条件决定 tensor 空间对称性，直接影响 λ 指纹。
3. 提供 4 个「合规预检」函数：uniform 性、边界一致、网格符合、对称性。

不做
----
- 不做真实密码学（HMAC 签名需要私钥，本模块只算 sha256 内容指纹）
- 不做持久化存储（这是设计层的绑定规则，不是运行时库）
- 不替换已有的 provenance_guard——那管「谁调用了什么模型」，这里管
  「张量是不是真从该参数下算出来的」

诚实边界
--------
sha256 内容指纹能防**无意拼接**，但对抗性攻击者可以伪造 tensor 内容再
签名。真正的对抗防御需要 HMAC+私钥，本模块留接口但默认不启用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

_SCHEMA = "svp.tensor-binding.v1"


class BindingError(ValueError):
    """任何绑定不匹配 / 边界违反 / 校验失败时抛出。"""


#: 支持的边界条件（决定张量在维度端点的连接方式）
BOUNDING_TYPES: Tuple[str, ...] = ("uniform", "periodic", "reflective")


@dataclass(frozen=True)
class TensorBinding:
    """一个 (sample_id, λ) 组合上的张量绑定。

    Attributes
    ----------
    sample_id : str
        样本的唯一标识。可以是数据行 index 字符串、实验记录 id、或任何
        人类可读且唯一的 key。
    lam : dict
        参数配置的 canonical 形式。必须是 JSON-able 的 dict。
        例：{"method": "gp", "kernel": "rbf", "length_scale": 0.5, "alpha": 1e-8}
    shape : tuple
        被绑定张量的 shape。用于 shape mismatch 校验。
    bounding : str
        边界条件，`BOUNDING_TYPES` 之一。影响对称性判据。
    """

    sample_id: str
    lam: Dict[str, Any]
    shape: Tuple[int, ...]
    bounding: str = "uniform"

    def __post_init__(self):
        if self.bounding not in BOUNDING_TYPES:
            raise BindingError(
                f"bounding must be one of {BOUNDING_TYPES}, got {self.bounding!r}"
            )
        if not self.sample_id or not isinstance(self.sample_id, str):
            raise BindingError("sample_id must be a non-empty string")
        if not isinstance(self.lam, dict):
            raise BindingError("lam must be a dict")
        if not isinstance(self.shape, tuple) or not all(isinstance(d, int) for d in self.shape):
            raise BindingError("shape must be tuple[int,...]")

    def lam_fingerprint(self) -> str:
        """λ 的 canonical hash（顺序无关）。"""
        canonical = json.dumps(self.lam, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(b"LAM:" + canonical).hexdigest()[:32]

    def bind(self, tensor: np.ndarray) -> "BoundTensor":
        """把张量内容绑定到本 (sample_id, λ)，返回签名结果。

        shape mismatch 会抛 BindingError。
        """
        if tensor.shape != self.shape:
            raise BindingError(
                f"shape mismatch: expected {self.shape}, got {tensor.shape}"
            )
        content_bytes = np.ascontiguousarray(tensor, dtype=np.float64).tobytes()
        content_hash = hashlib.sha256(b"CONTENT:" + content_bytes).hexdigest()
        combo_hash = hashlib.sha256(
            (self.sample_id + "|" + self.lam_fingerprint() + "|" + self.bounding).encode("utf-8")
        ).hexdigest()[:32]
        return BoundTensor(
            sample_id=self.sample_id,
            lam=self.lam,
            shape=self.shape,
            bounding=self.bounding,
            content_hash=content_hash,
            combo_hash=combo_hash,
        )

    def verify(self, bound: "BoundTensor", tensor: Optional[np.ndarray] = None) -> bool:
        """验证一个 BoundTensor 是否真从本绑定产生。

        如果传入 tensor，也会额外核对内容 hash。
        """
        if bound.sample_id != self.sample_id:
            return False
        if bound.lam != self.lam:
            return False
        if bound.shape != self.shape:
            return False
        if bound.bounding != self.bounding:
            return False
        if bound.combo_hash != self._combo_hash():
            return False
        if tensor is not None:
            content_bytes = np.ascontiguousarray(tensor, dtype=np.float64).tobytes()
            content_hash = hashlib.sha256(b"CONTENT:" + content_bytes).hexdigest()
            return content_hash == bound.content_hash
        return True

    def _combo_hash(self) -> str:
        return hashlib.sha256(
            (self.sample_id + "|" + self.lam_fingerprint() + "|" + self.bounding).encode("utf-8")
        ).hexdigest()[:32]


@dataclass(frozen=True)
class BoundTensor:
    """绑定结果的不可变视图（相当于 ML-BOM 的一条 attestation 记录）。"""

    sample_id: str
    lam: Dict[str, Any]
    shape: Tuple[int, ...]
    bounding: str
    content_hash: str
    combo_hash: str

    def to_bom_entry(self) -> Dict[str, Any]:
        """序列化成 CycloneDX ML-BOM 风格的 JSON 条目。"""
        return {
            "_schema": _SCHEMA,
            "type": "machine-learning-model-attestation",
            "sample_id": self.sample_id,
            "parameter_fingerprint": {
                "lam": dict(self.lam),
                "fingerprint": self._lam_fingerprint(),
            },
            "tensor": {
                "shape": list(self.shape),
                "bounding": self.bounding,
                "content_sha256": self.content_hash,
            },
            "combo_sha256": self.combo_hash,
        }

    def _lam_fingerprint(self) -> str:
        canonical = json.dumps(self.lam, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(b"LAM:" + canonical).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 合规预检（4 项）
# ---------------------------------------------------------------------------

def verify_uniformity(values: Sequence[float], rel_tol: float = 1e-6) -> bool:
    """检查采样点是否在均匀网格上。"""
    v = np.asarray(values, dtype=float)
    if v.size < 2:
        return True
    diffs = np.diff(v)
    if np.any(diffs <= 0):
        return False  # 非递增
    step = diffs.mean()
    return bool(np.max(np.abs(diffs - step)) <= rel_tol * step)


def verify_boundary_agreement(
    X: np.ndarray, y: np.ndarray, tol: float = 1e-6, wrap_dim: int = 0
) -> bool:
    """周期边界：端点值应一致（x=lo 与 x=hi 同 y）。"""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim < 2 or X.shape[0] < 2:
        return True
    col = X[:, wrap_dim]
    lo, hi = col.min(), col.max()
    lo_mask = np.isclose(col, lo, atol=tol)
    hi_mask = np.isclose(col, hi, atol=tol)
    if not lo_mask.any() or not hi_mask.any():
        return True  # 没有端点样本，判据不适用
    y_lo = y[lo_mask].mean()
    y_hi = y[hi_mask].mean()
    scale = float(np.std(y)) or 1.0
    return bool(abs(y_lo - y_hi) <= tol * scale * 100)


def verify_grid_conformity(
    X: np.ndarray, ref_grid: Sequence[Sequence[float]]
) -> bool:
    """每个坐标的取值必须在给定参考网格上（数值允许微小浮动）。"""
    X = np.asarray(X, dtype=float)
    ref = [np.asarray(g, dtype=float) for g in ref_grid]
    if X.ndim != 2 or X.shape[1] != len(ref):
        return False
    for j, g in enumerate(ref):
        col = X[:, j]
        exact = bool(np.all(np.isin(col, g)))
        if exact:
            continue
        # 允许浮动：每个 col 值必须靠近某个 g 值
        dist = np.min(np.abs(col[:, None] - g[None, :]), axis=1)
        tol = max(1e-9, 1e-9 * float(np.max(np.abs(g))) if g.size else 1e-9)
        if not bool(np.all(dist <= tol)):
            return False
    return True


def verify_symmetry(
    values: Sequence[float], kind: str = "reflective"
) -> bool:
    """
    检查采样点序列是否满足边界对称性。
    - reflective: 值序列应关于中点对称（左右两侧镜像）
    - periodic: 与 uniformity 一致即可（首尾在周期意义上是同一个点）
    - uniform: 不做对称性要求
    """
    v = np.asarray(values, dtype=float)
    if v.size < 3:
        return True
    if kind == "uniform":
        return True
    if kind == "periodic":
        return verify_uniformity(v)
    if kind == "reflective":
        return bool(np.allclose(v, v[::-1], rtol=1e-9, atol=1e-12))
    raise BindingError(f"unknown symmetry kind: {kind}")


# ---------------------------------------------------------------------------
# 便捷批量接口
# ---------------------------------------------------------------------------

def bind_batch(
    sample_ids: Sequence[str],
    lam: Dict[str, Any],
    tensors: Sequence[np.ndarray],
    bounding: str = "uniform",
) -> List[BoundTensor]:
    """批量绑定。所有张量共享同一 λ，用于同一 batch 内的 tensor。"""
    out: List[BoundTensor] = []
    for sid, t in zip(sample_ids, tensors):
        b = TensorBinding(sample_id=sid, lam=lam, shape=t.shape, bounding=bounding)
        out.append(b.bind(t))
    return out


__all__ = [
    "TensorBinding",
    "BoundTensor",
    "BindingError",
    "BOUNDING_TYPES",
    "bind_batch",
    "verify_uniformity",
    "verify_boundary_agreement",
    "verify_grid_conformity",
    "verify_symmetry",
]
