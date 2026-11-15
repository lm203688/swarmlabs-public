"""SwarmLabs 快速上手示例。

三步：装包 → demo → 场景列表。
完整 CLI: `swarmlabs --help`
"""

import subprocess
import sys


def run(cmd: list[str]) -> int:
    return subprocess.call(cmd)


def main() -> int:
    print("=" * 60)
    print("SwarmLabs Quickstart")
    print("=" * 60)

    # 1. 端到端 demo
    print("\n[1/3] 运行 demo ...\n")
    if run([sys.executable, "-m", "swarmlabs", "demo"]) != 0:
        print("demo 失败，请检查依赖: pip install -e .")
        return 1

    # 2. 场景列表
    print("\n[2/3] 列出场景 ...\n")
    run([sys.executable, "-m", "swarmlabs", "scenarios", "--limit", "10"])

    # 3. 环境自检
    print("\n[3/3] 环境自检 ...\n")
    run([sys.executable, "-m", "swarmlabs", "doctor"])

    print("\n" + "=" * 60)
    print("完成。更多命令: swarmlabs --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
