# LEGACY 模块隔离说明（2026-10-14）

以下模块已重命名为 `.py.legacy` 后缀，**不再参与运行时 import**，
仅作为代码归档保留，供未来考古参考。

## 隔离清单

| 原名 | 后缀 | 行数 | 隔离原因 |
|---|---|---:|---|
| `engine/conformal_unified.py` | `.py.legacy` | 345 | 与 `engine/conformal.py` 并存，无外部 import |
| `engine/paper_validation_unified.py` | `.py.legacy` | 243 | 板块 11 报告标记的孤儿 |

## 为什么是"改名"而不是"删除"

1. **可追溯性**：板块评估报告与 CI 日志可能引用这些路径；直接删除会破坏引用链
2. **零破坏**：`.py.legacy` 后缀让 Python 不再识别为模块，但文件仍在磁盘上
3. **可回收**：如需恢复，`mv engine/conformal_unified.py.legacy engine/conformal_unified.py` 即可

## 验证

```bash
python -c "
import importlib
for m in ['engine.conformal_unified', 'engine.paper_validation_unified']:
    try:
        importlib.import_module(m)
        print(f'[WARN] {m} 仍可导入')
    except ModuleNotFoundError:
        print(f'  {m} 已隔离 ✓')
"
```

预期输出：两个都"已隔离"。

## 相关报告

- `reports/assessment/modules/板块03_保形预测.md`（345 行处置建议）
- `reports/assessment/modules/板块11_论文知识.md`（243 行处置建议）
- `reports/upgrade/comprehensive_upgrade_exec_report_2026-10-14.md` §3.1（完整待办）
