"""run_notebook_cpu.py —— 在本地 CPU 上把 heat_pinn_paddle.ipynb 的代码 cell 全部真跑一遍。

目的：交付前验证 Notebook 真能运行（不是只做语法检查），并拿到真实 rmse / coverage。
用法：python run_notebook_cpu.py
注意：绘图 cell 会被自动切到 Agg 后端，不弹窗、存成 png。
"""
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
NB = os.path.join(HERE, "heat_pinn_paddle.ipynb")

# 绘图不弹窗
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_orig_show = plt.show
_fig_idx = [0]


def _save_instead_of_show(*a, **kw):
    _fig_idx[0] += 1
    out = os.path.join(HERE, "cpu_run_fig%d.png" % _fig_idx[0])
    plt.savefig(out, dpi=110)
    print("[figure saved] %s" % out)


plt.show = _save_instead_of_show

with open(NB, encoding="utf-8") as f:
    nb = json.load(f)

shared = {"__name__": "__main__", "plt": plt}
t_start = time.time()

for idx, cell in enumerate(nb["cells"]):
    if cell["cell_type"] != "code":
        continue
    src = "".join(cell["source"])
    print("\n" + "=" * 62)
    print("RUN cell %d" % idx)
    print("=" * 62)
    t0 = time.time()
    try:
        exec(compile(src, "<cell%d>" % idx, "exec"), shared)
    except Exception:
        import traceback
        print("!!! cell %d FAILED" % idx)
        traceback.print_exc()
        sys.exit(1)
    print("[cell %d ok, %.1fs]" % (idx, time.time() - t0))

print("\n" + "#" * 62)
print("ALL CODE CELLS PASSED on CPU. total %.1f min" % ((time.time() - t_start) / 60))
print("#" * 62)
for k in ("rmse", "coverage", "PROFILE"):
    if k in shared:
        print("%s = %s" % (k, shared[k]))
