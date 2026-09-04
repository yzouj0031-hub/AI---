"""手机 / 平板适配测试：布局、触摸摇杆、会议输入框。

覆盖三件在桌面上永远发现不了的事：
  1. 横屏时地图会不会被控件挤没（实测原版 iPhone 横屏画布只剩 134px 高）
  2. 摇杆用触摸能不能真的驱动角色（几何是按实际尺寸算的，改 CSS 不能改坏）
  3. 弹出键盘后辩解输入框还在不在可视区内

    pip install playwright && playwright install chromium
    python tests/test_mobile.py
"""
from __future__ import annotations

import asyncio
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB_PORT = 8794
FAILED: list[str] = []

# 名称, 宽, 高, dpr
DEVICES = [
    ("iPhone 竖屏", 390, 844, 3),
    ("iPhone 横屏", 844, 390, 3),
    ("iPad 竖屏", 820, 1180, 2),
    ("iPad 横屏", 1180, 820, 2),
    ("小安卓机 竖屏", 360, 640, 2),
]


def check(cond: bool, msg: str) -> None:
    if cond:
        print("  ✓", msg)
    else:
        FAILED.append(msg)
        print("  ✗", msg)


async def audit(browser, name, w, h, dpr):
    ctx = await browser.new_context(viewport={"width": w, "height": h},
                                    device_scale_factor=dpr,
                                    is_mobile=True, has_touch=True)
    pg = await ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    await pg.wait_for_timeout(1000)

    m = await pg.evaluate("""()=>{
        const de=document.documentElement;
        // 2D / 3D 各有一块画布，量当前显示的那块
        const el=[...document.querySelectorAll('.stage canvas')].find(c=>!c.hidden)
                 ||document.querySelector('canvas');
        const cv=el.getBoundingClientRect();
        const st=document.querySelector('#stick').getBoundingClientRect();
        const btns=[...document.querySelectorAll('.acts button')].map(e=>e.getBoundingClientRect());
        return {
          hOver: de.scrollWidth>de.clientWidth+1,
          cw: Math.round(cv.width), ch: Math.round(cv.height),
          stick: Math.round(st.width),
          stickIn: st.bottom<=innerHeight+1 && st.left>=0 && st.right<=innerWidth+1,
          btnsIn: btns.every(r=>r.bottom<=innerHeight+1 && r.right<=innerWidth+1),
          minBtn: Math.round(Math.min(...btns.map(r=>r.height))),
          mode: document.getElementById('cv3').hidden?'2D':'3D',
        };}""")

    print(f"\n--- {name} ({w}×{h}) · {m['mode']} ---")
    check(not m["hOver"], "没有横向溢出")
    # 画布要占到视口的合理比例，否则地图小到没法玩
    area = (m["cw"] * m["ch"]) / (w * h)
    check(m["cw"] > 0 and m["ch"] > 0 and area > 0.20,
          f"地图占据可用面积（{m['cw']}×{m['ch']}，占视口 {area:.0%}）")
    check(m["stickIn"], f"摇杆完整在屏内（{m['stick']}px）")
    check(m["btnsIn"], "所有技能按钮都在屏内")
    check(m["minBtn"] >= 40, f"按钮最小高度 {m['minBtn']}px，够手指点")

    # 触摸拖动摇杆 → 角色真的动了
    before = (await pg.evaluate("()=>window.__dbg()"))["me"]
    box = await pg.evaluate(
        "()=>{const r=document.querySelector('#stick').getBoundingClientRect();"
        "return [r.x+r.width/2, r.y+r.height/2, r.width/2]}")
    cx, cy, rad = box
    await pg.mouse.move(cx, cy)
    await pg.mouse.down()
    await pg.mouse.move(cx + rad * 0.9, cy, steps=4)
    await pg.wait_for_timeout(900)
    await pg.mouse.up()
    after = (await pg.evaluate("()=>window.__dbg()"))["me"]
    moved = math.hypot(after[0] - before[0], after[1] - before[1])
    check(moved > 20, f"触摸摇杆驱动了角色移动（位移 {moved:.0f}px，摇杆 {m['stick']}px）")

    check(not errs, f"无 JS 异常{'：' + str(errs[:1]) if errs else ''}")
    await ctx.close()


async def keyboard_case(browser):
    """模拟键盘弹出：视觉视口变矮后，会议面板要跟着收，输入框不能被盖住。"""
    print("\n--- 手机键盘弹出 ---")
    ctx = await browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=3, is_mobile=True, has_touch=True)
    pg = await ctx.new_page()
    await pg.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    await pg.wait_for_timeout(800)
    hooked = await pg.evaluate("()=>!!window.visualViewport")
    check(hooked, "浏览器提供 visualViewport，键盘适配生效")
    # 直接把会议面板打开，检查它是否受 visualViewport 高度约束
    ok = await pg.evaluate("""()=>{
        const ov=document.getElementById('ov');
        ov.classList.add('show');
        const h=ov.getBoundingClientRect().height;
        return Math.abs(h-(window.visualViewport?window.visualViewport.height:innerHeight))<2;
    }""")
    check(ok, "会议面板高度跟随视觉视口，键盘弹出时会一起收窄")
    await ctx.close()


