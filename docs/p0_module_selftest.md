# P0 模块自测文档 · 三大孤儿模块接线后

**生成日期**：2026-10-14
**范围**：`engine/digital_twin_lab.py` / `engine/physics_constrained_conformal.py` / `engine/spectral_guard.py`
**前置**：这三者均已接入 `engine/__init__.py`（板块 03/06/07 P0），Agent 经 `engine.X` 检索可直接 import。

---

## 1. `engine/digital_twin_lab.py` — 数字孪生湿实验适配器（561 行）

**定位**：让主动学习闭环在**无物理机器人**的条件下端到端跑通——用 95 场景的解析模型作数字孪生湿实验环境。

**导出符号**（在 `engine/__init__.py` 里）：
- `DigitalTwinScenario`（dataclass）
- `TwinRunResult`（dataclass）
- `run_twin_loop(scenario, strategy, n_iter, seed)`
- `compare_strategies(strategies, ...)`
- `run_all_twins(scenarios, strategies, ...)`

**核心 API**：
```python
from engine import run_twin_loop, run_all_twins
result = run_twin_loop(scenario_key="micro_monod", strategy="balb", n_iter=15)
print(result.final_r2, result.uq_coverage)
```

**自测**：
```bash
python -c "from engine.digital_twin_lab import _selftest; print(_selftest())"
# 期望输出 True
```

**诚实边界**：
- 数字孪生 ≠ 真实湿实验。解析模型没有仪器噪声、没有批次间变异。
- 本模块明确标注为 `simulated-digital-twin`，不宣称是湿实验数据。
- 与真实湿实验（Nature 2026 self-driving labs / AI4AutoSci）的关系是"桥梁"，不是替代。

---

## 2. `engine/physics_constrained_conformal.py` — 物理约束 conformal（412 行）

**定位**：外推时注入物理先验，让 conformal 预测区间在越出训练域时依然有意义。这是"外推压力测试 73/95 REFUTED"的直接对症药。

**导出符号**：
- `PhysicsConstraint`（dataclass：constraint_id, description, function, source_ref）
- `PhysicsConformalResult`（dataclass）
- `apply_physics_constraints(predictions, intervals, constraints)`
- `physics_constrained_conformal(data, base_intervals, constraints, alpha)`
- `validate_pcc(scenarios, ...)` （在 `engine/__init__.py` 中改名为 `validate_pcc` 避免与 `validate_scenario` 撞名）

**核心 API**：
```python
from engine import PhysicsConstraint, physics_constrained_conformal

# Monod 生长的物理约束：速率不能为负
constraints = [PhysicsConstraint(
    constraint_id="monod_rate_nonneg",
    description="Monod rate >= 0",
    function=lambda x: x >= 0,
    source_ref="Monod 1949",
)]
result = physics_constrained_conformal(
    data=X_test, base_intervals=conformal_intervals,
    constraints=constraints, alpha=0.10
)
print(result.sharpness_ratio)  # < 1.0 表示物理约束收紧了区间
```

**自测**（内置 18 项检查）：
```bash
python -c "from engine.physics_constrained_conformal import _selftest; print(_selftest())"
# 期望输出 True
```

**诚实边界**：
- 物理约束的**来源**必须是已发表的物理定律或经验公式，不能是任意先验。
- 本模块**不**自动发现约束，只提供"注入已声明约束"的机制。
- 约束越宽，`sharpness_ratio` 越接近 1.0（等于没约束）；约束越窄越能收紧区间但可能过度约束真实值。

---

## 3. `engine/spectral_guard.py` — 频谱溯源护栏（360 行）

**定位**：在数据流里检测"参数被跨场景偷换"的攻击面。arXiv:2608.20439 的跨参数重连攻击对 SHA256 校验是隐身的，但频谱指纹能捕获。

**导出符号**：
- `SpectralProvenanceGuard`（类：`diagnose`, `verify_against_reference`, `fingerprint_spectrum`）
- `spectral_diagnose(output_data, output_grid, expected_frequencies, tolerance_ratio)`（函数式 API）

**核心 API**：
```python
from engine import SpectralProvenanceGuard

guard = SpectralProvenanceGuard(tolerance_ratio=1.05)

# 场景 1：诊断输出是否包含预期频率
result = guard.diagnose(output_data=u, output_grid=x,
                        expected_frequencies=[2.0, 6.0])
assert result["violation_count"] == 0  # 0 = 频谱正确

# 场景 2：与参考信号对比
result = guard.verify_against_reference(pred, x_pred, ref, x_ref)

# 场景 3：稳定指纹（抗小噪声）
fp_clean = guard.fingerprint_spectrum(u_clean, x)
fp_noisy = guard.fingerprint_spectrum(u_clean + 0.001 * noise, x)
assert fp_clean == fp_noisy  # 6 位有效数字下的稳定性
```

**自测**（内置 5 项检查，通过 `python -m` 方式跑）：
```bash
python engine/spectral_guard.py
# 期望输出 "All spectral guard tests PASSED"
```

**诚实边界**：
- 频谱指纹**替代不了** SHA256。SHA256 是字节级完整性校验，频谱指纹是内容级溯源。
- 两者必须**同时用**：SHA256 拦任意 bit 修改，频谱指纹拦跨参数偷换（bit 未变但语义变）。
- 本模块**不覆盖**跨场景 ID 混淆（那是 provenance_guard 的职责）。

---

## 4. 统一自测命令

```bash
# 一键跑全部三个 P0 模块的自测
python -c "
from engine.digital_twin_lab import _selftest as s1
from engine.physics_constrained_conformal import _selftest as s2
import subprocess
r = subprocess.run(['python', 'engine/spectral_guard.py'], capture_output=True, text=True)
assert s1(), 'digital_twin_lab selftest FAILED'
assert s2(), 'physics_constrained_conformal selftest FAILED'
assert r.returncode == 0 and 'PASSED' in r.stdout, 'spectral_guard selftest FAILED'
print('All 3 P0 modules selftest PASSED')
"
```

---

## 5. 接线状态

| 模块 | 行数 | 接入 `engine/__init__.py` | 顶层可 import | `_selftest` 存在 |
|---|---|---|---|---|
| `digital_twin_lab` | 561 | ✅ | ✅ 5 个符号 | ✅ |
| `physics_constrained_conformal` | 412 | ✅ | ✅ 5 个符号 | ✅ |
| `spectral_guard` | 360 | ✅ | ✅ 2 个符号 | ✅ (CLI) |

**共同点**：三者纯 stdlib / numpy，import 无副作用、无文件 IO，可安全顶层导入。
