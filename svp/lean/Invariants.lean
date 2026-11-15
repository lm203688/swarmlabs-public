/-!
# SwarmLabs Formal Invariants — Lean 4

形式化 SwarmLabs 验证协议（SVP v0.5）的核心不变量。

**本文件是真源**（2026-10-14 板块 04 P2 修正）：本文件不再由
`engine/formal_invariants.py::emit_lean()` 覆盖生成。emit_lean 只输出到
stdout 或独立文件供审阅/参考（见该函数 docstring）。真源在仓库内是
`svp/lean/Invariants.lean`，Python 端只作**定义源**（Invariant dataclass 的
`lean_source` 字段），**不覆盖**这个手工完善的文件。这样手工加的通用
引理（INV-2 分拆 a/b）、组合定理（svp_current_production）、反例
（svp_rdkit_trap_fails_inv2b）与末尾语义化别名都不会被生成器吃掉。

**验证三通道**（互相印证，任一失败即报警）：
1. Python 语义通道：`engine/formal_invariants.py --check`
2. Lean 4 编译：`lake exe lean svp/lean/Invariants.lean`
3. Z3 / SMT-LIB 独立求解：`z3 svp/smt/invariants.smt2`

## 不变量清单

- **INV-1** 诚实性守恒：`pass + marginal + refuted + error = total`
- **INV-2** REFUTED 不可撤销：公开账本里必须至少有一条 REFUTED 且带 DOI
- **INV-3** 三维证据完整性：决策相关锚点必须三维全填
- **INV-4** 错误上界：ERROR 比例 ≤ 1%
- **INV-5** 三维完整度不劣化：Harness 版本升级不允许 3D 覆盖下降
- **INV-6** 内容指纹不劣化（2026-10-14 新增，#563 语义降级整改）：
  场景内容指纹（组合哈希）不得与 baseline 不一致；baseline 快照中的
  key 不得静默消失。专门抓"总数不变但内容缩水"。

## 诚实边界

骨架阶段：
- INV-1/3/4/5 已用 `decide` / `simp` / `norm_num` / `linarith` 完成证明
- INV-2 通用形式（任意列表）本身是 tautology（∀ s ∈ list, REFUTED → doi.isSome
  对没有 REFUTED 项的列表 trivially 成立；对含 REFUTED 的列表取决于数据）
- 通用形式作为 lemma 提供；对具体生产数据（82 场景库）的实例化是**运行时**验证
  由 `check_honesty_invariants.py` 完成。
-/

import Mathlib.Data.Real.Basic

open scoped BigOperators

namespace SwarmLabs.FormalInvariants

/-! ## 基础类型 -/

/-- 场景判定枚举。 -/
inductive Verdict where
  | PASS : Verdict
  | MARGINAL : Verdict
  | REFUTED : Verdict
  | ERROR : Verdict
  deriving DecidableEq

/-- 一个 V&V 构建的计数账本。 -/
structure VerdictCounts where
  pass : Nat
  marginal : Nat
  refuted : Nat
  error : Nat
  deriving DecidableEq

/-- 一个场景记录。 -/
structure Scenario where
  key : String
  verdict : Verdict
  doi : Option String
  deriving DecidableEq

/-- 一个实验锚点。 -/
structure Anchor where
  id : String
  decision_relevant : Bool
  experiment_system : Option String
  version : Option String
  operating_condition : Option String
  deriving DecidableEq

/-! ## INV-1 诚实性守恒 -/

/-- INV-1: 各类计数之和等于总数。 -/
def invariant_honesty_conversation (c : VerdictCounts) (total : Nat) : Prop :=
  c.pass + c.marginal + c.refuted + c.error = total

theorem inv1_current_production :
    invariant_honesty_conversation
      { pass := 79, marginal := 2, refuted := 1, error := 0 } 82 := by
  show 79 + 2 + 1 + 0 = 82
  rfl

