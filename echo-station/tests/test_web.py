"""观战服务测试：事件扇出、多人同时观战、中途接入。

这条路以前是坏的——所有 WebSocket 连接共用同一个 queue.Queue，
两个观众会把事件"瓜分"掉，谁都看不到完整对局。

[1] 走真 WebSocket（TestClient，不占端口）
[2-4] 直接测 Session 的订阅/广播语义（WS handler 只是它的一层薄壳）

    python tests/test_web.py
"""
from __future__ import annotations

import os
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_MOCK", "1")
os.environ.setdefault("GAME_SPEED", "0")      # 测试里不需要节流

from fastapi.testclient import TestClient      # noqa: E402

from web.server import Session, app            # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("     ✓", msg)
    else:
        FAILED.append(msg)
        print("     ✗", msg)


def fp(m: dict) -> tuple:
    return (m.get("round"), m.get("kind"), m.get("actor"), m.get("text"))


def drain_ws(ws, limit: int = 4000) -> list[tuple]:
    out = []
    for _ in range(limit):
        m = ws.receive_json()
        if m.get("kind") == "__roster__":
            continue
        if m.get("kind") == "__end__":
            return out
        out.append(fp(m))
    raise AssertionError("事件太多，没等到 __end__")


def drain_q(q: queue.Queue, timeout: float = 90.0) -> list[tuple]:
    """按 WS handler 的方式消费一个订阅队列。"""
    out = []
    while True:
        m = q.get(timeout=timeout)
        if m.get("kind") == "__roster__":
            continue
        if m.get("kind") == "__end__":
            return out
        out.append(fp(m))


# ---------- 1. 真 WebSocket ----------

def test_two_viewers_ws() -> None:
    print("[1/4] 两个观众同时观战（真 WebSocket）")
    client = TestClient(app)
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        r = client.post("/api/start?seed=42").json()
        check(r["started"] is True, "/api/start 开局成功")
        ea, eb = drain_ws(a), drain_ws(b)

    check(len(ea) > 20, f"A 收到完整对局（{len(ea)} 条事件）")
    check(len(eb) > 20, f"B 收到完整对局（{len(eb)} 条事件）")
    check(ea == eb, "两个观众的事件流逐条一致（不再互相抢事件）")


# ---------- 2. 中途接入 ----------

def test_late_joiner() -> None:
    print("[2/4] 中途接入")
    s = Session()
    qa, _ = s.subscribe()
    late: list[queue.Queue] = []
    backlog: list[dict] = []
    original = s.publish
    n = {"i": 0}

    def counting(item):
        original(item)
        n["i"] += 1
        if n["i"] == 30 and not late:           # 对局跑到一半才连进来
            q, bl = s.subscribe()
            late.append(q)
            backlog.extend(bl)

    s.publish = counting                        # type: ignore[method-assign]
    assert s.start(7) is True
    s.thread.join(timeout=120)
    check(not s.thread.is_alive(), "引擎线程正常结束")

    ea = drain_q(qa)
    check(bool(late), "确实在对局中途接入了")
    joined = [fp(m) for m in backlog if m.get("kind") != "__roster__"] + drain_q(late[0])
    check(len(backlog) > 0, f"补发了 {len(backlog)} 条历史，中途接入不是空屏")
    check(joined == ea, f"补发历史 + 后续事件 == 全程事件流（{len(joined)} vs {len(ea)}）")


# ---------- 3. 退订 ----------

def test_unsubscribe() -> None:
    print("[3/4] 断开的观众不再占用广播")
    s = Session()
    qa, _ = s.subscribe()
    qb, _ = s.subscribe()
    s.unsubscribe(qb)
    s.publish({"kind": "system", "text": "hi", "round": 0})
    check(qa.qsize() == 1, "在线观众照常收到")
    check(qb.qsize() == 0, "已断开的队列不再被投递")
    check(len(s.subscribers) == 1, "订阅表被正确清理，不泄漏")


# ---------- 4. 重复开局 ----------

def test_start_guard() -> None:
    print("[4/4] 重复开局保护")
    s = Session()
    q, _ = s.subscribe()
    check(s.start(3) is True, "第一次 start() 真的开了新局")
    check(s.start(99) is False, "对局进行中再 start() 不会开第二局，如实返回 False")
    s.thread.join(timeout=120)
    drain_q(q)
    check(s.running is False, "对局结束后 running 归位")


def main() -> int:
    test_two_viewers_ws()
    test_late_joiner()
    test_unsubscribe()
    test_start_guard()
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
