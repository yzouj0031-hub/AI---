"""引擎不变量测试：胜负一致性、信息隔离、畸形输入健壮性。

    cd echo-station && python tests/test_invariants.py

不依赖 pytest，直接跑。任何一项失败会以非零退出码结束。
"""
from __future__ import annotations

import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Game  # noqa: E402
from engine.models import Faction, Role  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILED.append(msg)
        print("  ✗", msg)


def dumb_agent(req):
    """确定性假 agent：永远选第一个合法选项。"""
    if req.kind in ("speech", "free_speech", "defense", "echo_chat"):
        return "……"
    if req.kind == "night_engineer":
        return "舰桥"
    if req.kind == "night_wiretap":
        return 0
    opts = [o for o in req.options if o != 0]
    return opts[0] if opts else 0


def random_agent(seed):
    r = random.Random(seed)

    def f(req):
        if req.kind in ("speech", "free_speech", "defense", "echo_chat"):
            return "……"
        if req.kind == "night_engineer":
            return "舰桥"
        if req.kind == "night_wiretap":
            return 0
        opts = [o for o in req.options if o != 0]
        return r.choice(opts) if opts else 0

    return f


# ---------------------------------------------------------------- 胜负一致性

def test_win_conditions(n=300):
    print(f"[1/4] 胜负判定一致性（{n} 局）")
    res = collections.Counter()
    for seed in range(n):
        g = Game(agent_fn=dumb_agent, seed=seed)
        w = g.run()
        res[w.value] += 1
        alive = g.alive
        echo_n = len([p for p in alive if p.faction is Faction.ECHO])
        human_n = len([p for p in alive if p.faction is Faction.CREW])
        salv = g.by_role(Role.SALVAGER)

        if w is Faction.CREW:
            check(echo_n == 0, f"seed={seed} 人类获胜但仍有回响体存活")
        elif w is Faction.ECHO:
            check(echo_n > human_n or g.round_no >= 12,
                  f"seed={seed} 回响体获胜但数量未超过人类")
        elif w is Faction.SALVAGER:
            check(salv is not None and len(alive) <= 2,
                  f"seed={seed} 拾荒者获胜但存活人数 != 2")
        check(g.round_no <= 12, f"seed={seed} 回合数超上限")
    print("     分布:", dict(res))


# ---------------------------------------------------------------- 信息隔离

def test_isolation(n=120):
    print(f"[2/4] 信息隔离（{n} 局，逐条检查每个座位的可见事件）")
    checked = 0
    for seed in range(n):
        g = Game(agent_fn=dumb_agent, seed=seed)
        g.run()
        echo_seats = {p.seat for p in g.players if p.faction is Faction.ECHO}
        for p in g.players:
            for ev in g.visible_events(p.seat):
                checked += 1
                if ev.kind == "echo_chat":
                    check(p.seat in echo_seats,
                          f"seed={seed} 座位{p.seat}(非回响体) 看到了密谈")
                if ev.kind == "info":
                    check(ev.actor == p.seat,
                          f"seed={seed} 座位{p.seat} 看到了属于 {ev.actor} 的私密情报")
    print(f"     检查了 {checked} 条可见事件")

    # 反向：回响体必须能看到自己的密谈
    g = Game(agent_fn=dumb_agent, seed=3)
    g.run()
    es = [p.seat for p in g.players if p.faction is Faction.ECHO]
    got = [e for e in g.visible_events(es[0]) if e.kind == "echo_chat"]
    check(len(got) > 0, "回响体看不到自己的密谈（隔离过度）")


# ---------------------------------------------------------------- 健壮性

JUNK = ["", None, "我觉得是3号但也可能是5号", "abc", "-1", "999", "0",
        "3.7", [], {}, "第七号", "```json\n{\"x\":1}\n```", "  4  ", "Ⅷ"]


def test_malformed(n=400):
    print(f"[3/4] 畸形返回值健壮性（{n} 局，模拟真实 LLM 的垃圾输出）")
    res = collections.Counter()
    for seed in range(n):
        r = random.Random(seed)

        def chaos(req):
            if req.kind in ("speech", "free_speech", "defense", "echo_chat"):
                return r.choice(["...", "", None, "x" * 500])
            return r.choice(JUNK)

        try:
            g = Game(agent_fn=chaos, seed=seed)
            res[g.run().value] += 1
        except Exception as e:  # noqa: BLE001
            check(False, f"seed={seed} 崩溃: {type(e).__name__}: {e}")
    print("     分布:", dict(res))


# ---------------------------------------------------------------- 平衡性

def test_balance(n=300):
    """胜率必须对推理能力敏感 —— 否则说明博弈没有意义。"""
    print(f"[4/4] 平衡性对推理能力的敏感度（每档 {n} 局）")
    rates = {}
    for p in (0.0, 0.35, 0.65):
        res = collections.Counter()
        for seed in range(n):
            g = Game(agent_fn=lambda r: 0, seed=seed)
            r = random.Random(seed)

            def skilled(req, _g=g, _r=r, _p=p):
                if req.kind in ("speech", "free_speech", "defense", "echo_chat"):
                    return "……"
                if req.kind == "night_engineer":
                    return "舰桥"
                if req.kind == "night_wiretap":
                    return 0
                opts = [o for o in req.options if o != 0]
                if not opts:
                    return 0
                me = _g.p(req.seat)
                if req.kind == "vote":
                    if me.faction is Faction.CREW and _r.random() < _p:
                        echo = [s for s in opts if _g.p(s).faction is Faction.ECHO]
                        if echo:
                            return _r.choice(echo)
                    if me.faction is Faction.ECHO:
                        hum = [s for s in opts if _g.p(s).faction is not Faction.ECHO]
                        if hum:
                            return _r.choice(hum)
                return _r.choice(opts)

            g.agent_fn = skilled
            res[g.run().value] += 1
        rates[p] = res["crew"] / n
        print(f"     推理准确率 {p:.0%} → 人类胜率 {rates[p]:.1%}")

    check(rates[0.0] < rates[0.35] < rates[0.65],
          "人类胜率未随推理能力单调上升 —— 博弈失去意义")
    check(rates[0.0] < 0.25, "随机乱投也能赢太多 —— 游戏太容易")
    check(rates[0.65] > 0.6, "强推理也赢不了 —— 游戏太难")


if __name__ == "__main__":
    test_win_conditions()
    test_isolation()
    test_malformed()
    test_balance()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败")
        raise SystemExit(1)
    print("✅ 全部通过")
