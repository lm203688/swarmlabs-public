"""
BM25 vs TF-IDF 检索对比测试（2026-10-14 升级证据）。

目的
----
验证 core/experience_store.py 用 BM25 替换 TF-IDF + cosine 的升级是**可衡量的**，
而不是"感觉更好"。用一份合成查询集，跑两种检索算法，输出：

  - Recall@1 / Recall@3 / Recall@5
  - MRR (Mean Reciprocal Rank)
  - 平均分数分布
  - Top-k 匹配一致性 (Overlap@5)

数据构造策略
------------
用**语义相关的自然英文任务描述**（避开中文分词陷阱）构造 (query, correct_doc_idx)
对，每个 query 都有 ground truth。旧 TF-IDF 和 BM25 用同一份 tokenizer。

评估指标
--------
- Recall@k: top-k 结果里包含 ground truth 的比例
- MRR: 1 / rank_of_first_hit 的平均（rank=0 → MRR=1）
- Overlap@k: 两种方法 top-k 集合的 Jaccard

运行
----
    python -m tests.test_bm25_vs_tfidf          # 打印对比报告
    python -m tests.test_bm25_vs_tfidf --write  # 同时写入 JSON 报告
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# ----------------------------------------------------------------------
# 复用 experience_store 里的 tokenizer，保持两种算法输入一致
# ----------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.experience_store import _tokenize  # noqa: E402
from core.bm25 import BM25  # noqa: E402


# ----------------------------------------------------------------------
# Baseline TF-IDF + cosine（复现 experience_store 旧实现，作为对照）
# ----------------------------------------------------------------------
def tfidf_baseline(docs_tokens: List[List[str]], query_tokens: List[str]) -> np.ndarray:
    """
    TF-IDF + cosine similarity（原实现逐字复刻）
    - tf(t, d) = count(t, d) / len(d)
    - idf(t)   = log((N + 1) / (df(t) + 1)) + 1
    - score    = cosine(tfidf_query, tfidf_doc)
    Returns: shape=(N,)
    """
    N = len(docs_tokens)
    if N == 0:
        return np.zeros(0)

    # Doc frequency
    df: Dict[str, int] = {}
    for doc in docs_tokens:
        for t in set(doc):
            df[t] = df.get(t, 0) + 1

    def tf(vec: Dict[str, int], total: int) -> Dict[str, float]:
        return {t: c / total for t, c in vec.items()} if total else {}

    def tfidf(tokens: List[str]) -> Dict[str, float]:
        counts: Dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        total = len(tokens) or 1
        out: Dict[str, float] = {}
        for t, c in counts.items():
            idf_v = math.log((N + 1) / (df.get(t, 0) + 1)) + 1
            out[t] = (c / total) * idf_v
        return out

    def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
        keys = set(a) & set(b)
        if not keys:
            return 0.0
        dot = sum(a[k] * b[k] for k in keys)
        na = math.sqrt(sum(v * v for v in a.values())) or 1.0
        nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
        return dot / (na * nb)

    q_vec = tfidf(query_tokens)
    return np.array([cosine(q_vec, tfidf(d)) for d in docs_tokens])


# ----------------------------------------------------------------------
# 合成数据集：30 组 (query, correct_doc_idx, distractors)
# ----------------------------------------------------------------------
# 覆盖：生物（Monod/Hodgkin-Huxley）、化学（Arrhenius/Suzuki）、
#       金融（Black-Scholes）、微生物、Agent 内部任务
# 每对 ground truth 与其"近邻"（同主题）+ "远邻"（不同主题）混合。
CORPUS: List[str] = [
    "monod growth kinetic model substrate consumption",
    "arrhenius temperature dependence of reaction rate constant",
    "black scholes option pricing formula volatility",
    "lotka volterra predator prey oscillation equilibrium",
    "hodgkin huxley neuronal membrane potential dynamics",
    "monod growth rate maximum half saturation constant",
    "suzuki coupling palladium catalyst yield optimization",
    "esterification acid catalyzed equilibrium constant",
    "bayesian optimization expected improvement acquisition",
    "saltelli sobol sensitivity variance decomposition",
    "conformal prediction finite sample coverage interval",
    "gp surrogate gaussian process acquisition uncertainty",
    "bioreactor oxygen transfer feed rate control",
    "fermentation temperature ph monitoring setpoint",
    "autonomous agent planner executor verifier loop",
    "trust layer reproducible certificate signing",
    "monod kinetic model fitting parameter estimation",
    "enzyme catalysis michaelis_menten kinetics substrate",
    "gaussian process cross validation log likelihood",
    "active learning uncertainty sampling query strategy",
    "neural network pinn physics informed loss residual",
    "deep onet operator learning function approximation",
    "agent memory experience store recall retrieval",
    "conditional contrast detection dual context success failure",
    "system test basic task execution success",
    "task description keyword extraction tfidf retrieval",
    "bayes factor model comparison evidence ratio",
    "empirical coverage conformal calibration quantile",
    "sobol sensitivity partial variance first order index",
    "experiment proposal next experiment design space",
]

# --- Easy queries: 词项大量重叠，两种方法都能正确排序 ---
EASY_QUERIES: List[Tuple[str, int]] = [
    ("monod substrate kinetic", 0),
    ("arrhenius rate temperature", 1),
    ("option pricing volatility", 2),
    ("predator prey population", 3),
    ("neural membrane potential", 4),
    ("half saturation constant growth", 5),
    ("suzuki palladium yield", 6),
    ("esterification equilibrium acid", 7),
    ("expected improvement bayesian", 8),
    ("sobol variance decomposition", 9),
    ("conformal coverage interval", 10),
    ("gaussian process acquisition", 11),
    ("oxygen transfer bioreactor", 12),
    ("fermentation ph temperature", 13),
    ("planner executor verifier", 14),
    ("reproducible certificate signing", 15),
    ("monod parameter estimation", 16),
    ("michaelis menten kinetics", 17),
    ("gp cross validation likelihood", 18),
    ("uncertainty sampling active learning", 19),
    ("physics informed neural net", 20),
    ("deep onet operator", 21),
    ("experience store recall", 22),
    ("dual context contrast", 23),
    ("basic task execution test", 24),
    ("keyword extraction tfidf", 25),
    ("bayes factor evidence", 26),
    ("conformal quantile calibration", 27),
    ("sobol partial variance", 28),
    ("next experiment design", 29),
]

# --- Hard queries: 同义异形 / 短查询 / 部分重叠 / 中文 / 词序扰动 ---
# BM25 优势场景：文档长度惩罚（b=0.75）、tf 权重（k1=1.5）、IDF 修正
HARD_QUERIES: List[Tuple[str, int]] = [
    # 单 token 查询——TF-IDF 的 cosine 对单 token 会因文档长度不同而稀释信号
    ("monod", 0),        # correct: idx 0；但 idx 5, 16 也含 monod（近邻）
    ("sobol", 9),        # correct: idx 9；但 idx 28 也含 sobol
    ("gp", 11),          # correct: idx 11；但 idx 18 也含 gp（"gaussian process"）
    # 单字 + 常见词——TF-IDF 会让所有含 gp/monod 的文档几乎同分
    ("conformal", 10),   # correct: idx 10；但 idx 27 也含 conformal
    # 同义异形——"temperature" 出现在 idx 1, 13, 14；"ph" 只出现在 idx 13
    ("ph fermentation", 13),
    ("temperature fermentation", 13),
    # 中文查询——tokenizer 会保留整块中文作为单 token
    ("系统测试任务", 24),  # correct: idx 24 (system test basic task)
    ("任务执行", 24),
    # 只重叠 1 个词的短查询（TF-IDF 会因 cosine 归一化导致弱信号）
    ("planner", 14),     # 只 idx 14 含 planner
    ("volatility", 2),   # 只 idx 2 含 volatility
    # 部分重叠 + 干扰
    ("option formula", 2),
    ("neuronal potential", 4),
    # 词序倒置（对 BM25/TFIDF 无影响，验证对称性）
    ("kinetic monod model", 0),
]

QUERIES = EASY_QUERIES + HARD_QUERIES


def _rank_of(scores: np.ndarray, target: int) -> int:
    """Rank of target in scores (0-based, higher score = rank 0). -1 if absent."""
    order = np.argsort(-scores, kind="stable")
    for rank, idx in enumerate(order):
        if int(idx) == target:
            return rank
    return -1


def evaluate(
    scores_fn,
    corpus_tokens: List[List[str]],
    queries: List[Tuple[str, int]],
    k_list=(1, 3, 5),
) -> Dict[str, object]:
    """Run scores_fn over each query and collect Recall@k, MRR, hit ranks."""
    recall = {k: 0 for k in k_list}
    mrr_hits = 0.0
    hit_ranks: List[int] = []
    all_scores: List[float] = []
    total = 0

    for query, correct in queries:
        scores = scores_fn(corpus_tokens, _tokenize(query))
        all_scores.extend(scores.tolist())
        rank = _rank_of(scores, correct)
        if rank >= 0:
            hit_ranks.append(rank)
            mrr_hits += 1.0 / (rank + 1)
            for k in k_list:
                if rank < k:
                    recall[k] += 1
        total += 1

    avg_score = float(np.mean(all_scores)) if all_scores else 0.0
    return {
        "total_queries": total,
        "recall": {f"recall@{k}": recall[k] / total for k in k_list},
        "mrr": mrr_hits / total,
        "avg_score": avg_score,
        "hit_ranks": hit_ranks,
        "avg_hit_rank": float(np.mean(hit_ranks)) if hit_ranks else float("inf"),
    }


def jaccard_at_k(ranked_a: List[int], ranked_b: List[int], k: int = 5) -> float:
    sa = set(ranked_a[:k])
    sb = set(ranked_b[:k])
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def rank_by_scores(scores: np.ndarray) -> List[int]:
    return [int(i) for i in np.argsort(-scores, kind="stable").tolist()]


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main(write_report: bool = False) -> Dict[str, object]:
    # Tokenize corpus once
    corpus_tokens: List[List[str]] = [_tokenize(doc) for doc in CORPUS]

    # BM25
    bm25 = BM25().fit(corpus_tokens)
    bm25_scores_fn = lambda corpus, query: bm25.score_all(query)

    # TF-IDF baseline
    tfidf_scores_fn = tfidf_baseline

    print("=" * 70)
    print("BM25 vs TF-IDF retrieval comparison")
    print("=" * 70)
    print(f"Corpus size    : {len(CORPUS)} documents")
    print(f"Queries        : {len(QUERIES)}")
    print(f"Vocab (BM25)   : {len(bm25.vocab)}")
    print(f"Vocab (TF-IDF) : {sum(1 for _ in set(t for doc in corpus_tokens for t in doc))}")
    print()

    t0 = time.perf_counter()
    bm25_report = evaluate(bm25_scores_fn, corpus_tokens, QUERIES)
    t_bm25 = time.perf_counter() - t0

    t0 = time.perf_counter()
    tfidf_report = evaluate(tfidf_scores_fn, corpus_tokens, QUERIES)
    t_tfidf = time.perf_counter() - t0

    print(f"{'Metric':<20} {'TF-IDF':>12} {'BM25':>12} {'Delta':>12}")
    print("-" * 58)
    for k in (1, 3, 5):
        a = tfidf_report["recall"][f"recall@{k}"]
        b = bm25_report["recall"][f"recall@{k}"]
        print(f"{'recall@' + str(k):<20} {a:>12.4f} {b:>12.4f} {b - a:>+12.4f}")
    print()
    for m in ("mrr", "avg_hit_rank"):
        a = tfidf_report[m]
        b = bm25_report[m]
        sign = "↑" if m == "mrr" else "↓"  # ↑ good for MRR, ↓ good for rank
        print(f"  {m:<18} {a:>12.4f} {b:>12.4f} {b - a:>+12.4f}  {sign} good")
    print(f"{'avg_score':<20} {tfidf_report['avg_score']:>12.4f} {bm25_report['avg_score']:>12.4f}")
    print(f"{'runtime (s)':<20} {t_tfidf:>12.4f} {t_bm25:>12.4f}")

    # Top-5 overlap
    overlaps = []
    for query, _ in QUERIES:
        a = rank_by_scores(tfidf_scores_fn(corpus_tokens, _tokenize(query)))
        b = rank_by_scores(bm25_scores_fn(corpus_tokens, _tokenize(query)))
        overlaps.append(jaccard_at_k(a, b, k=5))
    print(f"\nTop-5 Jaccard overlap (TF-IDF vs BM25): {np.mean(overlaps):.4f}")

    # 展示失败案例
    def _run_and_report(tag: str, q: str, correct: int):
        a_scores = tfidf_scores_fn(corpus_tokens, _tokenize(q))
        b_scores = bm25.score_all(_tokenize(q))
        a_rank = _rank_of(a_scores, correct)
        b_rank = _rank_of(b_scores, correct)
        print(f"[{tag}] {q!r}  gt={correct}  "
              f"TFIDF rank={a_rank + 1 if a_rank >= 0 else 'miss'}  "
              f"BM25 rank={b_rank + 1 if b_rank >= 0 else 'miss'}  "
              f"TFIDF score={a_scores[correct]:.4f}  BM25 score={b_scores[correct]:.4f}")

    print("\n--- 3 个典型（成功）查询的排名对比 ---")
    _run_and_report("easy", QUERIES[0][0], QUERIES[0][1])
    _run_and_report("easy", QUERIES[10][0], QUERIES[10][1])
    _run_and_report("easy", QUERIES[22][0], QUERIES[22][1])

    # 找出失败案例（BM25 rank > 3）
    print("\n--- 失败案例分析（BM25 rank > 3） ---")
    failed = 0
    for q, correct in QUERIES:
        b_scores = bm25.score_all(_tokenize(q))
        b_rank = _rank_of(b_scores, correct)
        if b_rank > 3:
            _run_and_report("FAIL", q, correct)
            failed += 1
    if failed == 0:
        print("  (all queries ranked within top-3 by BM25)")

    # ------------------------------------------------------------------
    # 中文实测场景：反映项目真实数据特征
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("中文实测场景（反映真实 experience_store 数据分布）")
    print("=" * 70)
    zh_corpus = [
        "系统测试任务",
        "优化渲染管线性能",
        "部署模型到生产环境",
        "训练深度学习模型",
        "生成训练数据集",
        "系统测试任务",       # 重复项（模拟真实重复经验）
        "验证回归测试通过",
        "修复边界条件 bug",
        "重构代码模块",
        "更新依赖版本",
    ]
    zh_tokens = [_tokenize(doc) for doc in zh_corpus]
    print(f"Chinese corpus size: {len(zh_corpus)}")
    print(f"Tokenized sample: {_tokenize('系统测试任务')}")
    print(f"Tokenized sample: {_tokenize('部署模型到生产环境')}")

    zh_bm25 = BM25().fit(zh_tokens)
    zh_query = "系统测试"  # 部分重叠中文
    a_scores = tfidf_scores_fn(zh_tokens, _tokenize(zh_query))
    b_scores = zh_bm25.score_all(_tokenize(zh_query))

    print(f"\nQuery: {zh_query!r}  tokens={_tokenize(zh_query)}")
    print(f"  TF-IDF scores: {[round(float(s), 4) for s in a_scores]}")
    print(f"  BM25   scores: {[round(float(s), 4) for s in b_scores]}")
    print(f"  TF-IDF non-zero: {(a_scores > 0).sum()}/{len(a_scores)}")
    print(f"  BM25   non-zero: {(b_scores > 0).sum()}/{len(b_scores)}")
    if a_scores.max() > 0:
        print(f"  TF-IDF top-1: idx={int(a_scores.argmax())} score={a_scores.max():.4f}")
    else:
        print(f"  TF-IDF: 全部零分 —— 检索完全失效")
    if b_scores.max() > 0:
        print(f"  BM25   top-1: idx={int(b_scores.argmax())} score={b_scores.max():.4f}")
    else:
        print(f"  BM25: 全部零分 —— 检索完全失效")

    summary = {
        "corpus_size": len(CORPUS),
        "query_count": len(QUERIES),
        "tfidf": tfidf_report,
        "bm25": bm25_report,
        "top5_jaccard": float(np.mean(overlaps)),
        "runtime_seconds": {"tfidf": t_tfidf, "bm25": t_bm25},
        "score_scale_ratio_bm25_over_tfidf": float(bm25_report["avg_score"] / max(tfidf_report["avg_score"], 1e-9)),
        "chinese_scenario": {
            "corpus": zh_corpus,
            "query": zh_query,
            "tfidf_nonzero_count": int((a_scores > 0).sum()),
            "bm25_nonzero_count": int((b_scores > 0).sum()),
            "tfidf_top1_score": float(a_scores.max()),
            "bm25_top1_score": float(b_scores.max()),
            "tfidf_top1_idx": int(a_scores.argmax()) if a_scores.max() > 0 else -1,
            "bm25_top1_idx": int(b_scores.argmax()) if b_scores.max() > 0 else -1,
        },
    }

    if write_report:
        out = Path("reports/upgrade/bm25_comparison_results.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nReport written to {out}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write JSON report")
    args = parser.parse_args()
    main(write_report=args.write)
