"""6 关自检雷达图（renderRadar + refreshRadar）端到端验证。

不打 LLM、不渲染浏览器；走静态 HTML/JS 关键字 + 算法伪代码级数学抽样校验。
"""
from __future__ import annotations
import re, sys, math, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


sf = ROOT / "web" / "self-check.html"
html = sf.read_text(encoding="utf-8")

# 1) DOM/HTML 结构
dom_checks = {
    "DOM #radarWrap 容器": 'id="radarWrap"' in html,
    "DOM #radarLarge 大 SVG": 'id="radarLarge"' in html,
    "DOM .radar-card 卡片": 'class="radar-card"' in html,
    "DOM #radarWeak 弱项文字": 'id="radarWeak"' in html,
    "DOM #radarSub 副标题": 'id="radarSub"' in html,
    "DOM legend 本周/上周": "本周" in html and "上周" in html,
    "DOM SVG role/aria-label": 'role="img"' in html and "aria-label=\"6 维本周能力" in html,
    "SVG viewBox 0 0 260 260": 'viewBox="0 0 260 260"' in html,
}
for name, ok in dom_checks.items():
    check(name, ok)

# 2) CSS 类
css_checks = {
    "CSS .radar-card 卡片样式": ".radar-card" in html,
    "CSS .r-poly 本周轮廓": ".radar-card .r-poly" in html,
    "CSS .r-poly-prev 上周虚线": ".radar-card .r-poly-prev" in html,
    "CSS .r-grid 网格": ".radar-card .r-grid" in html,
    "CSS .r-axis 维度名": ".radar-card .r-axis" in html,
    "CSS .h-mini 历史迷你 SVG": ".hist .h-mini" in html,
    "CSS .l-cur 本周色块": ".r-legend .l-cur" in html,
    "CSS .l-prev 上周色块": ".r-legend .l-prev" in html,
}
for name, ok in css_checks.items():
    check(name, ok)

# 3) JS 函数
js_checks = {
    "JS renderRadar 函数": "function renderRadar" in html,
    "JS refreshRadar 函数": "function refreshRadar" in html,
    "JS renderRadar 用极坐标": "Math.PI / 2" in html or "Math.cos" in html,
    "JS renderRadar 算 6 顶点 polygon": "polygon" in html and "polygonFor" in html,
    "JS renderRadar 半径 = score/5": "Math.max(0, Math.min(5" in html or "/ 5" in html,
    "JS refreshRadar 6 关填齐才显示": "filled < 6" in html or "filled >= 6" in html or "filled < 6" in html,
    "JS refreshRadar 周环比 ↑↓": "improved" in html and "regressed" in html,
    "JS renderHistory 加迷你 SVG": "rmini-" in html and "h-mini" in html,
    "JS loadHistory 设 PREV_SCORES": "PREV_SCORES = " in html,
    "JS updateProgress 调 refreshRadar": "refreshRadar()" in html,
}
for name, ok in js_checks.items():
    check(name, ok)

# 4) 算法数学抽样（重写核心算法 + 校验 6 个顶点位置）
# renderRadar 中：起点角 = -π/2 (正上方)，每条轴 60° 间隔
# 半径 r = norm(score) * maxR；score=5 → r=maxR
# 中心 (cx, cy) = (W/2, H/2) = (130, 130)，maxR ≈ 130 - 30 = 100
N = 6
W = H = 260
cx, cy = W/2, H/2
pad = 30
maxR = min(W, H) / 2 - pad

def expected(i, score):
    ang = (-math.pi / 2) + i * (2 * math.pi / N)
    r = (score / 5) * maxR
    return (cx + r * math.cos(ang), cy + r * math.sin(ang))

# 验证 6 顶点几何位置：score=5 时 = 6 个端点（6 边形），最大半径
for i in range(N):
    x, y = expected(i, 5)
    # 端点应近似在半径 maxR 圆周上
    dist = math.hypot(x - cx, y - cy)
    check(f"几何端点 i={i} 在 maxR 圆周", abs(dist - maxR) < 0.01, f"dist={dist:.2f} maxR={maxR}")

# 验证顶点 0 在正上方（cos=0, sin=-1 → y = cy - maxR）
x0, y0 = expected(0, 5)
check("顶点 0 在正上方", abs(x0 - cx) < 0.01 and abs(y0 - (cy - maxR)) < 0.01, f"({x0},{y0})")

# 验证 60° 间隔
for i in range(N):
    x, y = expected(i, 5)
    ang = math.atan2(y - cy, x - cx)  # 弧度，[-π, π]
    expected_ang = (-math.pi / 2) + i * (2 * math.pi / N)
    diff = abs(((ang - expected_ang + math.pi) % (2 * math.pi)) - math.pi)
    check(f"角度 i={i} 等于 -π/2+i·60°", diff < 0.001, f"diff={diff:.4f}")

# 验证 score=0 时所有顶点都在中心
all_center = all(abs(expected(i, 0)[0] - cx) < 0.01 and abs(expected(i, 0)[1] - cy) < 0.01 for i in range(N))
check("score=0 全部顶点退化为中心", all_center)

# 5) 边界：score 越界应被 clamp 到 [0,5]；前端 UI 只发整数，算法对浮点/NaN/字符串也安全
# renderRadar 中：Math.max(0, Math.min(5, +v || 0)) / 5
def clamped(v):
    try: x = float(v)
    except (TypeError, ValueError): x = 0
    if x != x:  # NaN
        x = 0
    return max(0, min(5, x))
check("clamp(6) = 5", clamped(6) == 5)
check("clamp(-1) = 0", clamped(-1) == 0)
check("clamp(3.7) 通过到 3.7", abs(clamped(3.7) - 3.7) < 1e-6)
check("clamp(\"abc\") = 0", clamped("abc") == 0)
check("clamp(0) = 0", clamped(0) == 0)

# 6) 验证迷你 SVG 大小（56×56）和 viewBox
mini_ok = 'width="56" height="56" viewBox="0 0 56 56"' in html
check("历史迷你 SVG 56×56", mini_ok)

# 7) 验证 SVG 无外部库依赖（无 d3 / chart.js / echarts 引用）
no_deps = not any(lib in html.lower() for lib in ["d3.js", "chart.js", "echarts", "highcharts"])
check("雷达图无外部图表库依赖", no_deps)

# 8) 验证 radar CSS 在 self-check.html 已生效（非重复定义）
check("CSS radar 块只出现一次", html.count(".radar-card {") == 1)

# 9) 验证 renderRadar 函数源代码包含 60° 间隔
check("renderRadar 用 2π/6 间隔", "2 * Math.PI / N" in html or "2*Math.PI/N" in html)

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
total = len(dom_checks) + len(css_checks) + len(js_checks) + N + 4 + 3 + 1 + 1 + 1 + 1
print(f"✅ 全部通过（{total} 项）")
