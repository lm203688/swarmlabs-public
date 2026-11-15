"""SwarmLabs 统一内核包 (engine)。

整个项目融合后的唯一真源。子模块：
  - surrogate      : GPSurrogate / UQ / 主动学习 + SurrogateEngine 门面
  - guard          : VirtualLab Guard（三态门禁）+ PhysicsNeMo 科学护栏（四层）
  - time_machine   : Experiment Time Machine（快照 + 回放）
  - sdk            : VirtualExperiment（统一四算子）
  - federated      : Federated Evidence（跨域证据检索）
  - emukit_adapter : Emukit 兼容接口 + 敏感性分析 + 贝叶斯优化（纯 numpy）
  - brand          : Self-Evolving Engine 品牌命名
  - domains        : 领域 taxonomy（按使用场景组织）
  - validation_mesh: PARITY 验证独立 wheel

⚡ 惰性加载（2026-10-17 P2）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
原先 `import engine` 会 eager 拉入 60+ 个子模块（含 numpy/scipy/torch 依赖链），
冷启动实测 10+ 秒，且任意子模块的顶层副作用都会泄漏到全局。现在通过
PEP 562 `__getattr__` 把每个符号按需解析：
  * `import engine` 本身 <0.1 秒
  * `engine.GPSurrogate` 首次访问时才 import `engine.surrogate`
  * 首次解析后写入 `globals()` 缓存，后续访问 O(1)
  * 保持向后兼容：`from engine import GPSurrogate` / `engine.GPSurrogate` /
    `dir(engine)` / `help(engine)` 全部照旧
  * `__all__` 仍完整列出所有导出符号，供工具链 (tool_ocean / docs 生成) 使用

⚠️ 刻意保持的非惰性导入：无。所有子模块都走 lazy 通道，只有本 __init__ 本身
执行时是零导入。这与 engine/flywheel 的死锁防线兼容——本 __init__ 完全不会再
主动 import 编排层，因此 `from engine.flywheel import ...` 的用法不受影响。
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys
import threading as _threading
from typing import Any


# ---------------------------------------------------------------------------
# 惰性导出表：export_name -> (submodule, name_in_submodule)
#   * submodule 用 relative dotted path（不带 `engine.` 前缀）
#   * name_in_submodule 是子模块里的实际标识符；有 `as` 重命名时二者不同
# ---------------------------------------------------------------------------
# 顺序刻意与旧 __init__ 一致，便于 diff review；不影响功能。

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # ── surrogate 门面 ──
    "ExperimentSpace":        ("surrogate", "ExperimentSpace"),
    "GPSurrogate":            ("surrogate", "GPSurrogate"),
    "NeuralOperatorSurrogate": ("surrogate", "NeuralOperatorSurrogate"),
    "SyntheticOracle":        ("surrogate", "SyntheticOracle"),
    "RemoteLabOracle":        ("surrogate", "RemoteLabOracle"),
    "Calibrator":             ("surrogate", "Calibrator"),
    "ActiveLearner":          ("surrogate", "ActiveLearner"),
    "run_parity_loop":        ("surrogate", "run_parity_loop"),
    "mirror_run":             ("surrogate", "mirror_run"),
    "SurrogateEngine":        ("surrogate", "SurrogateEngine"),

    # ── guard（三态门禁 + PhysicsNeMo 科学护栏）──
    "VirtualLabGuard":                  ("guard", "VirtualLabGuard"),
    "GateResult":                       ("guard", "GateResult"),
    "GateStatus":                       ("guard", "GateStatus"),
    "UQGate":                           ("guard", "UQGate"),
    "PhysicalConsistencyGate":          ("guard", "PhysicalConsistencyGate"),
    "AleatoricEpistemicDecomposer":     ("guard", "AleatoricEpistemicDecomposer"),
    "EnsembleGuard":                    ("guard", "EnsembleGuard"),

    # ── time machine ──
    "SnapshotService": ("time_machine", "SnapshotService"),
    "time_travel":     ("time_machine", "time_travel"),
    "capture":         ("time_machine", "capture"),
    "list_runs":       ("time_machine", "list_runs"),
    "diff":            ("time_machine", "diff"),

    # ── sdk ──
    "VirtualExperiment": ("sdk", "VirtualExperiment"),

    # ── federated ──
    "federated_evidence": ("federated_evidence", "federated_evidence"),

    # ── validation_mesh ──
    "validate_scenario": ("validation_mesh", "validate_scenario"),
    "validate_batch":    ("validation_mesh", "validate_batch"),
    "compare_runs":      ("validation_mesh", "compare_runs"),
    "audit_log":         ("validation_mesh", "audit_log"),
    "write_audit":       ("validation_mesh", "write_audit"),
    "validate_full":     ("validation_mesh", "validate_full"),

    # ── entity_shard ──
    "load_full":             ("entity_shard", "load_full"),
    "load_works_by_bucket":  ("entity_shard", "load_works_by_bucket"),
    "load_concepts":         ("entity_shard", "load_concepts"),
    "load_insights":         ("entity_shard", "load_insights"),
    "get_bucket_list":       ("entity_shard", "get_bucket_list"),
    "split_store":           ("entity_shard", "split_store"),
    "shard_summary":         ("entity_shard", "shard_summary"),
    "search_all":            ("entity_shard", "search_all"),
    "get_total_counts":      ("entity_shard", "get_total_counts"),

    # ── emukit_adapter ──
    "EmukitSurrogate":        ("emukit_adapter", "EmukitSurrogate"),
    "EmukitAnalytics":        ("emukit_adapter", "EmukitAnalytics"),
    "sobol_sensitivity":      ("emukit_adapter", "sobol_sensitivity"),
    "expected_improvement":   ("emukit_adapter", "expected_improvement"),
    "bayesian_optimize":      ("emukit_adapter", "bayesian_optimize"),
    "has_emukit":             ("emukit_adapter", "has_emukit"),

    # ── 涨落定理凸体借鉴谱系（纯 numpy，不依赖 scipy）──
    "MomentBoundGate":          ("moment_bound", "MomentBoundGate"),
    "MomentBoundResult":        ("moment_bound", "MomentBoundResult"),
    "cantelli_coverage_lb":     ("moment_bound", "cantelli_coverage_lb"),
    "bhatia_davis_reachable":   ("moment_bound", "bhatia_davis_reachable"),
    "bhatia_davis_mean_bounds": ("moment_bound", "bhatia_davis_mean_bounds"),

    "BidirectionalParityGate": ("bidirectional_parity", "BidirectionalParityGate"),
    "ParityResult":            ("bidirectional_parity", "ParityResult"),
    "ParityStatus":            ("bidirectional_parity", "ParityStatus"),

    "SecondLawGate":           ("second_law", "SecondLawGate"),
    "SecondLawResult":         ("second_law", "SecondLawResult"),
    "entropy_production":      ("second_law", "entropy_production"),

    "ValidationBody":      ("validation_body", "ValidationBody"),
    "ValidationBodyGate":  ("validation_body", "ValidationBodyGate"),
    "Indicator":           ("validation_body", "Indicator"),

    # ── microbiology（微生物实验完整引擎 38 场景）──
    "PAPER_PARAMS":                  ("microbiology_expanded", "PAPER_PARAMS"),
    "specific_growth_rate":          ("microbiology_expanded", "specific_growth_rate"),
    "batch_simulation":              ("microbiology_expanded", "batch_simulation"),
    "chemostat_steady_state":        ("microbiology_expanded", "chemostat_steady_state"),
    "build_expanded_scenarios":      ("microbiology_expanded", "build_expanded_scenarios"),
    "validate_all_papers":           ("microbiology_expanded", "validate_all_papers"),
    "demo_batch_simulation":         ("microbiology_expanded", "demo_batch_simulation"),

    "build_full_scenarios":              ("microbiology_full", "build_full_scenarios"),
    "coverage_report":                   ("microbiology_full", "coverage_report"),
    "validate_full_scenarios":           ("microbiology_full", "validate_full_scenarios"),
    "lag_phase_model":                   ("microbiology_full", "lag_phase_model"),
    "smooth_lifecycle":                  ("microbiology_full", "smooth_lifecycle"),
    "diauxic_growth":                    ("microbiology_full", "diauxic_growth"),
    "fed_batch_simulation":              ("microbiology_full", "fed_batch_simulation"),
    "multi_species_competition_smooth":  ("microbiology_full", "multi_species_competition_smooth"),
    "crabtree_effect":                   ("microbiology_full", "crabtree_effect"),
    "haldane_inhibition":                ("microbiology_full", "haldane_inhibition"),
    "contois_growth":                    ("microbiology_full", "contois_growth"),
    "tessier_growth":                    ("microbiology_full", "tessier_growth"),
    "pirt_maintenance":                  ("microbiology_full", "pirt_maintenance"),
    "oxygen_limitation_growth":          ("microbiology_full", "oxygen_limitation_growth"),
    "antibiotic_kill":                   ("microbiology_full", "antibiotic_kill"),
    "osmotic_stress_growth":             ("microbiology_full", "osmotic_stress_growth"),
    "biofilm_growth":                    ("microbiology_full", "biofilm_growth"),
    "scale_up_kla":                      ("microbiology_full", "scale_up_kla"),
    "in_silico_doe":                     ("microbiology_full", "in_silico_doe"),
    "parameter_estimation_MLE":          ("microbiology_full", "parameter_estimation_MLE"),
    "micro_sobol_sensitivity":           ("microbiology_full", "sobol_sensitivity"),  # alias
    "uncertainty_quantification":        ("microbiology_full", "uncertainty_quantification"),
    "smooth_secondary_metabolite":       ("microbiology_full", "smooth_secondary_metabolite"),
    "smooth_thermal_death":              ("microbiology_full", "smooth_thermal_death"),
    "smooth_immobilized_cell":           ("microbiology_full", "smooth_immobilized_cell"),
    "smooth_plasmid_stability":          ("microbiology_full", "smooth_plasmid_stability"),
    "smooth_phage_infection":            ("microbiology_full", "smooth_phage_infection"),
    "smooth_gene_expression":            ("microbiology_full", "smooth_gene_expression"),
    "smooth_oxidative_stress":           ("microbiology_full", "smooth_oxidative_stress"),
    "smooth_chemostat_washout":          ("microbiology_full", "smooth_chemostat_washout"),
    "smooth_mixed_substrate":            ("microbiology_full", "smooth_mixed_substrate"),
    "smooth_ph_control":                 ("microbiology_full", "smooth_ph_control"),
    "smooth_fba_flux":                   ("microbiology_full", "smooth_fba_flux"),
    "smooth_dead_volume":                ("microbiology_full", "smooth_dead_volume"),
    "smooth_foam_dynamics":              ("microbiology_full", "smooth_foam_dynamics"),
    "smooth_sensor_fusion":              ("microbiology_full", "smooth_sensor_fusion"),

    # ── brand（模块本体作为符号导出）──
    "brand": ("brand", "_SELF"),

    # ── surrogate 的依赖基座 ──
    "SINDy":             ("discovery", "SINDy"),
    "SINDySelect":       ("discovery", "SINDySelect"),
    "EloTournament":     ("elo", "EloTournament"),
    "FourierOperatorSurrogate":  ("neural_operator", "FourierOperatorSurrogate"),
    "make_burgers_dataset":      ("neural_operator", "make_burgers_dataset"),
    "DeepONetSurrogate":         ("neural_operator", "DeepONetSurrogate"),
    "FourierNeuralOperator":     ("fourier_operator", "FourierNeuralOperator"),
    "PINN1D":                    ("pinn", "PINN1D"),
    "ConvexBodyAcquisition":     ("acquisition_volume", "ConvexBodyAcquisition"),
    "LLMHypothesizer":           ("hypothesis_llm", "LLMHypothesizer"),
    "DEFAULT_BASE":              ("hypothesis_llm", "DEFAULT_BASE"),
    "DEFAULT_MODEL":             ("hypothesis_llm", "DEFAULT_MODEL"),
    "HypothesisTree":            ("tree_search", "HypothesisTree"),
    "ExperimentSpec":            ("remote_lab", "ExperimentSpec"),
    "DatasetOracle":             ("remote_lab", "DatasetOracle"),
    "LocalLabSimulator":         ("remote_lab", "LocalLabSimulator"),
    "RemoteLabClient":           ("remote_lab", "RemoteLabClient"),
    "SCENARIOS_EXTRA":           ("scenarios_extra", "SCENARIOS_EXTRA"),
    "COVERAGE_PASS":             ("scenarios_extra", "COVERAGE_PASS"),
    "run_extra_validation":      ("scenarios_extra", "run_extra_validation"),
    "run_all_benchmarks_and_export": ("validation_db", "run_all_benchmarks_and_export"),

    # ── strain_registry / media_database ──
    "get_strain_registry":       ("strain_registry", "get_strain_registry"),
    "get_strain_registry_path":  ("strain_registry", "get_strain_registry_path"),
    "export_registry":           ("strain_registry", "export_registry"),
    "get_strain":                ("strain_registry", "get_strain"),
    "search_strains":            ("strain_registry", "search_strains"),
    "search_by_substrate":       ("strain_registry", "search_by_substrate"),
    "search_by_phylum":          ("strain_registry", "search_by_phylum"),
    "get_registry_params":       ("strain_registry", "get_all_params"),  # alias
    "registry_summary":          ("strain_registry", "registry_summary"),
    "STRAIN_REGISTRY":           ("strain_registry", "STRAINS"),           # alias
    "STRAIN_SCHEMA":             ("strain_registry", "STRAIN_SCHEMA"),

    "get_media_registry":        ("media_database", "get_media_registry"),
    "get_media_registry_path":   ("media_database", "get_media_registry_path"),
    "export_media_database":     ("media_database", "export_media_database"),
    "get_media":                 ("media_database", "get_media"),
    "get_strain_media":          ("media_database", "get_strain_media"),
    "media_summary":             ("media_database", "media_summary"),
    "MEDIA_DATABASE":            ("media_database", "MEDIA"),               # alias
    "MEDIA_SCHEMA":              ("media_database", "MEDIA_SCHEMA"),

    # ── instrument_simulator ──
    "PlateReaderSimulator":      ("instrument_simulator", "PlateReaderSimulator"),
    "DOProbeSimulator":          ("instrument_simulator", "DOProbeSimulator"),
    "PHProbeSimulator":          ("instrument_simulator", "PHProbeSimulator"),
    "TempProbeSimulator":        ("instrument_simulator", "TempProbeSimulator"),
    "CO2FlowSimulator":          ("instrument_simulator", "CO2FlowSimulator"),
    "DCWAnalyzerSimulator":      ("instrument_simulator", "DCWAnalyzerSimulator"),
    "full_experiment_simulation": ("instrument_simulator", "full_experiment_simulation"),

    # ── baybe_adapter ──
    "baybe_optimize":         ("baybe_adapter", "baybe_optimize"),
    "baybe_searchspace":      ("baybe_adapter", "baybe_searchspace"),
    "baybe_run_optimization": ("baybe_adapter", "baybe_run_optimization"),
    "MultiFidelityOptimizer": ("baybe_adapter", "MultiFidelityOptimizer"),
    "has_baybe":              ("baybe_adapter", "has_baybe"),
    "baybe_version":          ("baybe_adapter", "baybe_version"),

    # ── 核心验证三件套 ──
    "Invariant":       ("formal_invariants", "Invariant"),
    "INVARIANTS":      ("formal_invariants", "INVARIANTS"),
    "CheckResult":     ("formal_invariants", "CheckResult"),
    "check_all":       ("formal_invariants", "check_all"),
    "all_pass":        ("formal_invariants", "all_pass"),
    "emit_lean":       ("formal_invariants", "emit_lean"),
    "emit_smt":        ("formal_invariants", "emit_smt"),

    "NOISE_FLOOR":       ("verification_gate", "NOISE_FLOOR"),
    "NOMINAL_COVERAGE":  ("verification_gate", "NOMINAL_COVERAGE"),
    "COVERAGE_MARGINAL": ("verification_gate", "COVERAGE_MARGINAL"),
    "KAPPA_CAP":         ("verification_gate", "KAPPA_CAP"),
    "GRADE_PASS":        ("verification_gate", "GRADE_PASS"),
    "GRADE_MARGINAL":    ("verification_gate", "GRADE_MARGINAL"),
    "GRADE_REFUTED":     ("verification_gate", "GRADE_REFUTED"),
    "GRADE_ERROR":       ("verification_gate", "GRADE_ERROR"),
    "GATE_PROCEED":      ("verification_gate", "GATE_PROCEED"),
    "GATE_HUMAN":        ("verification_gate", "GATE_HUMAN"),
    "GATE_BLOCK":        ("verification_gate", "GATE_BLOCK"),
    "GATE_MAP":          ("verification_gate", "GATE_MAP"),
    "ActionBlocked":     ("verification_gate", "ActionBlocked"),
    "gate_for_verdict":  ("verification_gate", "gate_for_verdict"),
    "gate_label":        ("verification_gate", "gate_label"),
    "policy":            ("verification_gate", "policy"),
    "thresholds":        ("verification_gate", "thresholds"),
    "ledger_entry":      ("verification_gate", "ledger_entry"),
    "load_ledger":       ("verification_gate", "load_ledger"),

    "ALPHA_LEVELS":       ("conformal_vv", "ALPHA_LEVELS"),
    "AlphaLevelResult":   ("conformal_vv", "AlphaLevelResult"),
    "ConfidenceProfile":  ("conformal_vv", "ConfidenceProfile"),
    "build_confidence_profile": ("conformal_vv", "build_confidence_profile"),
    "all_profiles":       ("conformal_vv", "all_profiles"),
    "rank_profiles":      ("conformal_vv", "rank_profiles"),
    "summarize_grades":   ("conformal_vv", "summarize_grades"),

    # ── 自进化层（harness_evolver + harness_registry）──
    "Feedback":         ("harness_evolver", "Feedback"),
    "Gate":             ("harness_evolver", "Gate"),
    "ScenarioResult":   ("harness_evolver", "ScenarioResult"),
    "Harness":          ("harness_evolver", "Harness"),
    "Candidate":        ("harness_evolver", "Candidate"),
    "Decision":         ("harness_evolver", "Decision"),
    "EvolutionStep":    ("harness_evolver", "EvolutionStep"),
    "HarnessEvolver":   ("harness_evolver", "HarnessEvolver"),
    "evaluate_harness": ("harness_evolver", "evaluate_harness"),
    "decide":           ("harness_evolver", "decide"),
    "apply_candidate":  ("harness_evolver", "apply_candidate"),
    "make_gate":        ("harness_evolver", "make_gate"),
    "from_current_system": ("harness_evolver", "from_current_system"),

    "get_gate":            ("harness_registry", "get_gate"),
    "all_gates":           ("harness_registry", "all_gates"),
    "registered_ids":      ("harness_registry", "registered_ids"),
    "harness_registry_summary": ("harness_registry", "summary"),  # alias

    # ── 证据锚定（3D 锚点）──
    "Anchor":             ("wet_lab_anchors", "Anchor"),
    "DIM_UNANCHORED":     ("wet_lab_anchors", "DIM_UNANCHORED"),
    "evidence_completeness": ("wet_lab_anchors", "evidence_completeness"),
    "anchors_3d_coverage":   ("wet_lab_anchors", "anchors_3d_coverage"),
    "Grade":              ("wet_lab_anchors", "Grade"),
    "grade_against_anchor":  ("wet_lab_anchors", "grade_against_anchor"),
    "worst_grade":        ("wet_lab_anchors", "worst_grade"),
    "ScenarioAnchor":     ("wet_lab_anchors", "ScenarioAnchor"),
    "summarize_anchors":  ("wet_lab_anchors", "summarize"),  # alias
    "anchors_index":      ("wet_lab_anchors", "anchors_index"),

    # ── 推理模板库（STELLA 模式）──
    "Template":           ("template_library", "Template"),
    "TemplateLibrary":    ("template_library", "TemplateLibrary"),
    "template_id":        ("template_library", "template_id"),
    "default_library_path": ("template_library", "default_library_path"),
    "from_harness":       ("template_library", "from_harness"),

    # ── 多保真机理先验（尺度自适应 + 两阶段门控）──
    "axis_grid":          ("multifidelity", "axis_grid"),
    "make_lowfi":         ("multifidelity", "make_lowfi"),
    "decide_mean_fn":     ("multifidelity", "decide_mean_fn"),
    "COVERAGE_OK_FLOOR":  ("multifidelity", "COVERAGE_OK_FLOOR"),
    "DEFAULT_COV_TOL":    ("multifidelity", "DEFAULT_COV_TOL"),
    "LOG_SPAN_THRESHOLD": ("multifidelity", "LOG_SPAN_THRESHOLD"),
    "assess_applicability":  ("multifidelity", "assess_applicability"),
    "ROUGHNESS_CAP":      ("multifidelity", "ROUGHNESS_CAP"),
    "MODALITY_MINIMA_CAP": ("multifidelity", "MODALITY_MINIMA_CAP"),

    # ── Tool Ocean 能力索引 ──
    "Capability":           ("tool_ocean", "Capability"),
    "build_manifest":       ("tool_ocean", "build_manifest"),
    "scan_engine":          ("tool_ocean", "scan_engine"),
    "find":                 ("tool_ocean", "find"),
    "CAPABILITY_DOMAINS":   ("tool_ocean", "CAPABILITY_DOMAINS"),
    "default_manifest_path": ("tool_ocean", "default_manifest_path"),

    # ── SciTrust 四维信任自评 ──
    "scitrust_evaluate":           ("scitrust_eval", "evaluate"),   # alias
    "SCITRUST_DIMENSIONS":         ("scitrust_eval", "SCITRUST_DIMENSIONS"),
    "SCITRUST_ETHICS_SUBDOMAINS":  ("scitrust_eval", "SCITRUST_ETHICS_SUBDOMAINS"),

    # ── 三级泛化基准 G1/G2/G3 ──
    "run_g1":                  ("generalization_bench", "run_g1"),
    "run_g2":                  ("generalization_bench", "run_g2"),
    "run_g3":                  ("generalization_bench", "run_g3"),
    "run_generalization_bench": ("generalization_bench", "run_all"),   # alias
    "G1_MAX_RATIO":            ("generalization_bench", "G1_MAX_RATIO"),
    "G2_MAX_RATIO":            ("generalization_bench", "G2_MAX_RATIO"),
    "GEN_BENCH_SEED":          ("generalization_bench", "DEFAULT_SEED"),  # alias

    # ── 对抗性探针（A11）──
    "run_adversarial_probes":  ("adversarial_probes", "run_all"),  # alias
    "Probe":                   ("adversarial_probes", "Probe"),

    # ── 实验输出接口 ──
    "propose_experiment":      ("experiment_proposal", "propose_experiment"),
    "verify_prediction":       ("experiment_proposal", "verify_prediction"),
    "audit_predictor":         ("experiment_proposal", "audit_predictor"),
    "build_experiment_proposals": ("experiment_proposal", "build_all"),  # alias
    "ExperimentProposal":      ("experiment_proposal", "ExperimentProposal"),

    # ── 假设驱动接口（L4 层）──
    "HypothesisSpec":           ("hypothesis_to_conclusion", "HypothesisSpec"),
    "HypothesisVerdict":        ("hypothesis_to_conclusion", "HypothesisVerdict"),
    "AdmissionResult":          ("hypothesis_to_conclusion", "AdmissionResult"),
    "check_admission":          ("hypothesis_to_conclusion", "check_admission"),
    "evaluate_hypothesis":      ("hypothesis_to_conclusion", "evaluate_hypothesis"),
    "evaluate_all":             ("hypothesis_to_conclusion", "evaluate_all"),
    "load_hypotheses":          ("hypothesis_to_conclusion", "load_hypotheses"),
    "what_would_make_verifiable": ("hypothesis_to_conclusion", "what_would_make_verifiable"),

    # ── 验证框架（基础层）──
    "ValidationResult":    ("validation", "ValidationResult"),
    "ValidationFramework": ("validation", "ValidationFramework"),
    "DIMENSIONS":          ("validation", "DIMENSIONS"),

    # ── 世界模型（可选，加载 JSON 文件，允许失败关闭）──
    "WorldModel":          ("world_model", "WorldModel"),

    # ── 三大 P0 孤儿模块接线 ──
    "DigitalTwinScenario":   ("digital_twin_lab", "DigitalTwinScenario"),
    "TwinRunResult":         ("digital_twin_lab", "TwinRunResult"),
    "run_twin_loop":         ("digital_twin_lab", "run_twin_loop"),
    "compare_strategies":    ("digital_twin_lab", "compare_strategies"),
    "run_all_twins":         ("digital_twin_lab", "run_all_twins"),

    "PhysicsConstraint":         ("physics_constrained_conformal", "PhysicsConstraint"),
    "PhysicsConformalResult":    ("physics_constrained_conformal", "PhysicsConformalResult"),
    "apply_physics_constraints": ("physics_constrained_conformal", "apply_physics_constraints"),
    "physics_constrained_conformal": ("physics_constrained_conformal", "physics_constrained_conformal"),
    "validate_pcc":              ("physics_constrained_conformal", "validate_on_scenarios"),  # alias

    "SpectralProvenanceGuard":   ("spectral_guard", "SpectralProvenanceGuard"),
    "spectral_diagnose":         ("spectral_guard", "spectral_diagnose"),

    # ── Fourier symbol diagnosis ──
    "SymbolProbe":                  ("fourier_symbol_diag", "SymbolProbe"),
    "probe_laplace_1d":             ("fourier_symbol_diag", "probe_laplace_1d"),
    "probe_laplace_2d":             ("fourier_symbol_diag", "probe_laplace_2d"),
    "probe_wave_equation":          ("fourier_symbol_diag", "probe_wave_equation"),
    "probe_advection":              ("fourier_symbol_diag", "probe_advection"),
    "probe_helmholtz":              ("fourier_symbol_diag", "probe_helmholtz"),
    "probe_advection_diffusion":    ("fourier_symbol_diag", "probe_advection_diffusion"),
    "run_all_probes":               ("fourier_symbol_diag", "run_all_probes"),
    "detect_symbol_tampering":      ("fourier_symbol_diag", "detect_symbol_tampering"),

    # ── 对照基线 RandomForest ──
    "DecisionTree":           ("random_forest_baseline", "DecisionTree"),
    "RandomForest":           ("random_forest_baseline", "RandomForest"),
    "rf_evaluate_scenario":   ("random_forest_baseline", "evaluate_scenario"),   # alias
    "rf_compare_with_gp":     ("random_forest_baseline", "compare_with_gp"),     # alias
    "rf_cross_conformal_scores": ("random_forest_baseline", "cross_conformal_scores"),  # alias

    # ── 校准 RL ──
    "CalibrationRLPolicy":    ("calibration_rl", "CalibrationPolicy"),  # alias
    "calib_train":            ("calibration_rl", "train"),                # alias
    "calib_evaluate":         ("calibration_rl", "evaluate"),              # alias

    # ── 张量↔(sample_id, λ) 密码学绑定 ──
    "TensorBinding":               ("tensor_binding", "TensorBinding"),
    "BoundTensor":                 ("tensor_binding", "BoundTensor"),
    "BindingError":                ("tensor_binding", "BindingError"),
    "BOUNDING_TYPES":              ("tensor_binding", "BOUNDING_TYPES"),
    "bind_batch":                  ("tensor_binding", "bind_batch"),
    "verify_uniformity":           ("tensor_binding", "verify_uniformity"),
    "verify_boundary_agreement":   ("tensor_binding", "verify_boundary_agreement"),
    "verify_grid_conformity":      ("tensor_binding", "verify_grid_conformity"),
    "verify_symmetry":             ("tensor_binding", "verify_symmetry"),

    # ── HMAC-SHA256 三元密码学绑定 ──
    "BindingRecord":         ("provenance_binding", "BindingRecord"),
    "BindingVerdict":        ("provenance_binding", "BindingVerdict"),
    "BindingIntegrityError": ("provenance_binding", "BindingIntegrityError"),
    "SECRET_KEY_SOURCES":    ("provenance_binding", "SECRET_KEY_SOURCES"),
    "pb_bind":               ("provenance_binding", "bind"),
    "pb_verify":             ("provenance_binding", "verify"),
    "pb_tensor_sha256":      ("provenance_binding", "tensor_sha256"),
    "pb_compute_signature":  ("provenance_binding", "compute_signature"),
    "to_cyclonedx_bom":      ("provenance_binding", "to_cyclonedx_bom"),
    "bom_to_json":           ("provenance_binding", "bom_to_json"),
    "load_secret_keys":      ("provenance_binding", "load_secret_keys"),

    # ── 场景浏览器查询接口 ──
    "TRUST_TIERS":           ("scenario_browser", "TRUST_TIERS"),
    "DIFFICULTY_TIERS":      ("scenario_browser", "DIFFICULTY_TIERS"),
    "trust_tier":            ("scenario_browser", "trust_tier"),
    "difficulty_tier":       ("scenario_browser", "difficulty_tier"),
    "query_by_discipline":   ("scenario_browser", "query_by_discipline"),
    "query_by_trust":        ("scenario_browser", "query_by_trust"),
    "query_by_difficulty":   ("scenario_browser", "query_by_difficulty"),
    "discipline_index":      ("scenario_browser", "discipline_index"),
    "scenario_summary":      ("scenario_browser", "scenario_summary"),

    # ── 多方法保形内核 ──
    "CONFORMAL_METHODS":     ("conformal_kernel", "METHODS"),     # alias
    "cf_predict_interval":   ("conformal_kernel", "predict_interval"),  # alias
    "cf_evaluate_methods":   ("conformal_kernel", "evaluate_methods"),  # alias
    "cf_coverage":           ("conformal_kernel", "coverage"),
    "cf_width":              ("conformal_kernel", "width"),
    "cf_backend_summary":    ("conformal_kernel", "backend_summary"),  # alias
}


# 惰性解析缓存：首次访问后缓存解析结果，后续 O(1)。
# 用全局 dict 保护并发访问下的重复导入。
_LOCK = _threading.RLock()


def __getattr__(name: str) -> Any:
    """PEP 562 惰性属性解析。

    语义：`engine.<name>` / `from engine import <name>` 在此解析。
    解析成功后写入模块 globals()，后续访问走模块属性快路径。
    """
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        # 未在本表中——先尝试解析为子模块（`engine.surrogate` 之类）
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule_rel, attr_name = spec

    with _LOCK:
        # 缓存命中检查（并发下另一线程可能刚完成解析）
        if name in globals():
            return globals()[name]

        full_path = f"{__name__}.{submodule_rel}"
        try:
            mod = _sys.modules.get(full_path)
            if mod is None:
                mod = _importlib.import_module(f".{submodule_rel}", __name__)
        except (OSError, ValueError) as exc:
            # world_model 等允许加载失败的模块：与原实现一致，静默置 None
            if submodule_rel == "world_model":
                globals()["WorldModel"] = None
                return None
            raise ImportError(
                f"engine.{name}: failed to import submodule "
                f"'engine.{submodule_rel}' (original error: {exc!r})"
            ) from exc
        except Exception:
            raise
        except BaseException:  # pragma: no cover
            raise

        # 模块本体导出：brand 走 _SELF 哨兵
        if attr_name == "_SELF":
            value: Any = mod
        else:
            try:
                value = getattr(mod, attr_name)
            except AttributeError:
                raise AttributeError(
                    f"engine.{name}: submodule 'engine.{submodule_rel}' "
                    f"has no attribute {attr_name!r}"
                )

        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """完整导出列表（含惰性符号），保证 dir(engine) / tab-complete 正常工作。"""
    return sorted(set(list(globals().keys()) + list(_LAZY_EXPORTS.keys()) + ["__path__", "__file__"]))


# ---------------------------------------------------------------------------
# 公共 API 清单（__all__）
#   * 与 _LAZY_EXPORTS 键完全一致，供文档生成 / tool_ocean / IDE 补全使用
#   * 排序稳定，便于 diff 审查
# ---------------------------------------------------------------------------
__all__ = sorted(_LAZY_EXPORTS.keys())


# 包元信息
__version__ = "1.0.0"
__package__ = "engine"


# ---------------------------------------------------------------------------
# 兼容性补丁：某些旧代码路径用 `from engine import brand`，然后 `engine.brand.X`。
# 惰性加载下 `brand` 走 __getattr__ 返回模块对象，此路径天然兼容。
# ---------------------------------------------------------------------------

# ── 飞轮刻意不导出 ────────────────────────────────────────────────────────
# ⚠️ 刻意不导出 flywheel（2026-10-04 起沿用至今）。engine/flywheel.py 在**模块顶层**
# 就 `import paper_validation_loop`，而 paper_validation_loop 顶层又要
# `from virtual_experiment.parity_framework import ...`，后者内部
# `from engine.surrogate import ...` → 触发 `engine/__init__.py` 执行 →
# 若此时 __init__ 又 import flywheel，就形成死锁。惰性加载不会打破这条
# 防线（本 __init__ 从不主动 import flywheel），但也不能通过 __getattr__
# 走"隐式自动发现"路径——用户必须显式 `from engine.flywheel import ...`。
# 惰性化之后，这条防线依然成立：没有 _LAZY_EXPORTS 条目 = 不会自动 import。
