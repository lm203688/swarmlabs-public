"""
微生物 Monod 生长动力学 · 真实用户试用闭环演示
========================================================
模拟真实用户只有少量实验点 -> GP 虚拟实验拟合 -> UQ(误差棒) -> 主动学习推荐下一个最该做的实验。
完全复用项目内 parity_framework.GPSurrogate，证明微生物领域可端到端走通、可作为专项邀请真实用户的靶子。

运行: python scripts/virtual_experiment/microbial_trial_demo.py
产出: microbial_demo.png / microbial_demo_report.html
"""
import sys, os, base64
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parity_framework import GPSurrogate

# ---- 地面真值：Monod 1949 发表系数 (E.coli 葡萄糖, Shuler & Kargi 2002 代表值) ----
MUMAX, KS = 0.81, 0.22
def monod(S):
    S = np.asarray(S, float)
    return MUMAX * S / (KS + S)

# ---- 模拟真实用户：只有 8 个非均匀实验点 + 实验噪声 ----
rng = np.random.default_rng(7)
S_exp = np.array([0.05, 0.10, 0.20, 0.35, 0.50, 0.80, 1.20, 1.80])
NOISE = 0.015
y_exp = monod(S_exp) + rng.normal(0, NOISE, len(S_exp))

# ---- GP 虚拟实验 (y 做 z-score 归一化，修复幅值 bug) ----
ymean, ystd = float(y_exp.mean()), float(y_exp.std()) + 1e-9
gp = GPSurrogate(lengthscale=0.30, noise_var=NOISE**2, y_mean=ymean, y_scale=ystd)
gp.fit(S_exp.reshape(-1, 1), y_exp)

Sgrid = np.linspace(0.02, 2.0, 200)
mean, std = gp.predict(Sgrid.reshape(-1, 1))
lo, hi = mean - 1.96 * std, mean + 1.96 * std

# ---- UQ 覆盖率 (在密测试集上报告, 避免自欺) ----
ytrue_grid = monod(Sgrid)
coverage = float(np.mean((ytrue_grid >= lo) & (ytrue_grid <= hi)))
rmse = float(np.sqrt(np.mean((mean - ytrue_grid) ** 2)))
rel = rmse / (ytrue_grid.max() - ytrue_grid.min())

# ---- 主动学习：候选点上后验方差最大 = 信息量最大 = 最该做的下一个真实实验 ----
cand = np.linspace(0.02, 2.0, 300)
_, stdc = gp.predict(cand.reshape(-1, 1))
next_idx = int(np.argmax(stdc))
S_next = float(cand[next_idx])
std_next = float(stdc[next_idx])

# ---- 可视化 (标签英文, 避免缺字体乱码) ----
fig, ax = plt.subplots(figsize=(9, 5))
ax.plot(Sgrid, monod(Sgrid), "k-", lw=2, label="true Monod mechanism (mu_max=0.81, Ks=0.22)")
ax.scatter(S_exp, y_exp, c="red", s=45, zorder=5, label="real user experiments (N=8)")
ax.plot(Sgrid, mean, "b--", lw=2, label="GP virtual experiment mean")
ax.fill_between(Sgrid, lo, hi, alpha=0.20, color="b", label="95% interval (UQ)")
ax.axvline(S_next, color="green", ls=":", lw=2.2,
           label="next experiment to run: S=%.2f" % S_next)
ax.set_xlabel("substrate concentration S (g/L)")
ax.set_ylabel("specific growth rate mu (h^-1)")
ax.set_title("Microbial Monod kinetics · virtual experiment loop (8 pts -> UQ -> active learning)")
ax.legend(fontsize=8, loc="lower right")
ax.grid(alpha=0.3)
plt.tight_layout()
png_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "microbial_demo.png")
plt.savefig(png_path, dpi=130)
plt.close()

# ---- HTML 报告 (base64 内嵌图, 无外部依赖) ----
with open(png_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>微生物 Monod 虚拟实验闭环 · 试用报告</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1a1a1a;line-height:1.6}}
h1{{font-size:22px}} .kpi{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.card{{flex:1;min-width:150px;background:#f5f7fa;border:1px solid #e3e8ef;border-radius:10px;padding:12px 14px}}
.card .v{{font-size:22px;font-weight:700;color:#0b66c3}} .card .l{{font-size:12px;color:#667}}
img{{max-width:100%;border:1px solid #e3e8ef;border-radius:10px;margin:12px 0}}
.note{{background:#fff8e6;border-left:4px solid #f0a020;padding:10px 14px;border-radius:6px;font-size:14px}}
</style></head><body>
<h1>微生物 Monod 生长动力学 · 虚拟实验闭环</h1>
<p>场景：发酵 / 合成生物学中最基础的 <b>底物→比生长速率</b> 关系 μ(S)=μ<sub>max</sub>·S/(K<sub>s</sub>+S)。
真实用户通常只有个位数实验点，且每个点都要做一次摇瓶/补料实验（贵、慢）。</p>
<div class="kpi">
<div class="card"><div class="v">{len(S_exp)}</div><div class="l">真实实验点数 (用户输入)</div></div>
<div class="card"><div class="v">{coverage:.2f}</div><div class="l">95% 区间覆盖率 (UQ 可信)</div></div>
<div class="card"><div class="v">{rel*100:.1f}%</div><div class="l">相对误差 rel</div></div>
<div class="card"><div class="v">{S_next:.2f}</div><div class="l">推荐下一个实验 S (g/L)</div></div>
</div>
<img src="data:image/png;base64,{b64}" alt="Monod virtual experiment">
<h3>闭环说明</h3>
<ol>
<li><b>输入</b>：用户只提供 {len(S_exp)} 个 (S, μ) 实验点（红色）。</li>
<li><b>虚拟实验</b>：GP 代理拟合出整条曲线（蓝虚线）+ 95% 误差棒（UQ）。</li>
<li><b>主动学习</b>：绿色虚线 = 当前后验不确定性最大的底物浓度 <b>S={S_next:.2f}</b>，
即「再做一个实验，信息量最大」的点——这正是帮助用户<b>少做实验、多探索</b>的核心价值。</li>
</ol>
<div class="note"><b>诚实边界</b>：本演示用发表 Monod 系数作 ground-truth（合成，非你的真实实验室数据）。
接入你的真实实验数据后，曲线与推荐点会随之变化；UQ 误差棒永远保留，
覆盖率目标是 ≈0.95（过窄=过自信，过宽=信息量低，两者都不达标）。</div>
</body></html>"""
html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "microbial_demo_report.html")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)

print("=== 微生物 Monod 闭环走通 ===")
print(f"真实实验点 N = {len(S_exp)}")
print(f"rel 相对误差 = {rel*100:.2f}%")
print(f"95% 覆盖率   = {coverage:.3f}  (目标 ~0.95)")
print(f"推荐下一个实验点 S = {S_next:.3f} g/L, 该点 std = {std_next:.3f}")
print(f"输出: {png_path}")
print(f"报告: {html_path}")
print("闭环成立：少量实验点 -> GP 虚拟实验 -> UQ -> 主动学习推荐下一个实验。")
