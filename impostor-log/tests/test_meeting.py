"""浏览器端到端测试：验证 LLM 会议流程、主备 failover、规则降级。

前置：
    pip install playwright && playwright install chromium

运行：
    cd impostor-log && python tests/test_meeting.py

脚本会自己拉起假 LLM 端点(:8791)和静态服务(:8792)，跑完自动关掉。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MOCK_PORT = 8791
WEB_PORT = 8792
FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILED.append(msg)
        print("  ✗", msg)
    else:
        print("  ✓", msg)


async def play(pg, cfg, claim="我一直在导航室做接线"):
    """开局 → 杀人 → 开会 → 打字辩解，返回会议记录。"""
    await pg.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
    if cfg:
        await pg.evaluate(
            "c=>localStorage.setItem('impostor_llm_cfg',JSON.stringify(c))", cfg)
    else:
        await pg.evaluate("()=>localStorage.removeItem('impostor_llm_cfg')")
    await pg.reload()
    await pg.wait_for_timeout(600)

    # 靠近船员并击杀（按钮每帧重算 disabled，用 evaluate 抢时机）
    killed = False
    for _ in range(90):
        await pg.keyboard.down("d")
        await pg.wait_for_timeout(110)
        await pg.keyboard.up("d")
        killed = await pg.evaluate(
            "()=>{const b=document.getElementById('bKill');"
            "if(!b.disabled){b.click();return true}return false}")
        if killed:
            break
        await pg.keyboard.down("s")
        await pg.wait_for_timeout(90)
        await pg.keyboard.up("s")

    # 等 AI 发现尸体，或自己报告
    for _ in range(80):
        if await pg.eval_on_selector("#ov", "e=>e.classList.contains('show')"):
            break
        if await pg.evaluate(
            "()=>{const b=document.getElementById('bRep');"
            "if(!b.disabled){b.click();return true}return false}"):
            break
        await pg.keyboard.down("a")
        await pg.wait_for_timeout(110)
        await pg.keyboard.up("a")

    await pg.wait_for_selector("#ov.show", timeout=15000)
    await pg.wait_for_timeout(4500)          # 等四人发言

    typed = False
    if await pg.query_selector(".replybox input"):
        await pg.fill(".replybox input", claim)
        await pg.click(".replybox button")
        typed = True
        await pg.wait_for_timeout(3500)

    lines = await pg.eval_on_selector_all("#ovLog .say", "e=>e.map(x=>x.textContent)")
    tally = await pg.eval_on_selector_all("#ovLog .tag", "e=>e.map(x=>x.textContent)")
    behav = await pg.evaluate("()=>window.__dbg()")
    return {"killed": killed, "typed": typed, "lines": lines,
            "tally": tally, "behav": behav}


async def main() -> int:
    from playwright.async_api import async_playwright

    base = f"http://127.0.0.1:{MOCK_PORT}"
    good = {"base": f"{base}/v1", "key": "sk-test", "model": "mock",
            "base2": "", "key2": "", "model2": ""}
    failover = {"base": f"{base}/bad", "key": "k", "model": "m",
                "base2": f"{base}/v1", "key2": "k", "model2": "mock"}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for label, cfg in [("LLM 正常", good), ("主端点500→切备用", failover),
                           ("无配置→规则降级", None)]:
            print(f"\n--- {label} ---")
            pg = await browser.new_page(viewport={"width": 420, "height": 880})
            errs: list[str] = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            r = await play(pg, cfg)
            check(r["killed"], "成功击杀并触发会议")
            check(r["typed"], "自由打字输入框可用")
            check(len(r["lines"]) >= 5, f"会议产生了发言（{len(r['lines'])} 条）")
            check(len(r["tally"]) >= 2, f"投票正常结算（{len(r['tally'])} 票）")
            b = r["behav"]
            check(max(b["sus"]) > 0,
                  f"行为层：船员对你积累了怀疑度（{[round(x, 1) for x in b['sus']]}）")
            check(b["chases"] > 0, f"行为层：盯梢真的发生了（{b['chases']} 次）")
            check(b["staleWrite"] == 0 and not b["overBudget"],
                  "行为层：最后目击点只在看得见时刷新，且盯梢预算有上限")
            check(not errs, f"无 JS 异常{'：' + str(errs[:2]) if errs else ''}")
            await pg.close()
        await browser.close()

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    mock = subprocess.Popen([sys.executable, str(HERE / "mock_llm_server.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(WEB_PORT),
                            "--bind", "127.0.0.1"], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    try:
        code = asyncio.run(main())
    finally:
        mock.terminate()
        web.terminate()
    raise SystemExit(code)