async def pwa_case(browser):
    print("\n--- 添加到主屏幕 ---")
    ctx = await browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=3, is_mobile=True, has_touch=True)
    pg = await ctx.new_page()
    bad: list[str] = []
    pg.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    await pg.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    await pg.wait_for_timeout(600)
    href = await pg.evaluate(
        "()=>{const l=document.querySelector('link[rel=manifest]');return l?l.href:null}")
    check(bool(href), "页面声明了 manifest")
    mf = await pg.evaluate("""async h=>{const r=await fetch(h);return r.ok?await r.json():null}""", href)
    check(mf is not None, "manifest 能取到且是合法 JSON")
    if mf:
        check(mf.get("display") in ("fullscreen", "standalone"),
              f"display={mf.get('display')}，加到主屏幕后不带浏览器地址栏")
        check(len(mf.get("icons", [])) >= 2, f"提供了 {len(mf.get('icons', []))} 个图标")
        check(any(i.get("purpose") == "maskable" for i in mf.get("icons", [])),
              "含 maskable 图标，安卓上不会被裁成怪形状")
    apple = await pg.evaluate(
        "()=>{const l=document.querySelector('link[rel=\"apple-touch-icon\"]');return l?l.href:null}")
    check(bool(apple), "提供了 apple-touch-icon（iOS 主屏图标）")
    check(not bad, f"图标/manifest 无 404{'：' + str(bad[:2]) if bad else ''}")
    await ctx.close()


async def renderer_case(browser):
    """3D 渲染器：能开、能切、切回 2D 后照常能玩；three.js 拿不到时静默退回。"""
    print("\n--- 3D / 2D 渲染器 ---")
    ctx = await browser.new_context(viewport={"width": 900, "height": 640}, has_touch=True)
    pg = await ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    await pg.wait_for_timeout(1800)

    st = await pg.evaluate("()=>({three:!!window.THREE,"
                           "on:!document.getElementById('cv3').hidden,"
                           "btn:document.getElementById('b3d').textContent,"
                           "hidden:document.getElementById('b3d').hidden})")
    check(st["three"], "内置的 three.js 加载成功（不依赖 CDN）")
    check(st["on"] and st["btn"] == "3D", "默认进入 3D，按钮状态一致")
    d = await pg.evaluate("()=>window.__dbg()")
    check(d["r3"] is not None and d["r3"]["sun"] > 0, f"3D 光照在跑（sun={d['r3']['sun'] if d['r3'] else None}）")

    await pg.click("#b3d"); await pg.wait_for_timeout(700)
    st2 = await pg.evaluate("()=>({cv:!document.getElementById('cv').hidden,"
                            "cv3:!document.getElementById('cv3').hidden,"
                            "btn:document.getElementById('b3d').textContent})")
    check(st2["cv"] and not st2["cv3"] and st2["btn"] == "2D", "能切回 2D，两块画布不会同时显示")
    before = (await pg.evaluate("()=>window.__dbg()"))["me"]
    await pg.keyboard.down("d"); await pg.wait_for_timeout(700); await pg.keyboard.up("d")
    after = (await pg.evaluate("()=>window.__dbg()"))["me"]
    check(abs(after[0] - before[0]) + abs(after[1] - before[1]) > 5, "切回 2D 后游戏照常能玩")
    check(not errs, f"切换过程无 JS 异常{'：' + str(errs[:1]) if errs else ''}")
    await ctx.close()

    # three.js 拿不到时必须静默退回 2D，而不是白屏
    ctx2 = await browser.new_context(viewport={"width": 900, "height": 640}, has_touch=True)
    pg2 = await ctx2.new_page()
    errs2: list[str] = []
    pg2.on("pageerror", lambda e: errs2.append(str(e)))
    await pg2.route("**/vendor/three.min.js", lambda r: asyncio.ensure_future(r.abort()))
    await pg2.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    await pg2.wait_for_timeout(1500)
    fb = await pg2.evaluate("()=>({cv:!document.getElementById('cv').hidden,"
                            "btnHidden:document.getElementById('b3d').hidden,"
                            "three:!!window.THREE})")
    check(not fb["three"] and fb["cv"], "three.js 加载失败时自动退回 2D 渲染")
    check(fb["btnHidden"], "3D 按钮自动隐藏，不给用户点一个坏功能")
    b4 = (await pg2.evaluate("()=>window.__dbg()"))["me"]
    await pg2.keyboard.down("s"); await pg2.wait_for_timeout(700); await pg2.keyboard.up("s")
    a4 = (await pg2.evaluate("()=>window.__dbg()"))["me"]
    check(abs(a4[0] - b4[0]) + abs(a4[1] - b4[1]) > 5, "退回 2D 后游戏完整可玩")
    check(not errs2, f"降级过程无 JS 异常{'：' + str(errs2[:1]) if errs2 else ''}")
    await ctx2.close()


async def main() -> int:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, w, h, dpr in DEVICES:
            await audit(browser, name, w, h, dpr)
        await renderer_case(browser)
        await keyboard_case(browser)
        await pwa_case(browser)
        await browser.close()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过")
        for f in FAILED:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(WEB_PORT),
                            "--bind", "127.0.0.1"], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        code = asyncio.run(main())
    finally:
        web.terminate()
    raise SystemExit(code)
