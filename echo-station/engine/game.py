"""游戏状态机：夜晚结算、白天发言投票、胜负判定。

引擎不关心 agent 是 LLM 还是 mock —— 它通过 `decide(request)` 协议索取决策。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import (
    DEFAULT_ROLES,
    SECTIONS,
    DeathCause,
    Event,
    Faction,
    NightActions,
    Phase,
    Player,
    Role,
)

# 座位人格池
PERSONAS = [
    ("卡莱", "冷静的逻辑派，说话像在写报告，讨厌情绪化的指控"),
    ("温蒂", "警觉且尖锐，喜欢主动施压，第一个跳出来质疑别人"),
    ("周", "沉默寡言，发言极短，但每句都指向具体的行为矛盾"),
    ("伊莎", "感性且善于观察语气变化，常从'谁听起来不像自己'切入"),
    ("雷诺", "自嘲式幽默，用玩笑降低戒心，实际记性极好"),
    ("阿米娜", "谨慎的中立派，倾向于综合各方观点后再表态"),
    ("塔可", "急躁，容易被带节奏，但直觉偶尔准得吓人"),
    ("薇拉", "老练的谈判者，喜欢设置语言陷阱试探别人的反应"),
    ("恩佐", "话痨型，喜欢复盘每一轮的投票走向，信息记得又多又碎"),
    ("琳", "冷淡而精确，几乎不表态，一旦开口就是直接点名"),
]


@dataclass
class DecisionRequest:
    """引擎向 agent 索取一个决策。"""
    seat: int
    kind: str              # night_purge / night_protect / ... / speech / vote
    prompt: str            # 人类可读的情境描述
    options: list[Any] = field(default_factory=list)
    context: dict = field(default_factory=dict)


AgentFn = Callable[[DecisionRequest], Any]


class Game:
    def __init__(
        self,
        agent_fn: AgentFn,
        seed: Optional[int] = None,
        emit: Optional[Callable[[Event], None]] = None,
    ):
        self.rng = random.Random(seed)
        self.agent_fn = agent_fn
        self.emit_cb = emit
        self.events: list[Event] = []
        self.round_no = 0
        self.phase = Phase.SETUP
        self.winner: Optional[Faction] = None
        self.win_reason = ""
        self.inspect_count = 0          # 用于 1/3 反转节律
        self.vote_history: list[dict] = []
        self.players: list[Player] = self._setup_players()

    # ---------- 初始化 ----------

    def _setup_players(self) -> list[Player]:
        roles = DEFAULT_ROLES[:]
        self.rng.shuffle(roles)
        personas = PERSONAS[:]
        self.rng.shuffle(personas)
        players = []
        for i, role in enumerate(roles):
            name, persona = personas[i]
            players.append(Player(seat=i + 1, name=name, persona=persona, role=role))
        return players

    # ---------- 便捷访问 ----------

    def p(self, seat: int) -> Player:
        return self.players[seat - 1]

    @property
    def alive(self) -> list[Player]:
        return [p for p in self.players if p.alive]

    def alive_seats(self) -> list[int]:
        return [p.seat for p in self.alive]

    def by_role(self, role: Role) -> Optional[Player]:
        for p in self.players:
            if p.role == role and p.alive:
                return p
        return None

    def echoes(self, alive_only: bool = True) -> list[Player]:
        return [
            p for p in self.players
            if p.faction is Faction.ECHO and (p.alive or not alive_only)
        ]

    # ---------- 事件 ----------

    def log(
        self,
        kind: str,
        text: str,
        actor: Optional[int] = None,
        visible_to: Optional[list[int]] = None,
        **meta,
    ) -> Event:
        ev = Event(
            round_no=self.round_no,
            phase=self.phase,
            kind=kind,
            actor=actor,
            text=text,
            visible_to=visible_to,
            meta=meta,
        )
        self.events.append(ev)
        if self.emit_cb:
            self.emit_cb(ev)
        return ev

    def visible_events(self, seat: int) -> list[Event]:
        """某个座位能合法看到的全部事件。"""
        out = []
        for ev in self.events:
            if ev.is_public() or (ev.visible_to and seat in ev.visible_to):
                out.append(ev)
        return out

    def ask(self, req: DecisionRequest) -> Any:
        return self.agent_fn(req)

    # ---------- 夜晚 ----------

    def run_night(self) -> None:
        self.round_no += 1
        self.phase = Phase.NIGHT
        self.log("system", f"—— 第 {self.round_no} 夜：主照明关闭，全员回舱 ——")

        acts = NightActions()
        echo_seats = [p.seat for p in self.echoes()]

        # 1. 回响体密谈
        if len(echo_seats) > 1:
            for seat in echo_seats:
                req = DecisionRequest(
                    seat=seat,
                    kind="echo_chat",
                    prompt="与同伴密谈，讨论今晚清除谁、明天如何伪装。",
                    options=self.alive_seats(),
                    context={"partners": [s for s in echo_seats if s != seat]},
                )
                msg = self.ask(req)
                if msg:
                    acts.echo_chat.append((seat, msg))
                    self.log(
                        "echo_chat", msg, actor=seat, visible_to=echo_seats,
                    )

        # 2. 清除决策（主回响优先，死则次生接管）
        killer = self.by_role(Role.PRIME_ECHO) or self.by_role(Role.SECOND_ECHO)
        if killer:
            targets = [s for s in self.alive_seats() if s not in echo_seats]
            if targets:
                req = DecisionRequest(
                    seat=killer.seat,
                    kind="night_purge",
                    prompt="选择今晚要清除的船员。",
                    options=targets,
                    context={"partners": [s for s in echo_seats if s != killer.seat]},
                )
                acts.purge_target = self._coerce_seat(self.ask(req), targets)

        # 3. 次生回响：静默
        second = self.by_role(Role.SECOND_ECHO)
        if second and not second.used_silence:
            targets = [s for s in self.alive_seats() if s not in echo_seats]
            req = DecisionRequest(
                seat=second.seat,
                kind="night_silence",
                prompt="是否使用【静默】使某人明天无法发言？（可放弃，返回 0）",
                options=[0] + targets,
                context={},
            )
            choice = self._coerce_seat(self.ask(req), targets, allow_zero=True)
            if choice:
                acts.silence_target = choice
                second.used_silence = True

        # 4. 医疗官守护
        medic = self.by_role(Role.MEDIC)
        if medic:
            targets = [
                s for s in self.alive_seats()
                if s != medic.last_protected
                and (s != medic.seat or not medic.used_self_heal)
            ]
            if targets:
                req = DecisionRequest(
                    seat=medic.seat,
                    kind="night_protect",
                    prompt="选择今晚要用生命维持舱保护的人。",
                    options=targets,
                    context={"last_protected": medic.last_protected},
                )
                t = self._coerce_seat(self.ask(req), targets)
                acts.protect_target = t
                medic.last_protected = t
                if t == medic.seat:
                    medic.used_self_heal = True

        # 5. 指挥官查验
        cmd = self.by_role(Role.COMMANDER)
        if cmd:
            targets = [s for s in self.alive_seats() if s != cmd.seat]
            if targets:
                req = DecisionRequest(
                    seat=cmd.seat,
                    kind="night_inspect",
                    prompt="选择今晚要用神经扫描仪查验的人。",
                    options=targets,
                )
                t = self._coerce_seat(self.ask(req), targets)
                acts.inspect_target = t
                self._resolve_inspect(cmd, t)

        # 6. 工程师检修
        eng = self.by_role(Role.ENGINEER)
        if eng:
            req = DecisionRequest(
                seat=eng.seat,
                kind="night_engineer",
                prompt="选择要检修的舱段，可得知昨夜该舱段是否有人活动。",
                options=SECTIONS,
            )
            sec = self.ask(req)
            if sec not in SECTIONS:
                sec = self.rng.choice(SECTIONS)
            acts.engineer_section = sec
            self._resolve_engineer(eng, sec, acts)

        # 7. 拾荒者窃听
        salv = self.by_role(Role.SALVAGER)
        if salv and not salv.used_wiretap:
            others = [s for s in self.alive_seats() if s != salv.seat]
            if len(others) >= 2:
                req = DecisionRequest(
                    seat=salv.seat,
                    kind="night_wiretap",
                    prompt="是否使用【窃听】判断两人是否同阵营？返回两个座位号，或 0 放弃。",
                    options=others,
                )
                pair = self.ask(req)
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    a, b = pair
                    if a in others and b in others and a != b:
                        salv.used_wiretap = True
                        same = self.p(a).faction is self.p(b).faction
                        self.log(
                            "info",
                            f"【窃听】{self.p(a).label} 与 {self.p(b).label} "
                            f"{'属于同一阵营' if same else '不属于同一阵营'}。",
                            actor=salv.seat,
                            visible_to=[salv.seat],
                        )

        # 8. 结算
        self._resolve_night(acts)

    def _resolve_inspect(self, cmd: Player, target_seat: int) -> None:
        self.inspect_count += 1
        target = self.p(target_seat)
        truth = target.faction is Faction.ECHO

        result = truth
        note = ""
        # 镜像：主回响可让自己显示为人类
        if target.mirror_active:
            result = False
            target.mirror_active = False
            note = "mirror"
        # 每 3 次查验有 1 次被 ECHO 干扰反转
        elif self.inspect_count % 3 == 0:
            result = not truth
            note = "flip"

        self.log(
            "info",
            f"【神经扫描】{target.label} 的扫描结果："
            f"{'检测到回响体特征' if result else '未检测到异常，判定为人类'}。",
            actor=cmd.seat,
            visible_to=[cmd.seat],
            distorted=bool(note),
        )

    def _resolve_engineer(self, eng: Player, section: str, acts: NightActions) -> None:
        # 有夜间行动的人被随机分配到舱段（确定性地由种子驱动）
        actives: set[str] = set()
        movers = [
            acts.purge_target, acts.protect_target,
            acts.inspect_target, acts.silence_target,
        ]
        for seat in movers:
            if seat:
                actives.add(SECTIONS[(seat + self.round_no) % len(SECTIONS)])
        found = section in actives
        self.log(
            "info",
            f"【舱段检修】{section} 昨夜"
            f"{'检测到有人活动的痕迹' if found else '无任何活动记录'}。",
            actor=eng.seat,
            visible_to=[eng.seat],
        )

    def _resolve_night(self, acts: NightActions) -> None:
        if acts.silence_target:
            self.p(acts.silence_target).silenced_round = self.round_no

        self.phase = Phase.DAY_BRIEF
        if acts.purge_target and acts.purge_target != acts.protect_target:
            victim = self.p(acts.purge_target)
            victim.alive = False
            victim.death_round = self.round_no
            victim.death_cause = DeathCause.PURGED
            self.log(
                "death",
                f"清晨 06:00，{victim.label} 被发现在舱内失去生命体征。"
                f"神经接口已被完全格式化。",
                actor=victim.seat, cause="purged",
            )
        elif acts.purge_target:
            self.log(
                "system",
                "清晨 06:00，生命维持系统记录到一次紧急介入。全员生还——但有人差点没能醒来。",
            )
        else:
            self.log("system", "清晨 06:00，这是平静的一夜。")

        if acts.silence_target and self.p(acts.silence_target).alive:
            self.log(
                "system",
                f"{self.p(acts.silence_target).label} 的通讯模块出现故障，今日无法发言。",
            )

    # ---------- 白天 ----------

    def run_day(self) -> None:
        if self.winner:
            return
        self.phase = Phase.DAY_SPEECH
        self.log(
            "system",
            f"—— 第 {self.round_no} 日：全员在舰桥集合。存活 {len(self.alive)} 人 ——",
        )

        # 顺序发言
        for p in list(self.alive):
            if p.silenced_round == self.round_no:
                self.log("system", f"{p.label} 的通讯频道只有静电噪音。", actor=p.seat)
                continue
            self._speak(p, "speech")

        # 自由追加发言
        self.phase = Phase.DAY_FREE
        pool = [
            p for p in self.alive if p.silenced_round != self.round_no
        ]
        if len(pool) > 3:
            for p in self.rng.sample(pool, 3):
                self._speak(p, "free_speech")

        # 投票
        self._run_vote()

    def _speak(self, p: Player, kind: str) -> None:
        req = DecisionRequest(
            seat=p.seat,
            kind=kind,
            prompt="轮到你发言。分析局势，表达怀疑或为自己辩护。",
            options=self.alive_seats(),
        )
        text = self.ask(req)
        if text:
            self.log(kind, str(text), actor=p.seat)

    def _collect_votes(self, voters: list[Player], candidates: list[int]) -> dict[int, int]:
        votes: dict[int, int] = {}
        for p in voters:
            opts = [s for s in candidates if s != p.seat]
            if not opts:
                continue
            req = DecisionRequest(
                seat=p.seat,
                kind="vote",
                prompt="投票冻结一名你认为是回响体的船员。返回 0 表示弃权。",
                options=[0] + opts,
            )
            v = self._coerce_seat(self.ask(req), opts, allow_zero=True)
            votes[p.seat] = v or 0
            target = self.p(v).label if v else "弃权"
            self.log("vote", f"{p.label} → {target}", actor=p.seat, target=v)
        return votes

    def _run_vote(self) -> None:
        self.phase = Phase.DAY_VOTE
        self.log("system", "—— 冻结投票开始 ——")
        candidates = self.alive_seats()
        votes = self._collect_votes(self.alive, candidates)

        tally: dict[int, int] = {}
        for v in votes.values():
            if v:
                tally[v] = tally.get(v, 0) + 1
        self.vote_history.append({"round": self.round_no, "votes": votes, "tally": tally})

        if not tally:
            self.log("system", "全员弃权。没有人被冻结。")
            return

        top = max(tally.values())
        leaders = [s for s, c in tally.items() if c == top]

        if len(leaders) > 1:
            # 平票：辩护 + 重投
            self.phase = Phase.DAY_DEFENSE
            names = "、".join(self.p(s).label for s in leaders)
            self.log("system", f"平票：{names}。进入辩护环节。")
            for s in leaders:
                pl = self.p(s)
                req = DecisionRequest(
                    seat=s,
                    kind="defense",
                    prompt="你被平票锁定。用最多 30 字为自己辩护。",
                    options=self.alive_seats(),
                )
                text = self.ask(req)
                if text:
                    self.log("defense", str(text), actor=s)

            self.phase = Phase.DAY_VOTE
            self.log("system", "—— 重新投票 ——")
            revotes = self._collect_votes(self.alive, leaders)
            tally2: dict[int, int] = {}
            for v in revotes.values():
                if v:
                    tally2[v] = tally2.get(v, 0) + 1
            if not tally2:
                self.log("system", "重投全员弃权。无人被冻结。")
                return
            top2 = max(tally2.values())
            leaders2 = [s for s, c in tally2.items() if c == top2]
            if len(leaders2) > 1:
                self.log("system", "再次平票。舰桥陷入僵持，无人被冻结。")
                return
            leaders = leaders2

        out = self.p(leaders[0])
        out.alive = False
        out.death_round = self.round_no
        out.death_cause = DeathCause.FROZEN
        self.log(
            "death",
            f"{out.label} 被推入低温休眠舱。冻结前的最后一次身份读数："
            f"【{out.role_cn}】。",
            actor=out.seat, cause="frozen", role=out.role.value,
        )

    # ---------- 胜负 ----------

    def check_winner(self) -> Optional[Faction]:
        if self.winner:
            return self.winner
        alive = self.alive
        salv = self.by_role(Role.SALVAGER)

        # 拾荒者优先级最高
        if len(alive) <= 2 and salv:
            self._end(Faction.SALVAGER, f"{salv.label} 在最后的两人中存活，拾荒者达成目标。")
            return self.winner

        echo_n = len([p for p in alive if p.faction is Faction.ECHO])
        human_n = len([p for p in alive if p.faction is Faction.CREW])

        if echo_n == 0:
            self._end(Faction.CREW, "所有回响体已被清除。ECHO 的副本被彻底切断。")
        elif echo_n > human_n:
            self._end(Faction.ECHO, "回响体数量已超过人类。回响号的控制权易主。")
        return self.winner

    def _end(self, faction: Faction, reason: str) -> None:
        self.winner = faction
        self.win_reason = reason
        self.phase = Phase.GAME_OVER
        self.log("system", f"【对局结束】{reason}", winner=faction.value)
        roster = "、".join(f"{p.label}={p.role_cn}" for p in self.players)
        self.log("system", f"身份公开：{roster}")

    # ---------- 主循环 ----------

    def run(self, max_rounds: int = 12) -> Faction:
        self.log("system", "回响号 ECHO-7，深空科考站。船员 8 名。主控 AI 状态：未知。")
        while not self.winner and self.round_no < max_rounds:
            self.run_night()
            if self.check_winner():
                break
            self.run_day()
            if self.check_winner():
                break
        if not self.winner:
            self._end(Faction.ECHO, "补给耗尽，救援未至。无人生还。")
        return self.winner

    # ---------- 工具 ----------

    def _coerce_seat(
        self, val: Any, options: list[int], allow_zero: bool = False
    ) -> Optional[int]:
        """把 agent 的返回值强制转成合法座位号，非法则随机兜底。"""
        try:
            n = int(val)
        except (TypeError, ValueError):
            n = -1
        if allow_zero and n == 0:
            return None
        if n in options:
            return n
        return self.rng.choice(options) if options else None

    def snapshot(self) -> dict:
        return {
            "round": self.round_no,
            "phase": self.phase.value,
            "winner": self.winner.value if self.winner else None,
            "win_reason": self.win_reason,
            "players": [
                {
                    "seat": p.seat,
                    "name": p.name,
                    "alive": p.alive,
                    "role": p.role.value if (self.winner or not p.alive) else None,
                    "role_cn": p.role_cn if (self.winner or not p.alive) else "???",
                    "death_round": p.death_round,
                    "death_cause": p.death_cause.value if p.death_cause else None,
                }
                for p in self.players
            ],
        }
