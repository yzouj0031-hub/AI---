"""Agent 层测试：prompt 拼装、真实 HTTP 路径、越权信息、兜底遥测。

test_invariants.py 测的是引擎（用合成 agent）。这个文件测的是
agents/ 目录——也就是接真模型时真正会跑的那条路：
system_prompt() 怎么拼、LLMClient 怎么发 HTTP、返回值怎么解析。

    python tests/test_agents.py        # 全程本地，不联网、不花钱
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import AgentPool, LLMClient   # noqa: E402
from agents.player_agent import PlayerAgent  # noqa: E402
from engine import Game                    # noqa: E402
from engine.models import Faction          # noqa: E402

HERE = Path(__file__).resolve().parent
PORT = 8793
FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("     ✓", msg)
    else:
        FAILED.append(msg)
        print("     ✗", msg)


# ---------- 抓取真正送进模型的 prompt ----------

_SEAT_RE = re.compile(r"【你的身份】(\d+)号")


def recording_client(llm: LLMClient) -> tuple[LLMClient, dict[int, list[str]]]:
    """包一层记录器：留下每个座位实际收到的 system+user 全文。"""
    seen: dict[int, list[str]] = {}
    original = llm.chat

    def chat(system: str, messages: list[dict], **kw) -> str:
        m = _SEAT_RE.search(system)
        if m:
            blob = system + "\n" + "\n".join(x["content"] for x in messages)
            seen.setdefault(int(m.group(1)), []).append(blob)
        return original(system, messages, **kw)

    llm.chat = chat  # type: ignore[method-assign]
    return llm, seen


def play(base_url: str, seed: int, **kw) -> tuple[Game, dict[int, list[str]]]:
    llm = LLMClient(base_url=base_url, api_key="test-key", model="mock-model",
                    mock=False, timeout=10, max_retries=0, **kw)
    llm, seen = recording_client(llm)
    pool = AgentPool(llm)
    g = Game(agent_fn=pool, seed=seed)
    pool.bind(g)
    g.run()
    return g, seen


# ---------- 1. 真实 HTTP 路径 ----------

def test_http_path() -> None:
    print("[1/5] 真实 HTTP 路径（openai SDK → 本地假端点）")
    g, seen = play(f"http://127.0.0.1:{PORT}/v1", seed=7)
    check(g.winner is not None, f"整局跑完，胜方 {g.winner.value if g.winner else None}")
    check(g.decision_count > 30, f"确实发生了模型调用（{g.decision_count} 次决策）")
    check(len(seen) >= 6, f"多个座位都走了 agent 层（{len(seen)} 个座位有记录）")
    check(g.fallback_rate < 0.35,
          f"正常端点下兜底率低（{g.fallback_rate:.1%}）")


# ---------- 2. prompt 越权（核心） ----------

def test_prompt_isolation() -> None:
    """test_invariants 只验证 visible_events；真正送进模型的是拼好的字符串。

    prompt 拼装那一层手滑，引擎层的隔离测试是抓不到的。这里直接检查
    每个座位收到的全部 prompt 文本里，有没有出现它看不见的事件原文。
    """
    print("[2/5] 送进模型的 prompt 不含越权信息")
    leaks: list[str] = []
    partner_leaks: list[str] = []
    checked = 0

    for seed in (11, 12, 13):
        g, seen = play(f"http://127.0.0.1:{PORT}/v1", seed=seed)
        for seat, blobs in seen.items():
            blob = "\n".join(blobs)
            visible = {id(e) for e in g.visible_events(seat)}
            for ev in g.events:
                if id(ev) in visible or not ev.text.strip():
                    continue
                checked += 1
                if ev.text in blob:
                    leaks.append(f"seed={seed} seat={seat} 看到了不该看的：{ev.kind} «{ev.text[:40]}»")
            # 同伴名单只能出现在回响体自己的 prompt 里
            if g.p(seat).faction is not Faction.ECHO and "你的同伴是" in blob:
                partner_leaks.append(f"seed={seed} seat={seat} 非回响体却拿到了同伴名单")

    check(not leaks, f"{checked} 条不可见事件，无一出现在对应座位的 prompt 里"
                     + ("" if not leaks else f" —— {leaks[0]}"))
    check(not partner_leaks, "同伴名单只出现在回响体的 prompt 里"
                             + ("" if not partner_leaks else f" —— {partner_leaks[0]}"))


# ---------- 3. 端点吐垃圾 ----------

def test_garbage_endpoint() -> None:
    print("[3/5] 端点持续返回垃圾（空串 / 代码块 / 超范围数字 / 乱码）")
    winners, crashed = [], 0
    total_fb, total_dec = 0, 0
    for seed in range(20):
        try:
            g, _ = play(f"http://127.0.0.1:{PORT}/garbage", seed=seed)
            winners.append(g.winner)
            total_fb += g.fallback_count
            total_dec += g.decision_count
        except Exception as e:  # noqa: BLE001
            crashed += 1
            print("       崩溃:", type(e).__name__, e)
    check(crashed == 0, f"20 局零崩溃")
    check(all(w is not None for w in winners), "每局都判出了胜方")
    check(total_fb > 0, f"兜底确实被触发并计数（{total_fb}/{total_dec} 次决策）")
    # 垃圾里混着"我觉得是3号"这种能被 _first_int 救回来的，所以不会是 100%；
    # 只要显著高于正常端点（那边是 0%）就说明遥测反映得出模型质量。
    check(total_fb / total_dec > 0.3,
          f"垃圾端点下兜底率显著偏高（{total_fb / total_dec:.1%}）—— 遥测能反映模型质量")


# ---------- 4. 端点 500 ----------

def test_dead_endpoint() -> None:
    print("[4/5] 端点全部 500（模型挂了）")
    g, _ = play(f"http://127.0.0.1:{PORT}/bad", seed=5)
    check(g.winner is not None, "对局仍然跑完并判出胜方")
    texts = [e.text for e in g.events if e.kind in ("speech", "free_speech", "defense")]
    check(any(t.startswith("[通讯中断") for t in texts),
          "发言降级为可见的通讯中断标记，而不是静默空白")
    check(g.fallback_count > 0, f"兜底被记录（{g.fallback_count} 次）")


# ---------- 5. 兜底遥测本身 ----------

def test_telemetry() -> None:
    print("[5/5] 兜底遥测的准确性")
    g = Game(agent_fn=lambda r: "完全不是数字", seed=3)
    g.run()
    seat_kinds = {k for k in g.fallback_by_kind if k.startswith("night_") or k == "vote"}
    check(g.fallback_count > 0, f"合成垃圾 agent 触发了 {g.fallback_count} 次兜底")
    check(bool(seat_kinds), f"兜底按决策类型归类：{sorted(g.fallback_by_kind)}")
    check(0.0 < g.fallback_rate <= 1.0, f"兜底率在合法区间（{g.fallback_rate:.1%}）")

    snap = g.snapshot()
    check("fallback_rate" in snap and "fallback_by_kind" in snap,
          "snapshot 暴露了遥测字段，前端/CLI 能读到")

    clean = Game(agent_fn=lambda r: (r.options[0] if r.options else 0), seed=3)
    clean.run()
    check(clean.fallback_count == 0,
          f"合法 agent 不产生任何兜底（{clean.fallback_count} 次）—— 计数器没有误报")


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "mock_llm_server.py"), str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                       data=b"{}", timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            print("假端点没起来")
            return 1

        test_http_path()
        test_prompt_isolation()
        test_garbage_endpoint()
        test_dead_endpoint()
        test_telemetry()
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过")
        for f in FAILED:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
