"""FastAPI + WebSocket 实时观战服务。

引擎是同步阻塞的，跑在后台线程里；事件通过线程安全队列推给 WebSocket。
"""
from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import AgentPool, LLMClient  # noqa: E402
from engine import Game  # noqa: E402
from engine.models import Event  # noqa: E402

BASE = Path(__file__).resolve().parent
app = FastAPI(title="深空回响 · 观战")


class Session:
    """一场对局：后台线程跑引擎，事件扇出(fan-out)给每个订阅者。

    每个 WebSocket 连接持有自己的队列——共用一个队列会让多个观众
    互相"抢"事件，谁都看不到完整对局。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.subscribers: list[queue.Queue] = []
        self.history: list[dict] = []
        self.game: Optional[Game] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.speed = float(os.getenv("GAME_SPEED", "1.2"))  # 每条事件之间的停顿(秒)

    # ---------- 订阅 ----------

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        """原子地取历史快照并注册队列，避免中途接入时漏事件或收重复。"""
        q: queue.Queue = queue.Queue()
        with self.lock:
            backlog = list(self.history)
            self.subscribers.append(q)
        return q, backlog

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def publish(self, item: dict) -> None:
        with self.lock:
            if item.get("kind") != "__end__":
                self.history.append(item)
            targets = list(self.subscribers)
        for q in targets:
            q.put(item)

    # ---------- 对局 ----------

    def start(self, seed: Optional[int] = None) -> bool:
        if self.running:
            return False
        with self.lock:
            self.history.clear()

        llm = LLMClient()
        pool = AgentPool(llm)
        game = Game(agent_fn=pool, seed=seed)
        pool.bind(game)
        self.game = game

        def emit(ev: Event):
            payload = ev.to_dict()
            p = game.p(ev.actor) if ev.actor is not None else None
            payload["actor_name"] = p.name if p else None
            payload["actor_label"] = p.label if p else None
            self.publish(payload)
            self.publish({"kind": "__roster__", "roster": self.roster()})
            # 节流：让观众跟得上（mock 模式下引擎跑得飞快）
            if ev.kind in ("speech", "free_speech", "defense", "echo_chat", "death"):
                time.sleep(self.speed)
            elif ev.kind in ("vote", "info"):
                time.sleep(self.speed * 0.35)

        game.emit_cb = emit
        self.running = True

        def run():
            try:
                game.run()
            except Exception as e:
                self.publish({
                    "kind": "system", "text": f"[引擎异常] {type(e).__name__}: {e}",
                    "public": True, "round": game.round_no, "phase": "game_over",
                    "actor": None,
                })
            finally:
                self.running = False
                self.publish({"kind": "__end__", "snapshot": game.snapshot()})

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return True

    def roster(self) -> list[dict]:
        if not self.game:
            return []
        return [
            {"seat": p.seat, "name": p.name, "persona": p.persona, "alive": p.alive}
            for p in self.game.players
        ]


session = Session()


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/start")
async def start(seed: Optional[int] = None):
    started = session.start(seed)
    return {
        "ok": True,
        "started": started,          # False = 已有对局在跑，本次未新开
        "running": session.running,
        "roster": session.roster(),
    }


@app.get("/api/state")
async def state():
    return {
        "running": session.running,
        "roster": session.roster(),
        "snapshot": session.game.snapshot() if session.game else None,
    }


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q, backlog = session.subscribe()
    try:
        # 补发历史，便于中途接入
        for item in backlog:
            await sock.send_json(item)
        if session.game:
            await sock.send_json({"kind": "__roster__", "roster": session.roster()})

        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.08)
                continue
            await sock.send_json(item)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.unsubscribe(q)


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
