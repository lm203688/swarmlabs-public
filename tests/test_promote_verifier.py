"""
promote_verifier 单元测试。

设计要点：
1. 不依赖 numpy / sklearn（本地测试机器可能没装）；
2. **不 import `engine` 包** —— 因为 `engine/__init__.py` 顶层会拉 surrogate
   → numpy，会污染测试环境。这里用 importlib.util 按文件路径加载
   `engine/promote_verifier.py`，绕开包初始化。

覆盖：
- PromoteVerifier.check：脚本缺失 / 缺预测文件 / 全 pass / 失败率过高
- PromoteVerifier.check --dry-run：不执行脚本、只生成骨架
- 退出码语义：0/1/2/3
- CLI --dry-run / --check 路径
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERIFIER_PY = REPO / "engine" / "promote_verifier.py"


def _load_promote_verifier():
    """按文件路径加载 promote_verifier，绕开 engine/__init__.py。

    必须先把模块注册进 sys.modules，否则 dataclasses 会找不到模块 __dict__。
    """
    name = "promote_verifier_isolated"
    spec = importlib.util.spec_from_file_location(name, VERIFIER_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPromoteVerifier(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.module = _load_promote_verifier()
        self.PromoteVerifier = self.module.PromoteVerifier

    def tearDown(self):
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_script(self, script_body: str, target_dir: Path) -> Path:
        """在 target_dir/scripts/ 下写一个 Python 脚本（跨平台）。"""
        d = target_dir / "scripts"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "verify.py"
        p.write_text(script_body, encoding="utf-8")
        return p

    def _write_predictions(self, target_dir: Path, results) -> Path:
        d = target_dir / "results"
        d.mkdir(exist_ok=True)
        p = d / "predictions.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in results), encoding="utf-8")
        return p

    def test_init_creates_skeleton(self):
        d = self.tmpdir / "init_test"
        g = self.PromoteVerifier(project="proj1", target_dir=d, dry_run=True)
        skill_md = g.init()
        self.assertTrue(skill_md.exists())
        content = skill_md.read_text(encoding="utf-8")
        self.assertIn("proj1", content)
        self.assertIn("Verifier Contract", content)
        self.assertTrue((d / ".verifier_state.json").exists())

    def test_check_dry_run_returns_pass(self):
        d = self.tmpdir / "dry"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake\n", encoding="utf-8")
        g = self.PromoteVerifier(project="p", target_dir=d, dry_run=True)
        status = g.check()
        self.assertEqual(status.gate_exit, 0)
        self.assertTrue(status.gate_pass)

    def test_missing_script_returns_exit_2(self):
        d = self.tmpdir / "noscript"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake skill\n", encoding="utf-8")
        g = self.PromoteVerifier(project="p", target_dir=d, dry_run=False)
        status = g.check(status_out=self.tmpdir / "status.json")
        self.assertEqual(status.gate_exit, 2)
        self.assertFalse(status.gate_pass)
        self.assertTrue((self.tmpdir / "status.json").exists())

    def test_script_without_predictions_returns_exit_3(self):
        # 脚本能跑，但预测文件不存在 → script_failed
        d = self.tmpdir / "noresult"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake\n", encoding="utf-8")
        script = self._write_script("# do nothing\n", d)
        g = self.PromoteVerifier(
            project="p", target_dir=d, dry_run=False,
            script_path=script, max_runtime_sec=30,
        )
        status = g.check(status_out=self.tmpdir / "status.json")
        self.assertEqual(status.gate_exit, 3)
        self.assertFalse(status.gate_pass)

    def test_all_pass_returns_exit_0(self):
        d = self.tmpdir / "allpass"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake\n", encoding="utf-8")
        results = [
            {"candidate_id": "c1", "verdict": "pass", "checks": ["syntax"]},
            {"candidate_id": "c2", "verdict": "pass", "checks": ["syntax"]},
            {"candidate_id": "c3", "verdict": "pass", "checks": ["syntax"]},
        ]
        self._write_predictions(d, results)
        script = self._write_script("pass\n", d)
        g = self.PromoteVerifier(
            project="p", target_dir=d, dry_run=False,
            script_path=script, max_runtime_sec=30,
        )
        status = g.check(status_out=self.tmpdir / "status.json")
        self.assertEqual(status.gate_exit, 0)
        self.assertTrue(status.gate_pass)
        self.assertEqual(status.total, 3)
        self.assertEqual(status.pass_count, 3)
        self.assertAlmostEqual(status.success_rate, 1.0)

    def test_high_failure_blocks_promotion(self):
        d = self.tmpdir / "fail"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake\n", encoding="utf-8")
        results = [
            {"candidate_id": "c1", "verdict": "fail", "checks": ["syntax"]},
            {"candidate_id": "c2", "verdict": "fail", "checks": ["syntax"]},
            {"candidate_id": "c3", "verdict": "pass", "checks": ["syntax"]},
        ]
        self._write_predictions(d, results)
        script = self._write_script("pass\n", d)
        g = self.PromoteVerifier(
            project="p", target_dir=d, dry_run=False,
            script_path=script, max_runtime_sec=30,
        )
        status = g.check(status_out=self.tmpdir / "status.json")
        self.assertEqual(status.gate_exit, 1)
        self.assertFalse(status.gate_pass)
        self.assertAlmostEqual(status.success_rate, 1 / 3, places=3)

    def test_too_few_candidates_returns_exit_3(self):
        # min_candidates=3，只 1 个数据 → 数据太少
        d = self.tmpdir / "small"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text("# fake\n", encoding="utf-8")
        results = [{"candidate_id": "c1", "verdict": "pass", "checks": ["syntax"]}]
        self._write_predictions(d, results)
        script = self._write_script("pass\n", d)
        g = self.PromoteVerifier(
            project="p", target_dir=d, dry_run=False,
            script_path=script, max_runtime_sec=30,
        )
        status = g.check(status_out=self.tmpdir / "status.json")
        self.assertEqual(status.gate_exit, 3)
        self.assertFalse(status.gate_pass)

    def test_interpret_exit_code(self):
        m = _load_promote_verifier()
        self.assertEqual(m.interpret_exit_code(0), "ok")
        self.assertEqual(m.interpret_exit_code(1), "fail")
        self.assertEqual(m.interpret_exit_code(2), "script_missing")
        self.assertEqual(m.interpret_exit_code(3), "script_failed")
        self.assertEqual(m.interpret_exit_code(99), "unknown")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.py = sys.executable
        self.verifier_py = str(VERIFIER_PY)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, args):
        return subprocess.run(
            [self.py, self.verifier_py] + args,
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO),
        )

    def test_cli_dry_run(self):
        d = self.tmpdir / "proj"
        r = self._run(["--project", "p", "--target", str(d), "--dry-run"])
        self.assertEqual(r.returncode, 0)
        self.assertTrue((d / "SKILL.md").exists())

    def test_cli_check_missing_returns_2(self):
        d = self.tmpdir / "proj2"
        r1 = self._run(["--project", "p2", "--target", str(d), "--dry-run"])
        self.assertEqual(r1.returncode, 0)
        r2 = self._run([
            "--project", "p2", "--target", str(d), "--check",
            "--status-out", str(self.tmpdir / "s.json"),
        ])
        self.assertEqual(r2.returncode, 2)
        self.assertTrue((self.tmpdir / "s.json").exists())

    def test_cli_requires_mode(self):
        # 未指定 --dry-run / --check 应报错
        d = self.tmpdir / "proj3"
        r = self._run(["--project", "p3", "--target", str(d)])
        self.assertNotEqual(r.returncode, 0)

    def test_cli_invalid_flag(self):
        r = self._run(["--invalid-flag-xyz"])
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
