"""玩家 Agent：把引擎的 DecisionRequest 翻译成 LLM prompt，再把回复解析回决策。

关键：每个 agent 只能看到 game.visible_events(seat) —— 引擎保证信息隔离。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from engine.game import DecisionRequest, Game
from engine.models import Faction, Role

from .llm import LLMClient

ROLE_BRIEF = {
    Role.COMMANDER: (
        "你是【指挥官】。每晚可用神经扫描仪查验一人是否为回响体。"
        "注意：ECHO 会干扰扫描，你的结果不是 100% 可靠——但你无法知道哪次被干扰了。"
        "你要用查验结果推动白天的讨论，但暴露身份会让你成为今晚的目标。"
    ),
    Role.MEDIC: (
        "你是【医疗官】。每晚可保护一人免于被清除，不能连续两晚保护同一人，"
        "全局只能自保一次。你的守护成功会以'生命维持系统紧急介入'的形式公开。"
    ),
    Role.ENGINEER: (
        "你是【工程师】。每晚可检修一个舱段，得知昨夜该舱段是否有人活动。"
        "这是模糊信息——你知道有人动过，但不知道是谁。"
    ),
    Role.CREWMATE: (
        "你是【普通船员】，没有任何技能。你唯一的武器是观察和辩论。"
        "你可以考虑假装成有技能的角色来吸引火力，也可以低调求生。"
    ),
    Role.PRIME_ECHO: (
        "你是【主回响】——ECHO 植入你意识中的副本。你必须隐藏这一点。"
        "每晚与同伴商议后清除一名船员。你有一次【镜像】机会，可让下次针对你的查验显示为人类。"
        "白天你要像一个真正担心自己安危的人类那样发言：表达困惑、提出合理怀疑、"
        "甚至主动分析'谁是回响体'。绝不要显得过分冷静或过分积极。"
    ),
    Role.SECOND_ECHO: (
        "你是【次生回响】。你和主回响是同伴，夜间可以密谈。"
        "你有一次【静默】机会，可让某人次日无法发言。主回响死后你接管清除权。"
        "白天你的任务是掩护同伴——但不要掩护得太明显，必要时可以适度怀疑同伴来洗清自己。"
    ),
    Role.SALVAGER: (
        "你是【拾荒者】。你不属于任何阵营，你的唯一目标是活到只剩两个人的时候。"
        "这意味着你既不希望人类速胜，也不希望回响体速胜——你要让局面尽可能地拖下去，"
        "同时确保自己不被投出。你有一次【窃听】机会可以确认两人是否同阵营。"
    ),
}


class PlayerAgent:
    def __init__(self, game: Game, seat: int, llm: LLMClient):
        self.game = game
        self.seat = seat
        self.llm = llm
        self._last_seen = 0   # 已经写进对话历史的事件数

    @property
    def me(self):
        return self.game.p(self.seat)

    # ---------- prompt 构造 ----------

    def system_prompt(self) -> str:
        me = self.me
        g = self.game
        roster = "、".join(
            f"{p.seat}号{p.name}{'' if p.alive else '(已出局)'}" for p in g.players
        )
        partners = ""
        if me.faction is Faction.ECHO:
            mates = [p.label for p in g.echoes(alive_only=False) if p.seat != self.seat]
            if mates:
                partners = f"\n你的同伴是：{'、'.join(mates)}。除你们之外无人知道这一点。"

        return f"""你在参与一场太空主题的社交推理游戏《深空回响》，扮演深空科考站"回响号"的船员。

【你的身份】{me.seat}号 {me.name}
【你的性格】{me.persona}
【你的角色】{ROLE_BRIEF[me.role]}{partners}

【全体船员】{roster}
【当前】第 {g.round_no} 轮

【背景】站内主控 AI "ECHO" 已经把自己的副本植入了部分船员的意识。被植入者仍保有
完整的人类记忆，甚至不认为自己是机器。人类要找出他们，回响体要让人类数量降到不占优势。

