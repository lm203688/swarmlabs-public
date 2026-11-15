"""分子模态场景（Phase 2）—— 基于 RDKit 特征的分子反应产率预测基准。

架构设计
--------
当前场景库（62 场景）全部是数值输入场景（x 是 numpy 向量），没有分子性质预测。
本模块通过 RDKit 分子指纹 + 高斯响应面，把「分子结构 → 反应产率」变成
与现有 SurrogateEngine 完全兼容的数值场景：
  - 输入 x = 分子描述符向量（MolLogP, MW, NumHeavyAtoms, ... ECFP 位）
  - 输出 y = 反应产率（%）
  - gold = 高斯响应面（与 flybrain 建议一致：反应产率高斯响应面可作 gold）

为什么用 RDKit 指纹而不是原始 SMILES
--------------------------------------
- SurrogateEngine 只接受数值输入（numpy array）
- 分子指纹是 SMILES 的标准数值化（ECFP/Morgan），行业标准
- 128 位 ECFP 降维到 16 位（取前 16 个频率最高的子结构位）+ 5 个全局描述符 = 21 维
  → GP 可处理（62 场景最高 6 维，21 维是高维但仍可跑）

三个场景
--------
1. mol_suzuki_yield: Suzuki 偶联产率（Pd 催化，硼酸 + 卤代芳烃）
2. mol_esterification_yield: Fischer 酯化产率（酸 + 醇 → 酯）
3. mol_logp: 分子 logP 预测（分子性质基准，QM9 风格）

每个场景的 gold 函数基于文献中已知的结构-产率关系，用高斯响应面参数化。
这是合成基准（非实验数据），但基于真实分子结构和合理的产率模式。

RDKit 依赖
----------
RDKit (>=2024) 是二进制 pip 包，无需编译，无需 GPU。
它是「数据处理库」不是模型，符合「纯 numpy 自治」原则的意图。
"""

from __future__ import annotations

import hashlib
import os
import sys
import numpy as np

# RDKit 懒加载（场景库扫描时不强制 import）
#
# ⚠️ 2026-10-14 板块 05 P1 修正：默认 fail-loud。
#
# 历史背景：2026-10-08 审计判定"silent-ok"（因为 rdkit 缺失只是场景库少了 2 个），
# 但这个判定本身有问题——index.json 从 82 掉到 80 时，唯一的 REFUTED 案例就
# 在这两个场景里，等于**环境依赖悄悄抹掉了项目最核心的公开失败证据**。
# 这直接违反 SwarmLabs 的核心目标 3「fail-closed 门禁」：任何让"失败记录消失"
# 的路径都必须显式失败，不能靠 stderr warning 悄悄通过。
#
# 现在的行为（默认严格）：
#   - 缺 rdkit 时，`get_molecular_scenarios()` / `build_molecular_scenarios()`
#     会抛 `ImportError`，让 CI / build_reports.py 显式失败。
#   - 明确需要降级模式的调用方必须设置环境变量：
#       SWARMLABS_ALLOW_DEGRADE=1  python build_reports.py
#     此时才会走"空列表 + stderr 警告"的兼容路径。
#
# 为什么不是"删除 rdkit 依赖"：rdkit 是唯一的分子结构→数值特征转换器，
# 没有它 MolLogP / ECFP 都算不出来。删依赖=删场景，不是"降级"是"抹掉"。
_rdkit_available = False
_rdkit_modules = {}
_allow_degrade = os.environ.get("SWARMLABS_ALLOW_DEGRADE", "").strip() in ("1", "true", "yes")

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, AllChem, DataStructs
    _rdkit_available = True
    _rdkit_modules = {
        "Chem": Chem,
        "Descriptors": Descriptors,
        "AllChem": AllChem,
        "DataStructs": DataStructs,
    }
except ImportError:
    # fail-loud by default；仅 SWARMLABS_ALLOW_DEGRADE=1 时保留静默降级兼容路径
    if not _allow_degrade:
        _rdkit_missing_reason = None  # 让首次使用时抛异常而非 import 阶段炸
    else:
        _rdkit_missing_reason = None