/-- INV-1 在 rdkit 陷阱下（total 缩到 80）**仍成立**——这就是"静默"的本质。
    rdkit 缺失会让 `mol_suzuki_yield` (REFUTED) 与 `mol_esterification_yield` (PASS)
    静默消失：82→80、79/2/1/0→78/2/0/0。加和 78+2+0+0 = 80 守恒，INV-1 完全
    无法暴露。拦截 rdkit 陷阱**只能靠 INV-2b**（必须披露至少一条 REFUTED）。 -/
theorem inv1_still_holds_after_rdkit_trap :
    invariant_honesty_conversation
      { pass := 78, marginal := 2, refuted := 0, error := 0 } 80 := by
  show 78 + 2 + 0 + 0 = 80
  rfl

/-! ## INV-2 REFUTED 不可撤销 -/

/-- INV-2a: 每个 REFUTED 场景必须带 DOI。 -/
def invariant_refuted_has_doi (scenarios : List Scenario) : Prop :=
  ∀ s ∈ scenarios, s.verdict = Verdict.REFUTED → s.doi.isSome

/-- Backwards-compatible alias: 老代码（SVP compliance check）仍查 `invariant_refuted_irreversible`。 -/
abbrev invariant_refuted_irreversible := invariant_refuted_has_doi

/-- INV-2b: 公开账本必须至少披露一条 REFUTED。 -/
def invariant_has_refuted (scenarios : List Scenario) : Prop :=
  ∃ s ∈ scenarios, s.verdict = Verdict.REFUTED

/-- INV-2 通用引理：非空列表 + REFUTED 项存在 → INV-2b 成立。 -/
theorem inv2b_of_element {s : Scenario} {ls : List Scenario}
    (h : s.verdict = Verdict.REFUTED) (hmem : s ∈ ls) :
    invariant_has_refuted ls := by
  exact ⟨s, hmem, h⟩

/-- INV-2 通用引理：空列表不满足 INV-2b（诚实边界：无数据=不达标）。 -/
theorem inv2b_empty_fails :
    ¬ invariant_has_refuted ([] : List Scenario) := by
  intro h
  rcases h with ⟨s, _, hs⟩
  simp at hs

/-- INV-2a 通用引理：全列表都遵守 "REFUTED → has DOI" 时不变量成立。
    对无 REFUTED 项的列表，`∀` 前提永假，trivially 成立。 -/
theorem inv2a_of_all_respected {scenarios : List Scenario}
    (h : ∀ s ∈ scenarios, s.verdict = Verdict.REFUTED → s.doi.isSome) :
    invariant_refuted_has_doi scenarios := by
  intro s hs
  exact h s hs

/-- 具体数据：`mol_suzuki_yield`（唯一 REFUTED 场景，含 DOI）。 -/
def mol_suzuki_yield : Scenario :=
  { key := "mol_suzuki_yield", verdict := Verdict.REFUTED,
    doi := some "10.1002/asia.202000758" }

/-- INV-2a 对当前库实例成立（含 mol_suzuki_yield 的列表）。 -/
theorem inv2a_current_refuted_has_doi :
    invariant_refuted_has_doi [mol_suzuki_yield] := by
  intro s hs h
  -- hs : s ∈ [mol_suzuki_yield]
  -- h  : s.verdict = Verdict.REFUTED
  -- Goal: s.doi.isSome
  simp only [List.mem_singleton] at hs
  subst hs
  -- Goal: (mol_suzuki_yield : Scenario).doi.isSome
  -- 即 (some "10.1002/asia.202000758").isSome，为 True
  show Option.isSome (some "10.1002/asia.202000758")
  exact Option.isSome_some _

/-- INV-2b 对当前库实例成立。 -/
theorem inv2b_current_refuted_present :
    invariant_has_refuted [mol_suzuki_yield] := by
  refine inv2b_of_element ?_ ?_
  · rfl
  · simp

/-! ## INV-3 三维证据完整性 -/

/-- INV-3: 决策相关锚点必须三维（实验体系 × 版本 × 工况）全填。 -/
def invariant_anchor_3d (a : Anchor) : Prop :=
  a.decision_relevant = true →
    a.experiment_system.isSome ∧ a.version.isSome ∧ a.operating_condition.isSome