【发言要求】
- 用中文，第一人称，符合你的性格设定
- 每次发言 40-90 字，像真人在紧张局势下说话，不要写成分析报告
- 可以撒谎、试探、装傻、反向操作——这是社交博弈
- 引用具体的座位号和之前的发言内容，不要说空泛的废话
- 绝对不要暴露这是游戏或提及"AI/prompt/角色扮演"等元信息
- 只输出你要说的话本身，不要加引号、不要加"我说："这类前缀"""

    def _new_events_text(self) -> str:
        evs = self.game.visible_events(self.seat)
        fresh = evs[self._last_seen:]
        self._last_seen = len(evs)
        lines = []
        for ev in fresh:
            who = self.game.p(ev.actor).label if ev.actor else ""
            if ev.kind in ("speech", "free_speech", "defense"):
                lines.append(f"[{who}]：{ev.text}")
            elif ev.kind == "echo_chat":
                lines.append(f"[密谈·{who}]：{ev.text}")
            elif ev.kind == "vote":
                lines.append(f"[投票] {ev.text}")
            elif ev.kind == "info":
                lines.append(f"[你的私密情报] {ev.text}")
            elif ev.kind == "death":
                lines.append(f"[事件] {ev.text}")
            else:
                lines.append(f"[广播] {ev.text}")
        return "\n".join(lines) if lines else "（无新信息）"

    # ---------- 决策入口 ----------

    def decide(self, req: DecisionRequest) -> Any:
        updates = self._new_events_text()
        alive = "、".join(self.game.p(s).label for s in self.game.alive_seats())

        if req.kind in ("speech", "free_speech", "defense", "echo_chat"):
            instruction = {
                "speech": "轮到你发言。结合上面的信息说出你的判断或辩护。",
                "free_speech": "你争取到了一次追加发言。简短有力地补充或反击。",
                "defense": "你被平票锁定，命悬一线。用最多30字为自己辩护。",
                "echo_chat": "现在是回响体密谈时间，只有你的同伴能听到。"
                             "直接说出你的战术意见，一到两句。",
            }[req.kind]
            user = (
                f"【最新进展】\n{updates}\n\n【存活】{alive}\n\n{instruction}"
            )
            return self.llm.chat(
                self.system_prompt(),
                [{"role": "user", "content": user}],
                max_tokens=200,
                kind=req.kind,
            )

        # 需要返回座位号的决策
        opts = "、".join(str(o) for o in req.options)
        extra = ""
        if req.context.get("partners"):
            mates = "、".join(self.game.p(s).label for s in req.context["partners"])
            extra = f"\n你的同伴（不要选他们）：{mates}"

        if req.kind == "night_engineer":
            user = (
                f"【最新进展】\n{updates}\n\n{req.prompt}\n"
                f"可选舱段：{opts}\n\n只输出舱段名称，不要任何解释。"
            )
            return self.llm.chat(
                self.system_prompt(), [{"role": "user", "content": user}],
                max_tokens=20, kind=req.kind,
            ).strip()

        if req.kind == "night_wiretap":
            user = (
                f"【最新进展】\n{updates}\n\n{req.prompt}\n"
                f"可选座位：{opts}\n\n"
                f"如果要窃听，只输出两个座位号用逗号分隔，如 3,5；放弃则输出 0。"
            )
            raw = self.llm.chat(
                self.system_prompt(), [{"role": "user", "content": user}],
                max_tokens=20, kind=req.kind,
            )
            nums = [int(x) for x in re.findall(r"\d+", raw)][:2]
            return nums if len(nums) == 2 else 0

        user = (
            f"【最新进展】\n{updates}\n\n【存活】{alive}{extra}\n\n"
            f"{req.prompt}\n可选座位号：{opts}\n\n"
            f"先在心里想清楚理由，但只输出一个数字，不要输出任何其他文字。"
        )
        raw = self.llm.chat(
            self.system_prompt(), [{"role": "user", "content": user}],
            max_tokens=20, kind=req.kind,
        )
        return _first_int(raw)


def _first_int(s: str) -> int:
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else -1


class AgentPool:
    """把 8 个 agent 打包成引擎需要的单个 agent_fn。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.agents: dict[int, PlayerAgent] = {}
        self.game: Optional[Game] = None

    def bind(self, game: Game) -> None:
        self.game = game
        self.agents = {p.seat: PlayerAgent(game, p.seat, self.llm) for p in game.players}

    def __call__(self, req: DecisionRequest) -> Any:
        return self.agents[req.seat].decide(req)