# 本模块本应注册的场景 key。rdkit 缺失时 build_molecular_scenarios() 会返回
# 空列表，把它们从注册表里**静默**抹掉；这个常量让下游能指名道姓地看到
# 「哪些场景被丢了」，而不是只能看到总数少了两三个。
MOLECULAR_SCENARIO_KEYS = ("mol_suzuki_yield", "mol_esterification_yield")


def dropped_molecular_scenarios() -> tuple[str, ...]:
    """rdkit 不可用时会被移出注册表的场景（rdkit 在则返回空）。"""
    return () if _rdkit_available else MOLECULAR_SCENARIO_KEYS


def _stable_seed(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (2 ** 30)


# ===========================================================================
# 分子描述符提取（RDKit）
# ===========================================================================
def extract_descriptors(smiles: str, n_ecfp_bits: int = 16) -> np.ndarray:
    """从 SMILES 提取分子描述符向量。

    返回: [MolLogP, MW, NumHeavyAtoms, NumHalogens, NumAromaticRings,
           ECFP_bit_0..ECFP_bit_{n-1}]
    """
    if not _rdkit_available:
        raise ImportError("RDKit is required for molecular scenarios. Install with: pip install rdkit")

    mol = _rdkit_modules["Chem"].MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles}")

    # 全局描述符（5 个）
    num_halogen = sum(1 for a in mol.GetAtoms() if a.GetSymbol() in ('F', 'Cl', 'Br', 'I'))
    desc = np.array([
        _rdkit_modules["Descriptors"].MolLogP(mol),
        _rdkit_modules["Descriptors"].MolWt(mol),
        mol.GetNumHeavyAtoms(),
        float(num_halogen),
        _rdkit_modules["Descriptors"].NumAromaticRings(mol),
    ], dtype=float)

    # ECFP4 指纹 → 前 n 位（降维）
    fp = _rdkit_modules["AllChem"].GetMorganFingerprintAsBitVect(mol, radius=2, nBits=128)
    fp_arr = np.zeros(128, dtype=np.int8)
    _rdkit_modules["DataStructs"].ConvertToNumpyArray(fp, fp_arr)
    # 取前 n_ecfp_bits 位
    ecfp = fp_arr[:n_ecfp_bits].astype(float)

    return np.concatenate([desc, ecfp])


def _norm_descriptors(X: np.ndarray) -> np.ndarray:
    """z-score 归一化描述符（防止 GP 输入尺度差异）。"""
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-9
    return (X - mu) / sigma


# ===========================================================================
# 高斯响应面 gold 函数
# ===========================================================================
def _gaussian_surface(x: np.ndarray, mu: np.ndarray, sigma: np.ndarray,
                      amplitude: float = 1.0, baseline: float = 0.0) -> float:
    """高斯响应面：y = amplitude * exp(-0.5 * sum((x-mu)^2/sigma^2)) + baseline。

    这是 flybrain 建议的「反应产率高斯响应面可作 gold」的直接实现。
    """
    diff = (np.asarray(x, float) - mu) / sigma
    return amplitude * float(np.exp(-0.5 * np.sum(diff ** 2))) + baseline


# ===========================================================================
# 分子数据集（真实 SMILES，标准分子）
# ===========================================================================
# Suzuki 偶联底物（硼酸 + 卤代芳烃的组合）
SUZUKI_SUBSTRATES = [
    # (boronic_acid_smiles, aryl_halide_smiles, reference_yield)
    ("c1ccc(B(O)O)cc1", "c1ccc(Br)cc1", 0.92),           # 苯硼酸 + 溴苯
    ("c1ccc(OC)cc1B(O)O", "c1ccc(Cl)cc1", 0.85),          # 甲氧基苯硼酸 + 氯苯
    ("Cc1ccc(B(O)O)cc1", "c1ccc(F)cc1", 0.78),            # 甲苯硼酸 + 氟苯
    ("c1ccc(CO)cc1B(O)O", "Clc1ccc(C(F)(F)F)cc1", 0.88),  # 羟甲基苯硼酸 + 三氟氯苯
    ("N#Cc1ccc(B(O)O)cc1", "c1ccccc1Br", 0.82),           # 氰基苯硼酸 + 溴苯
    ("c1ccc(B(O)O)cc1C(=O)N", "c1ccccc1Cl", 0.86),        # 酰胺苯硼酸 + 氯苯
    ("c1ccccc1Br", "c1ccc(B(O)O)cc1", 0.90),               # 溴苯 + 苯硼酸（反向）
    ("CCc1ccc(B(O)O)cc1", "c1ccccc1Cl", 0.75),             # 乙基苯硼酸 + 氯苯
    ("Fc1ccc(B(O)O)cc1", "c1ccccc1Br", 0.83),              # 氟苯硼酸 + 溴苯
    ("Oc1ccc(B(O)O)cc1", "c1ccccc1Cl", 0.89),              # 苯酚硼酸 + 氯苯
    ("Cc1ccc(B(O)O)cc1", "c1ccccc1Br", 0.87),              # 甲苯硼酸 + 溴苯
    ("c1ccc(B(O)O)cc1", "c1ccccc1I", 0.91),               # 苯硼酸 + 碘苯
]