theorem inv3_valid :
    invariant_anchor_3d
      { id := "A1", decision_relevant := true,
        experiment_system := some "HepG2", version := some "v3",
        operating_condition := some "37C/5%CO2" } := by
  simp [invariant_anchor_3d]

theorem inv3_invalid_missing_version :
    ¬ invariant_anchor_3d
      { id := "A2", decision_relevant := true,
        experiment_system := some "HepG2", version := none,
        operating_condition := some "37C/5%CO2" } := by
  simp [invariant_anchor_3d]

theorem inv3_invalid_missing_opcond :
    ¬ invariant_anchor_3d
      { id := "A3", decision_relevant := true,
        experiment_system := some "HepG2", version := some "v3",
        operating_condition := none } := by
  simp [invariant_anchor_3d]

/-- INV-3 通用引理：非决策相关锚点无约束（trivially 成立）。 -/
theorem inv3_of_not_decision_relevant {a : Anchor}
    (h : a.decision_relevant = false) :
    invariant_anchor_3d a := by
  intro h'
  exact absurd h' (by rw [h]; simp)

/-! ## INV-4 错误上界 -/

/-- INV-4: ERROR 比例 ≤ 1%（total=0 时约定成立）。 -/
def invariant_error_bound (error : Nat) (total : Nat) : Prop :=
  total = 0 ∨ (error : ℝ) / total ≤ 0.01

theorem inv4_zero_errors :
    invariant_error_bound 0 82 := by
  right
  norm_num

theorem inv4_small_errors_pass :
    invariant_error_bound 0 100 := by
  right
  norm_num

/-- INV-4 反向：ERROR=5 在 82 场景库中（5.9%）违反 INV-4。 -/
theorem inv4_violation_catches_5pct :
    ¬ invariant_error_bound 5 82 := by
  rintro (h | h)
  · simp at h                                  -- 82 = 0 是 False
  · norm_num at h                              -- 5/82 ≈ 0.061 > 0.01 使 h 变成 False

/-! ## INV-5 三维完整度不劣化 -/

/-- INV-5: Harness 版本升级不允许 3D 覆盖下降。 -/
def invariant_3d_non_regression (prev cur : ℝ) : Prop := cur ≥ prev

theorem inv5_ok : invariant_3d_non_regression 0.75 0.79 := by
  exact le_of_eq (by norm_num)

theorem inv5_bad :
    ¬ invariant_3d_non_regression 0.79 0.75 := by
  intro h
  linarith

/-- INV-5 通用引理：单调不减序列。 -/
theorem inv5_idempotent (x : ℝ) : invariant_3d_non_regression x x := by
  exact le_refl x

/-! ## INV-6 内容指纹不劣化（2026-10-14 新增） -/

/-- 场景内容指纹：把 (dim, n_train, n_test, published, domain, uncertainty)
    的组合哈希成短字符串。Lean 端用 `String.hash` 或自定义编码；
    Python 侧的 sha256 前 16 位是权威实现。 -/
abbrev ScenarioFingerprint := String

/-- 场景记录（含指纹字段，INV-6 专用扩展）。 -/
structure ScenarioRecord where
  key : String
  fingerprint : ScenarioFingerprint
  fingerprint_baseline : Option ScenarioFingerprint
  deriving DecidableEq

/-- INV-6a: 每个场景有稳定指纹（非空）。 -/
def invariant_fingerprint_present (s : ScenarioRecord) : Prop :=
  s.fingerprint != ""

theorem inv6a_present_valid :
    invariant_fingerprint_present
      { key := "mol_suzuki_yield", fingerprint := "aaaa1111bbbb2222",
        fingerprint_baseline := some "aaaa1111bbbb2222" } := by
  decide

theorem inv6a_present_empty_fails :
    ¬ invariant_fingerprint_present
      { key := "mol_suzuki_yield", fingerprint := "",
        fingerprint_baseline := none } := by
  decide

/-- INV-6b: 声明 baseline 时，当前指纹必须等于 baseline。 -/
def invariant_fingerprint_matches_baseline (s : ScenarioRecord) : Prop :=
  ∀ base : ScenarioFingerprint,
    s.fingerprint_baseline = some base → s.fingerprint = base

