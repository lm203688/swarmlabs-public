# reports/

本目录是 SwarmLabs CI 的**运行时产物目录**，不存放手写内容。

CI 各门禁（`ci-cd.yml` / `ci-baseline.yml` / `honesty-gate.yml`）在运行时会向
本目录写入审计产物（如 `ci-pytest-report.xml`、`ci-data-contracts.json`、
`ci-jsonschema.json`、`ci-error-taxonomy.json` 等），并通过
`actions/upload-artifact` 上传供事后追溯。

本 README 作为占位文件保证该目录**存在于版本树中**——Git 不追踪空目录，
若本目录在树上不存在，CI 里所有 `tee reports/...`、`--junitxml=reports/...`
与 `> reports/...` 都会因「目录不存在」而失败（表现为 step exit 1 / pytest
exit 2），产生与业务逻辑无关的假红。

历史产物（`model-validation-*.json`、`perf-baseline.json` 等）由各自构建脚本
按需生成，不入白名单，也不在提交时静态托管。