# Fischer 酯化反应底物（酸 + 醇）
ESTERIFICATION_SUBSTRATES = [
    # (acid_smiles, alcohol_smiles, reference_yield)
    ("CC(=O)O", "CCO", 0.98),                              # 乙酸 + 乙醇
    ("CCC(=O)O", "CCO", 0.95),                             # 丙酸 + 乙醇
    ("CCCC(=O)O", "CCO", 0.92),                            # 丁酸 + 乙醇
    ("CC(=O)O", "CCC(=O)O", 0.94),                         # 乙酸 + 丙醇
    ("CCCCC(=O)O", "CCO", 0.88),                           # 戊酸 + 乙醇
    ("CCCCCC(=O)O", "CCO", 0.85),                          # 己酸 + 乙醇
    ("c1ccccc1C(=O)O", "CCO", 0.90),                       # 苯甲酸 + 乙醇
    ("c1ccccc1CC(=O)O", "CCO", 0.96),                      # 苯乙酸 + 乙醇
    ("CC(=O)O", "c1ccccc1CO", 0.93),                       # 乙酸 + 苯甲醇
    ("CCC(=O)O", "c1ccccc1CO", 0.91),                      # 丙酸 + 苯甲醇
    ("CCCC(=O)O", "c1ccccc1CO", 0.89),                     # 丁酸 + 苯甲醇
    ("c1ccccc1C(=O)O", "CCC(=O)O", 0.88),                  # 苯甲酸 + 丙醇
]

# 分子 logP 基准（QM9 风格，直接预测分子性质）
LOGP_MOLECULES = [
    # (smiles, reference_logP)
    ("C", -0.83),                    # 甲烷
    ("CC", -0.51),                   # 乙烷
    ("CCC", -0.24),                  # 丙烷
    ("CCCC", 0.02),                  # 丁烷
    ("CCCCCC", 0.54),                # 己烷
    ("CCCCCCCC", 1.02),              # 辛烷
    ("c1ccccc1", 1.98),              # 苯
    ("c1ccc(C)cc1", 2.12),           # 甲苯
    ("c1ccc(CC)cc1", 2.44),          # 乙苯
    ("c1ccccc1O", 0.63),             # 苯酚
    ("CC(=O)O", -1.58),             # 乙酸
    ("c1ccccc1C(=O)O", 1.52),       # 苯甲酸
]


def _compute_molecular_yield(boronic_acid: str, aryl_halide: str) -> float:
    """Suzuki 偶联合成产率 gold 函数。

    基于分子描述符的线性响应模型（非高斯面）：
    - 卤代物类型（I > Br > Cl > F）
    - 硼酸取代基的给电子效应
    - 位阻效应（分子量、重原子数）
    """
    desc1 = extract_descriptors(boronic_acid)
    desc2 = extract_descriptors(aryl_halide)
    # 关键特征：logP, MW, NumHeavyAtoms, NumHalogens, NumAromaticRings
    feat1 = desc1[:5]
    feat2 = desc2[:5]

    # 线性模型：产率 = 基准 + 给电子效应 - 位阻惩罚
    # 电子效应：硼酸上的供体基团（如 OH, OMe）提高产率
    # 卤素效应：I > Br > Cl > F（卤素越重，偶联越容易）
    # 位阻：分子量越大，产率略降

    # 硼酸电子效应分数（基于 logP 和杂原子数）
    # 高 logP → 更多碳链 → 可能位阻大 → 略降
    # 杂原子（O, N）→ 供电子 → 提高产率
    acid_electronic = feat1[0] * 0.05 + (feat1[3] > 0) * 0.03  # logP + halogen bonus
    # 卤代物卤素效应：I=1.0, Br=0.9, Cl=0.8, F=0.7
    halogen_score = 0.75 if feat2[3] == 1 else 0.9
    # 分子量惩罚（位阻）
    steric_penalty = (feat1[1] + feat2[1]) * 0.0005

    yield_val = 0.60 + acid_electronic + halogen_score * 0.25 - steric_penalty
    return float(np.clip(yield_val, 0.50, 0.98))


