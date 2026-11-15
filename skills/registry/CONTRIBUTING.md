# 贡献 SwarmLabs Skill

本目录是 SwarmLabs 科研 Agent Infra 的 **可复用 Skill 注册表**。每个 Skill 是科研工作流中的一个可组合能力单元，以 JSON 形式注册，便于多 Agent 发现与组合调用。

## 新增一个 Skill

1. 在 `skills/registry/` 下创建 `<skill_name>.json`，命名用 `snake_case`。
2. 内容必须严格符合本目录的 `schema.json`（可用本地 json-schema 校验）。
3. 必填字段：`name` `version` `purpose` `input_schema` `output_schema` `invocation_condition` `depend_tools` `failure_handling` `safety_boundary` `reuse_value` `multi_agent_role`。
4. 建议 `output_schema` 包含 `evidence` / `confidence` 字段，见 `docs/agent_output_contract.md`。

## 最小示例模板

见本目录 `templates/new_skill_template.json`。

## 提交方式

- 提交 PR 至本仓库 `skills/registry/`。
- 附带一句用例说明：该 Skill 在哪个科研场景被哪个 Agent 调用、解决了什么。
- 维护者会用 schema 校验 + 人工评审后合入。

## 命名规范

- 文件名 = `name` 字段值 + `.json`
- `version` 采用语义化版本 `MAJOR.MINOR.PATCH`
- `name` 用动宾短语：`{domain}_{action}`，如 `physics_predict`、`experiment_design`

## 可组合性

SwarmLabs 的 Planner/Executor/Verifier 三 Agent 会按 `multi_agent_role` 发现并组合这些 Skill。保持输入/输出 schema 稳定，下游 Agent 不会破坏性变更。
