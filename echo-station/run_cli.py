"""命令行跑一局，用于验证引擎。

    LLM_MOCK=1 python run_cli.py            # 假回复，秒出结果
    LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=... python run_cli.py
"""
from __future__ import annotations

import sys

from agents import AgentPool, LLMClient
from engine import Game
from engine.models import Event

C = {
    "system": "\033[90m", "speech": "\033[97m", "free_speech": "\033[37m",
    "defense": "\033[93m", "vote": "\033[36m", "death": "\033[91m",
    "info": "\033[95m", "echo_chat": "\033[35m", "reset": "\033[0m",
}


def make_printer(game: Game):
    def show(ev: Event):
        color = C.get(ev.kind, "")
        who = game.p(ev.actor).label if ev.actor else ""
        tag = {"echo_chat": "密谈", "info": "私密", "vote": "投票",
               "defense": "辩护"}.get(ev.kind, "")
        prefix = f"[{tag}]" if tag else ""
        if ev.kind in ("speech", "free_speech", "defense", "echo_chat"):
            print(f"{color}{prefix}{who}：{ev.text}{C['reset']}")
        elif ev.kind == "info":
            print(f"{color}{prefix}→{who} {ev.text}{C['reset']}")
        else:
            print(f"{color}{ev.text}{C['reset']}")
    return show


def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else None
    llm = LLMClient()
    pool = AgentPool(llm)
    game = Game(agent_fn=pool, seed=seed)
    game.emit_cb = make_printer(game)
    pool.bind(game)

    print(f"{C['system']}模式：{'MOCK' if llm.mock else llm.model}  种子：{seed}{C['reset']}")
    print(f"{C['system']}真实身份：" + "、".join(
        f"{p.label}={p.role_cn}" for p in game.players) + C["reset"])
    print()

    winner = game.run()
    print(f"\n{C['death']}胜方：{winner.value}{C['reset']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