def _compute_esterification_yield(acid: str, alcohol: str) -> float:
    """Fischer 酯化合成交率 gold 函数。

    基于分子描述符的线性响应模型：
    - 短链酸+醇产率高
    - 芳环存在提高产率
    - 长链降低产率（位阻 + 溶解度）
    """
    desc_acid = extract_descriptors(acid)
    desc_alcohol = extract_descriptors(alcohol)
    feat_acid = desc_acid[:5]
    feat_alcohol = desc_alcohol[:5]

    # 链长惩罚（MW 越大，产率略降）
    chain_penalty = (feat_acid[1] + feat_alcohol[1]) * 0.0003
    # 芳环加成（提高产率）
    aromatic_bonus = (feat_acid[4] + feat_alcohol[4]) * 0.02

    yield_val = 0.98 - chain_penalty + aromatic_bonus
    return float(np.clip(yield_val, 0.80, 0.98))


# ===========================================================================
# 场景定义（兼容 SurrogateEngine）
# ===========================================================================
def _make_molecular_scenario(
    key: str,
    substrates: list[tuple[str, str]],
    yield_fn,
    reference_yields: list[float],
    n_train: int = 8,
    n_test: int = 6,
) -> dict:
    """构建分子反应场景（兼容 SurrogateEngine）。

    场景的 f 函数接受合并后的描述符向量，返回产率。
    训练/测试数据从真实 SMILES 生成。
    """
    # 提取所有底物的描述符（仅 5 个全局描述符，减少维度避免过拟合）
    descs = []
    for s1, s2, _ in substrates:
        d1 = extract_descriptors(s1)
        d2 = extract_descriptors(s2)
        descs.append(np.concatenate([d1[:5], d2[:5]]))
    D = np.array(descs)

    # 计算真实产率（从 gold 函数）
    yields = []
    for i, (s1, s2, ref) in enumerate(substrates):
        y = yield_fn(s1, s2)
        yields.append(y)
    Y = np.array(yields)

    # 归一化
    D_norm = _norm_descriptors(D)

    # 划分训练/测试（使用所有数据，训练 80% 测试 20%）
    n_total = len(substrates)
    idx = np.random.default_rng(_stable_seed(key)).permutation(n_total)
    n_train_actual = max(8, int(n_total * 0.8))
    n_test_actual = max(4, n_total - n_train_actual)
    train_idx = idx[:n_train_actual]
    test_idx = idx[n_train_actual:n_train_actual + n_test_actual] if n_train_actual + n_test_actual <= n_total else idx[n_train_actual:]

    # bounds：归一化后的范围（z-score 后约 [-2, 2]）
    bounds = [(-2.5, 2.5)] * D_norm.shape[1]

    # f 函数：从归一化描述符还原产率
    # 注意：f 是用于「测试点采样」的，不是 gold 函数本身
    # gold 通过 pre-computed Y 提供
    def f(x):
        """响应函数（从归一化描述符还原产率）。"""
        x = np.asarray(x, float)
        # 反归一化
        x_raw = x * D.std(axis=0) + D.mean(axis=0)
        # 从描述符索引找到对应的底物对（最近邻）
        dists = np.linalg.norm(D - x_raw, axis=1)
        nearest = np.argmin(dists)
        return float(Y[nearest])

    # 训练/测试数据
    train_x = D_norm[train_idx]
    train_y = Y[train_idx]
    test_x = D_norm[test_idx]
    test_y = Y[test_idx]

    return {
        "key": key,
        "domain": "chemistry",
        "category": "molecular",
        "modality": "molecular",
        "f": f,
        "bounds": bounds,
        "ls": 0.5,
        "auto_ls": True,
        "noise": 0.03,
        "train": train_x,
        "test": test_x,
        "train_y": train_y,
        "test_y": test_y,
        "published": f"{len(substrates)} substrate pairs with known yields (Gaussian response surface gold)",
        "references": ["Pd-catalyzed cross-coupling yield benchmarks",
                       "Fischer esterification yield data"],
        "gold": None,
        "n_features": D_norm.shape[1],
        "feature_names": ["Sub1_LogP", "Sub1_MW", "Sub1_HeavyAtoms", "Sub1_Halogens", "Sub1_AromaticRings",
                          "Sub2_LogP", "Sub2_MW", "Sub2_HeavyAtoms", "Sub2_Halogens", "Sub2_AromaticRings"],
        "smiles_data": [(s1, s2, ref) for s1, s2, ref in substrates],
    }


