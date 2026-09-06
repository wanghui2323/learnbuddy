"""浏览器实测：学习场（今日/路径/学情/资料 + 学练单元）。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
INST = "japanese-jlpt-n3"
OUT = Path(__file__).parent.parent / "data" / "_shots"
OUT.mkdir(parents=True, exist_ok=True)

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 1000})
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))

    pg.goto(f"{BASE}/instance/{INST}", wait_until="networkidle")
    pg.wait_for_selector("text=今日", timeout=8000)
    assert "开始本周学练" in pg.content(), "今日页缺少学练 CTA"
    pg.screenshot(path=str(OUT / "1_today.png"), full_page=True)
    print("OK 今日:", pg.inner_text(".coach .msg")[:40], "...")

    for v, label in [("path", "路径地图"), ("analytics", "学情"), ("docs", "资料")]:
        pg.click(f'.nav a[data-view="{v}"]')
        pg.wait_for_timeout(400)
        assert label in pg.content(), f"{v} 页未渲染"
        pg.screenshot(path=str(OUT / f"2_{v}.png"), full_page=True)
        print(f"OK {v}")

    # 学练单元：触发真实内容生成
    pg.click('.nav a[data-view="today"]')
    pg.wait_for_timeout(300)
    pg.click("text=开始学习")
    pg.wait_for_selector(".kicker", timeout=30000)  # 讲解卡出现
    title = pg.inner_text(".stage h2")
    print("OK 学练·讲解卡生成:", title[:40])
    pg.screenshot(path=str(OUT / "3_lesson_explain.png"), full_page=True)

    # 进入练习
    pg.click("#nextBtn")
    pg.wait_for_selector(".opt", timeout=5000)
    opts = pg.query_selector_all(".opt")
    print(f"OK 练习题选项数: {len(opts)}")
    opts[0].click()
    pg.wait_for_timeout(400)
    assert pg.query_selector(".opt.correct"), "答题后未标记正确答案"
    pg.screenshot(path=str(OUT / "4_lesson_practice.png"), full_page=True)
    print("OK 练习作答反馈")

    b.close()

print("\nCONSOLE ERRORS:", errors if errors else "无")
print("截图目录:", OUT)
sys.exit(1 if errors else 0)
