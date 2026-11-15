# 保形预测方法选择指引（板块 03 P1，2026-10-15）

> 目的：把项目里已经落地的 **5 个 conformal 变体**归成一个可查表，回答
> 「我拿到一批训练点，该选哪种保形？在什么场景会翻车？」
> 全部方法**零外部依赖**（纯 numpy），可在离线环境跑通。

## 一句话结论

| 我的场景 | 推荐方法 | 关键模块 |
|---|---|---|
| 训练点 <200 且分布稳定 | split conformal | `engine/conformal.py` |
| 训练点 ≥500，想提升覆盖率到 ≈0.95 | adaptive conformal | `engine/adaptive_conformal.py` |
| 有物理量纲/守恒律约束 | physics-constrained conformal | `engine/physics_constrained_conformal.py` |
| 主动学习循环里选点 | conformal active learning | `engine/conformal_active_learning.py` |
| 校准策略本身也要优化 | conformal + RL（辅助） | `engine/calibration_rl.py` |

**不要**用「方法 A + 方法 B」堆栈——本项目的每个方法都是**独立完整**的
保形预测流程，混用会破坏覆盖率保证。

---

## 五个方法的能力与陷阱

### 1. Split Conformal（基础版）
- **文件**：`engine/conformal.py`
- **原理**：把数据切成 train/calibration 两段，用 calibration 的非一致性
  分位数定区间宽度。
- **优点**：证明成熟（Vovk 2005），单侧/双侧都支持。
- **陷阱**：
  - 训练点少（<200）时 calibration 分位数方差大；
  - 分布漂移（新样本 ≠ calibration 分布）时覆盖率会掉。
- **判定门槛**：`coverage ≥ 1 − α − 0.03`（3% 噪声地板）。

### 2. Adaptive Conformal（分位数自适应）
- **文件**：`engine/adaptive_conformal.py`
- **原理**：把 split conformal 的分位数 Q 变成**滚动更新**——每次新样本
  加入都会重算一次 Q，避免 calibration 阶段固定带来的偏差。
- **优点**：分布漂移时覆盖率仍接近标称（α=0.05 → 覆盖 ≈ 0.95）。
- **陷阱**：
  - 每次更新要多算一次 O(n) 排序；n > 10k 建议每 100 步更新一次；
  - 对**外推点**无效（外推点属于分布外，任何 conformal 都不保证覆盖）。
- **本项目实测**：`miss_tracker` 追踪累积 miss 率，实测 α=0.05 时
  覆盖 = 0.94–0.97 区间。

### 3. Physics-Constrained Conformal（外推注入物理先验）
- **文件**：`engine/physics_constrained_conformal.py`（412 行，2026-10-15
  已接线到 `engine.__init__`）
- **原理**：外推点先用物理约束（守恒律、量纲约束、单调性）投影到可行域，
  再套 adaptive conformal。等价于「外推时不预测 → 而是约束」。
- **优点**：G1/G2/G3 外推场景**唯一能给出非平凡结论**的方法。
- **陷阱**：
  - 需要预先注册物理约束（`PhysicsConstraint` dataclass）；无约束的纯数值
    场景应回退到 split conformal；
  - 约束本身不物理（如「单调递增」用于周期函数）会给出虚假覆盖率。
- **判定门槛**：先跑 `apply_physics_constraints` 看投影距离，>0 说明
  外推点被物理约束拉回可行域。

### 4. Conformal Active Learning（主动学习选点）
- **文件**：`engine/conformal_active_learning.py`
- **原理**：选点准则 = 该点 conformal 区间的**期望宽度**（大区间意味着
  不确定性大 → 优先采）。
- **优点**：比纯 UCB / EI 更**保守**——不会选到分布外点。
- **陷阱**：
  - 依赖 `proximity()` 守卫——主动学习先过 OOD 过滤，否则会把区间宽度
    最大的**分布外点**选进来（这是主动学习的经典陷阱）。
  - 本项目的守卫是 `generalization_bench.proximity()`，判据中位数。

### 5. Calibration RL（校准策略优化，辅助层）
- **文件**：`engine/calibration_rl.py`（407 行，2026-10-15 已接线）
- **原理**：借鉴 Laya/Jev RLCD，把 coverage 从**约束**升级为**训练目标**
  （奖励 = log-likelihood）。
- **优点**：让「覆盖率 0.95」变成可学习的目标，而非事后校准的约束。
- **陷阱**：
  - 数据 91/95 PASS 仍失衡（账本已含 3 MARGINAL + 1 REFUTED），本模块**当前只是「训练框架就位」的证据**；
  - 训练集现仍不均衡，ECE 偏低主要是分布内验证，不代表生产泛化能力；外推域（A15）一律不给出 PASS。
  - **不修改硬 verdict**——它产出元数据供报告/MCP 读。

---

## 三种典型失败模式（务必识别）

### F1: 外推点被 conformal「温柔地覆盖」
- **症状**：外推点也进了区间，覆盖率看着达标。
- **真相**：外推点被拉回训练集均值（GP 核函数在域外趋零的**定义性质**），
  区间宽度极窄。
- **判别**：`assess_applicability(roughness)` > 0.3 → 目标函数超出平滑代理
  适用域（见 `AXIOM_AUDIT.md` A14）。

### F2: 多模态函数被单一区间"平均化"
- **症状**：覆盖率不达标，区间很宽但没有意义。
- **真相**：保形是「单分布」假设，多模态函数（如双峰）需要**分层**保形
  或密度基方法。
- **本项目**：`MODALITY_MINIMA_CAP = 24`，禁用机理先验注入多模态场景。

### F3: 校准折与生产折不共享 mean_fn
- **症状**：本地跑通，部署到生产覆盖率崩。
- **真相**：`decide_mean_fn` 是两阶段门控——OOF 判 rmse、生产判 coverage，
  两阶段的 `mean_fn` **必须一致**，否则 calibration 分位数是「错标」的。
- **本项目的铁律**：conformal Q 标定折必须带**同** `mean_fn`。

---

## 判定口径（与 verification_gate 对齐）

| 门槛 | 阈值 | 触发 |
|---|---|---|
| 噪声地板 | 3%（不降） | 任何 coverage < 1−α−0.03 → REFUTED |
| 标称覆盖率 | ≈0.95（α=0.05） | 覆盖率偏离 >2% → MARGINAL |
| 相对 rmse | ≤ 场景阈值 | 超阈 → MARGINAL/REFUTED |
| 适用域 roughness | ≤ 0.3 | 超阈 → 声明超适用域 |

以上四条**全部通过**才判 PASS。任何一条失守 → 显式披露，不掩盖。

---

## 相关锚点

- **理论**：Vovk, Gammerman, Shafer (2005) *Algorithmic Learning in a Random World*
- **本项目实证**：`docs/research/generalization-bench-20261004.md`
- **公理审计**：`AXIOM_AUDIT.md` A13（两阶段门控）/ A14（适用域）/ A15（外推不可达）
- **CI 门禁**：`scripts/validate_conformal_cross.py`（跨实现在线一致性）

---

## 变更历史

- 2026-10-15: 初版。合并 5 个 conformal 变体的选择判据与三种失败模式。
  对应板块 03 保形 UQ P1 落地（补齐方法选择指引，避免使用者「凭感觉」选）。