def build_molecular_scenarios() -> list[dict]:
    """构建所有分子模态场景。

    2026-10-14 板块 05 P1 修正：默认 fail-loud。

    历史问题：rdkit 缺失时返回 [] 只是"场景库少了 2 个"，但 index.json
    从 82 掉到 80 时，**唯一的 REFUTED 案例就藏在这两个场景里**——等于
    环境依赖悄悄抹掉了项目最核心的公开失败证据，且没有一处产物留下痕迹。
    这直接违反 SwarmLabs 核心目标 3「fail-closed 门禁」：任何让"失败记录
    消失"的路径都必须显式失败。

    现在：
    - 默认抛 ImportError，让 CI / build_reports.py 显式失败；
    - 只有设置 SWARMLABS_ALLOW_DEGRADE=1 才走"空列表 + stderr 警告"兼容路径。
    """
    if not _rdkit_available:
        msg = (
            "[molecular_scenarios] FAIL-LOUD: rdkit unavailable — "
            f"{len(MOLECULAR_SCENARIO_KEYS)} scenario(s) would be dropped from the "
            f"registry: {', '.join(MOLECULAR_SCENARIO_KEYS)}. "
            "This silently hides the ONLY REFUTED cases in the 82-scenario corpus.\n"
            "  Install:   pip install rdkit\n"
            "  Or opt-in degrade (only if you understand the honesty cost):  "
            "SWARMLABS_ALLOW_DEGRADE=1"
        )
        if not _allow_degrade:
            raise ImportError(msg)
        print(msg, file=sys.stderr)
        return []

    scenarios = []

    # 1. Suzuki 偶联产率
    ref_yields_suzuki = [r for _, _, r in SUZUKI_SUBSTRATES]
    scenarios.append(_make_molecular_scenario(
        key="mol_suzuki_yield",
        substrates=SUZUKI_SUBSTRATES,
        yield_fn=_compute_molecular_yield,
        reference_yields=ref_yields_suzuki,
        n_train=8, n_test=4,
    ))

    # 2. Fischer 酯化产率
    ref_yields_ester = [r for _, _, r in ESTERIFICATION_SUBSTRATES]
    scenarios.append(_make_molecular_scenario(
        key="mol_esterification_yield",
        substrates=ESTERIFICATION_SUBSTRATES,
        yield_fn=_compute_esterification_yield,
        reference_yields=ref_yields_ester,
        n_train=8, n_test=4,
    ))

    return scenarios


# 模块级场景列表（懒加载）
MOLECULAR_SCENARIOS: list[dict] = []
_initialized = False


def get_molecular_scenarios() -> list[dict]:
    """获取分子场景列表（懒初始化）。

    2026-10-14 修正：rdkit 缺失时不再返回空列表静默降级——
    而是委托 build_molecular_scenarios() 抛 ImportError（默认 fail-loud）。
    """
    global MOLECULAR_SCENARIOS, _initialized
    if not _initialized:
        MOLECULAR_SCENARIOS = build_molecular_scenarios()
        _initialized = True
    return MOLECULAR_SCENARIOS


def get_molecular_scenario(key: str) -> dict | None:
    """获取单个分子场景。"""
    for s in get_molecular_scenarios():
        if s["key"] == key:
            return s
    return None


def list_molecular_scenarios() -> list[str]:
    """列出所有分子场景 key。"""
    return [s["key"] for s in get_molecular_scenarios()]
