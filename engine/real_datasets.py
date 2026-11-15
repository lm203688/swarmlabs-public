"""UCI 机器学习基准数据集加载器 —— 用于 GP/BO 算法性能压测。

**⚠️ 命名澄清（重要）**：本模块名沿用了历史 `real_datasets`，但内容**并非**
本项目定位所指的"真实湿实验数据"。四个数据集均是 **UCI ML Repository** 的
物理/工程/化学基准：
  - airfoil: NASA 风洞声学风洞实测（Brooks et al. 1989）—— 声学工程，非生物
  - wine   : 红酒理化指标 → 感官评分（Cortez et al. 2009）—— 食品化学
  - yacht  : 船模拖曳 tank 水动力（Gerritsma 1981）—— 船舶工程
  - ccpp   : 联合循环电厂运行数据（Kaya et al. 2012）—— 能源系统

它们**是真实物理实验数据**（非合成），但**不属于本项目生物过程定位**（发酵 /
微生物 / 酶催化），也不构成"湿实验锚点"意义上的生物验证真值。它们的作用是
给 GP/BO 算法压测提供已知解的成熟基准，与 `g3_aligned_benchmark.json` 中
"G3 学科 0/14 通过"的诚实结论并不冲突——G3 测的是跨生物学科外推，本模块
测的是经典 ML 工程基准上的算法正确性。

**⚠️ 项目当前状态**：真实生物湿实验锚点数据仍为 **0 个场景**（详见
`data/trust_surface.json:overall.honesty.real_experiment_ground_truth`）。
本模块不应被对外宣传为"真实湿实验验证"。字段：
  X         : (N, d) 实验/运行数据输入
  y         : (N,)   实验/运行数据输出
  features  : 输入特征名
  citation  : 原始文献 / 数据集出处
  desc      : 一句话说明数据内容与来源类型

供 scripts/real_validation.py 调用做算法正确性回归，非生物真实性证明。
"""

from __future__ import annotations

from pathlib import Path
import csv
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "real"


def load_airfoil(path=None):
    """NASA 翼型自噪声（风洞实测声压级）。Brooks et al. 1989, UCI #291。"""
    p = path or (REAL / "airfoil_self_noise.dat")
    arr = np.loadtxt(p)
    X, y = arr[:, :5], arr[:, 5]
    return dict(
        X=X, y=y,
        features=["频率_Hz", "攻角_度", "弦长_m", "来流速度_mps", "厚度_m"],
        citation="NASA APS noise data (Brooks, Pope & Marcolini 1989), UCI#291",
        desc="NASA 风洞实测翼型自噪声（真实声学实验，1503 条）",
    )


def load_wine(path=None):
    """红酒品质（理化指标 → 感官评分）。Cortez et al. 2009, UCI #186。"""
    p = path or (REAL / "winequality-red.csv")
    rows, feats = [], None
    with open(p, newline="") as f:
        r = csv.reader(f, delimiter=";")
        feats = next(r)[:-1]
        for row in r:
            rows.append([float(v) for v in row])
    arr = np.array(rows)
    X, y = arr[:, :-1], arr[:, -1]
    return dict(
        X=X, y=y, features=feats,
        citation="Cortez et al. 2009, Decis. Support Syst. 47(4):547, UCI#186",
        desc="真实红酒理化指标 → 感官评分（1599 瓶实测）",
    )


def load_yacht(path=None):
    """游艇水动力残余阻力（船模拖曳 tank）。Gerritsma 1981, UCI #243。"""
    p = path or (REAL / "yacht.data")
    arr = np.loadtxt(p)
    X, y = arr[:, :6], arr[:, 6]
    feats = ["纵倾位置", "棱柱系数", "宽/吃水", "长/排水量", "宽/吃水比", "弗劳德数"]
    return dict(
        X=X, y=y, features=feats,
        citation="Gerritsma 1981 towing-tank data, UCI#243",
        desc="真实船模拖曳 tank 残余阻力实验（308 条）",
    )


def load_ccpp(path=None):
    """联合循环电厂发电功率（真实运行数据）。Kaya et al. 2012, UCI #294。"""
    p = path or (REAL / "ccpp.zip")
    import io
    import pandas as pd
    with zipfile.ZipFile(p) as z:
        name = [n for n in z.namelist() if n.endswith(".xlsx")][0]
        df = pd.read_excel(io.BytesIO(z.read(name)))
    arr = df.values.astype(float)
    X, y = arr[:, :4], arr[:, 4]
    feats = ["温度_C", "环境压力_mbar", "相对湿度_%", "排气真空_cmHg"]
    return dict(
        X=X, y=y, features=feats,
        citation="Kaya et al. 2012 (Tübingen), UCI#294",
        desc="真实联合循环电厂发电功率（9568 条运行记录）",
    )


# 注册表：名称 -> (领域分类, 加载函数)
REGISTRY = {
    "airfoil": ("aeroacoustics", load_airfoil),
    "wine": ("food_chemistry", load_wine),
    "yacht": ("hydrodynamics", load_yacht),
    "ccpp": ("energy_systems", load_ccpp),
}


def load(name):
    if name not in REGISTRY:
        raise KeyError(f"未知真实数据集 {name}，可选: {list(REGISTRY)}")
    category, fn = REGISTRY[name]
    d = fn()
    d["key"] = name
    d["category"] = category
    return d


if __name__ == "__main__":
    for k in REGISTRY:
        d = load(k)
        print(f"{k:8s} {d['X'].shape}  {d['desc']}")
