# -*- coding: utf-8 -*-
"""
cross_attention_bridge.py —— 双序列交互桥梁（Cross-Attention Bridge）

用途（SwarmLabs 多域代理模型组件）：
  交叉注意力中，查询 Q 来自**目标序列**（解码侧），键 K 与值 V 来自**源序列**（编码侧）：
      CrossAttn(Q, K, V) = softmax(Q K^T / sqrt(d_k)) V
  注意力矩阵为 n_t × n_s 矩形，天然适合长度/模态/角色不同的两路信息融合。

在 SwarmLabs 中可作为：
  - 跨域代理模型：源 = PDE 场表征，目标 = 参数/边界条件序列 → 实现跨域知识迁移；
  - 多保真度桥接：源 = 粗糙仿真，目标 = 高精度实验 → 廉价数据引导昂贵预测。

数学基石（通用近似定理 UAT，Cybenko 1989 / Hornik 1991）：
  单隐层前馈网络（连续非多项式激活）可在紧集上任意精度逼近任何连续函数。
  它保证「代理模型具备表达物理规律的能力」（存在性），但**不保证**宽度可控、
  可训练、能泛化——因此 SwarmLabs 用 PIML 物理先验 + 主动学习飞轮补足这三点。
  UAT 是「为什么 NN 能逼近物理场解」的依据；交叉注意力是「如何跨域融合」的架构杠杆。

实现：
  - CrossAttentionBridge：纯 NumPy 实现（可随处运行，无第三方依赖）；
  - TorchCrossAttentionBridge：PyTorch 实现（GPU 训练用），按需导入（无 torch 时跳过）。

运行自测：
  python cross_attention_bridge.py
"""
import numpy as np


class CrossAttentionBridge:
    """纯 NumPy 多头交叉注意力。可在任意环境运行，便于 CI/自测。"""

    def __init__(self, d_model, n_heads=4, rng_seed=0):
        assert d_model % n_heads == 0, "d_model 必须能被 n_heads 整除"
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        rng = np.random.default_rng(rng_seed)
        scale = (2.0 / (d_model + n_heads * self.d_k)) ** 0.5
        # 随机初始化（能力演示用；真实训练会用反向传播优化这些权重）
        self.W_Q = rng.normal(0, scale, (d_model, d_model))
        self.W_K = rng.normal(0, scale, (d_model, d_model))
        self.W_V = rng.normal(0, scale, (d_model, d_model))
        self.W_O = rng.normal(0, scale, (d_model, d_model))

    def _split_heads(self, x):
        # (B, n, d_model) -> (B, h, n, d_k)
        B, n, _ = x.shape
        return x.reshape(B, n, self.n_heads, self.d_k).transpose(0, 2, 1, 3)

    def forward(self, Q_seq, KV_seq, return_weights=False):
        """
        Q_seq : (B, n_target, d_model)  目标序列（解码侧查询）
        KV_seq: (B, n_source, d_model)  源序列（编码侧键/值）
        返回   : (B, n_target, d_model)
        """
        Q = self._split_heads(Q_seq @ self.W_Q)   # (B,h,n_t,d_k)
        K = self._split_heads(KV_seq @ self.W_K)  # (B,h,n_s,d_k)
        V = self._split_heads(KV_seq @ self.W_V)
        scores = np.einsum("bhqd,bhkd->bhqk", Q, K) / np.sqrt(self.d_k)
        # 数值稳定 softmax（沿 source 维）
        scores = scores - scores.max(axis=-1, keepdims=True)
        w = np.exp(scores)
        w = w / w.sum(axis=-1, keepdims=True)
        ctx = np.einsum("bhqk,bhkd->bhqd", w, V)           # (B,h,n_t,d_k)
        ctx = ctx.transpose(0, 2, 1, 3).reshape(Q_seq.shape[0], Q_seq.shape[1], self.d_model)
        out = ctx @ self.W_O
        return (out, w) if return_weights else out


def _torch_bridge_available():
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


class TorchCrossAttentionBridge:
    """PyTorch 版交叉注意力（训练/推理用，需 torch）。API 与 NumPy 版对齐。"""

    def __init__(self, d_model, n_heads=4):
        import torch
        import torch.nn as nn
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.q = nn.Linear(d_model, d_model)
        self.k = nn.Linear(d_model, d_model)
        self.v = nn.Linear(d_model, d_model)
        self.o = nn.Linear(d_model, d_model)
        self._torch = torch

    def forward(self, Q_seq, KV_seq, return_weights=False):
        import torch.nn.functional as F
        t = self._torch
        Q = self._split_heads(self.q(Q_seq))
        K = self._split_heads(self.k(KV_seq))
        V = self._split_heads(self.v(KV_seq))
        out = F.scaled_dot_product_attention(Q, K, V)      # (B,h,n_t,d_k)
        out = out.transpose(1, 2).reshape(Q_seq.shape[0], Q_seq.shape[1], self.d_model)
        out = self.o(out)
        if return_weights:
            scores = (Q @ K.transpose(-2, -1)) / (self.d_k ** 0.5)
            w = t.softmax(scores, dim=-1)
            return out, w
        return out

    def _split_heads(self, x):
        B, n, _ = x.shape
        return x.reshape(B, n, self.n_heads, self.d_k).transpose(1, 2)


def demo():
    """双序列桥接演示：源=PDE 场表征，目标=参数序列。"""
    rng = np.random.default_rng(7)
    B, n_source, n_target, d = 2, 12, 5, 16
    src = rng.normal(0, 1, (B, n_source, d))   # 例如：PDE 场 latent
    tgt = rng.normal(0, 1, (B, n_target, d))   # 例如：参数/边界条件序列
    bridge = CrossAttentionBridge(d, n_heads=4)
    out, w = bridge.forward(tgt, src, return_weights=True)
    assert out.shape == (B, n_target, d), out.shape
    assert w.shape == (B, 4, n_target, n_source), w.shape
    print(f"[cross-attn] 源序列 {src.shape} → 目标序列 {tgt.shape}")
    print(f"[cross-attn] 桥接输出 {out.shape}（每行目标 token 聚合了全部源 token 的信息）")
    print(f"[cross-attn] 注意力权重 {w.shape}（n_target × n_source 矩形矩阵）")
    print("[cross-attn] self-test OK")


if __name__ == "__main__":
    demo()
    print(f"[cross-attn] PyTorch 版可用: {_torch_bridge_available()}")