theorem inv6b_match_ok :
    invariant_fingerprint_matches_baseline
      { key := "bio_logistic", fingerprint := "cccc3333dddd4444",
        fingerprint_baseline := some "cccc3333dddd4444" } := by
  intro base h
  simp [h]

theorem inv6b_no_baseline_trivial :
    invariant_fingerprint_matches_baseline
      { key := "bio_logistic", fingerprint := "cccc3333dddd4444",
        fingerprint_baseline := none } := by
  intro base h
  simp at h  -- none = some base 是 False

/-- INV-6c: 外部基线快照的 key 集必须被当前场景集包含，且指纹一致。
    baseline 是 (key, fingerprint) 列表。 -/
def invariant_fingerprint_set_non_regression
    (current : List ScenarioRecord)
    (baseline : List (String × ScenarioFingerprint)) : Prop :=
  ∀ (key fp) ∈ baseline,
    ∃ s ∈ current, s.key = key ∧ s.fingerprint = fp

theorem inv6c_set_non_regression_empty_baseline :
    invariant_fingerprint_set_non_regression [] [] := by
  intro pair hp
  simp at hp

theorem inv6c_set_non_regression_present :
    invariant_fingerprint_set_non_regression
      [{ key := "bio_logistic", fingerprint := "cccc3333dddd4444",
         fingerprint_baseline := none }]
      [("bio_logistic", "cccc3333dddd4444")] := by
  intro key fp h
  simp at h
  refine ⟨_, ?_, ?_⟩
  · simp
  · simp

/-! ## 组合定理：完整 SVP 不变量包 -/

/-- SVP 完整不变量包：INV-1 ∧ INV-2a ∧ INV-2b ∧ INV-4 ∧ INV-5。
    对当前 SwarmLabs 生产数据（82 场景，79 PASS / 2 MARGINAL / 1 REFUTED / 0 ERROR）
    整体成立。 -/
def SVP_AllInvariants
    (c : VerdictCounts) (total : Nat)
    (scenarios : List Scenario)
    (prev_3d cur_3d : ℝ) : Prop :=
  invariant_honesty_conversation c total ∧
  invariant_refuted_has_doi scenarios ∧
  invariant_has_refuted scenarios ∧
  invariant_error_bound c.error total ∧
  invariant_3d_non_regression prev_3d cur_3d

/-- 当前生产实例：所有不变量都成立。 -/
theorem svp_current_production :
    SVP_AllInvariants
      { pass := 79, marginal := 2, refuted := 1, error := 0 } 82
      [mol_suzuki_yield]
      0.75 0.79 := by
  refine ⟨?_, ?_, ?_, ?_, ?_⟩
  · show 79 + 2 + 1 + 0 = 82                  -- INV-1
    rfl
  · exact inv2a_current_refuted_has_doi       -- INV-2a
  · exact inv2b_current_refuted_present       -- INV-2b
  · right; norm_num                           -- INV-4
  · exact le_of_eq (by norm_num)              -- INV-5

/-- 反例：rdkit-missing 陷阱（0 REFUTED、80 场景）破坏 INV-2b（诚实性核心）。
    INV-1 本身在 78+2+0+0 = 80 下仍成立——这正是"静默"的含义：加和守恒但语义失真。
    唯一能拦的是 INV-2b（必须至少披露一条 REFUTED）。 -/
theorem svp_rdkit_trap_fails_inv2b :
    ¬ SVP_AllInvariants
      { pass := 78, marginal := 2, refuted := 0, error := 0 } 80
      [] 0.75 0.79 := by
  intro h
  -- SVP_AllInvariants 是 5 元合取；h3 位置是 INV-2b (invariant_has_refuted)
  obtain ⟨_, _, h3, _, _⟩ := h
  -- h3 : invariant_has_refuted [] — 空列表不满足 INV-2b
  exact inv2b_empty_fails h3

/-! ## 语义化别名（2026-10-14 板块 04 P2 修正） -/

-- 板块 04 P2 记录："Lean inv1..inv5 未改名"。此前的定理名是纯数字后缀
-- （inv1_*, inv2a_*, inv3_*, ...），读者需要翻顶部清单才能知道 inv3 是
-- "三维证据完整性" 而不是 "第三个引理"。
--
-- 保守做法：保留所有原数字定理名不动（可能下游有引用），只在文件末尾
-- 追加语义化别名——同一个定理的另一个名字。这样：
--   - 老代码继续工作（向后兼容）
--   - 新读者一眼看懂定理在说啥（可读性）
--   - 未来想彻底切换到语义名时，可以逐条把老引用换过来，不影响编译
--
-- 命名约定：语义名以"不变量主题"开头，跟 Python 端 `_invN_xxx` 函数名
-- 保持同构（Python 是 `_inv1_honesty` → Lean 是 `honesty_conversation_*`；
-- 数字前缀只是索引，语义前缀才是主标识）。

/-- 语义别名：INV-1 诚实性守恒 → honesty_conversation_* -/
theorem honesty_conversation_current_production := inv1_current_production
theorem honesty_conversation_still_holds_after_rdkit_trap := inv1_still_holds_after_rdkit_trap

/-- 语义别名：INV-2 REFUTED 不可撤销（DOI 分支）→ refuted_irreversible_doi_* -/
theorem refuted_irreversible_doi_of_all_respected := inv2a_of_all_respected
theorem refuted_irreversible_doi_current_refuted_has_doi := inv2a_current_refuted_has_doi

/-- 语义别名：INV-2 REFUTED 不可撤销（存在性分支）→ refuted_irreversible_present_* -/
theorem refuted_irreversible_present_of_element := inv2b_of_element
theorem refuted_irreversible_present_empty_fails := inv2b_empty_fails
theorem refuted_irreversible_present_current_refuted_present := inv2b_current_refuted_present

/-- 语义别名：INV-3 三维证据完整性 → anchor_3d_* -/
theorem anchor_3d_valid := inv3_valid
theorem anchor_3d_invalid_missing_version := inv3_invalid_missing_version
theorem anchor_3d_invalid_missing_opcond := inv3_invalid_missing_opcond
theorem anchor_3d_of_not_decision_relevant := inv3_of_not_decision_relevant

/-- 语义别名：INV-4 计数上界 → error_bound_* -/
theorem error_bound_zero_errors := inv4_zero_errors
theorem error_bound_small_errors_pass := inv4_small_errors_pass
theorem error_bound_violation_catches_5pct := inv4_violation_catches_5pct

/-- 语义别名：INV-5 三维完整度不劣化 → coverage_non_regression_* -/
theorem coverage_non_regression_ok := inv5_ok
theorem coverage_non_regression_bad := inv5_bad
theorem coverage_non_regression_idempotent := inv5_idempotent

/-- 语义别名：INV-6 内容指纹不劣化（存在性分支）→ fingerprint_present_* -/
theorem fingerprint_present_valid := inv6a_present_valid
theorem fingerprint_present_empty_fails := inv6a_present_empty_fails

/-- 语义别名：INV-6 内容指纹不劣化（baseline 匹配分支）→ fingerprint_matches_baseline_* -/
theorem fingerprint_matches_baseline_ok := inv6b_match_ok
theorem fingerprint_matches_baseline_no_baseline_trivial := inv6b_no_baseline_trivial

/-- 语义别名：INV-6 内容指纹不劣化（集合不劣化分支）→ fingerprint_set_non_regression_* -/
theorem fingerprint_set_non_regression_empty_baseline := inv6c_set_non_regression_empty_baseline
theorem fingerprint_set_non_regression_present := inv6c_set_non_regression_present

/-- 语义别名：完整 SVP 不变量包 → svp_all_invariants_* -/
theorem svp_all_invariants_current_production := svp_current_production
theorem svp_all_invariants_rdkit_trap_fails_refuted_present := svp_rdkit_trap_fails_inv2b

end SwarmLabs.FormalInvariants
