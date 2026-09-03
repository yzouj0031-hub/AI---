"""核心数据模型：角色、阵营、玩家、事件。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Faction(str, Enum):
    CREW = "crew"          # 人类船员
    ECHO = "echo"          # 回响体
    SALVAGER = "salvager"  # 拾荒者


class Role(str, Enum):
    COMMANDER = "commander"    # 指挥官：查验
    MEDIC = "medic"            # 医疗官：守护
    ENGINEER = "engineer"      # 工程师：舱段检修
    CREWMATE = "crewmate"      # 普通船员
    PRIME_ECHO = "prime_echo"  # 主回响
    SECOND_ECHO = "second_echo"  # 次生回响
    SALVAGER = "salvager"      # 拾荒者


ROLE_FACTION: dict[Role, Faction] = {
    Role.COMMANDER: Faction.CREW,
    Role.MEDIC: Faction.CREW,
    Role.ENGINEER: Faction.CREW,
    Role.CREWMATE: Faction.CREW,
    Role.PRIME_ECHO: Faction.ECHO,
    Role.SECOND_ECHO: Faction.ECHO,
    Role.SALVAGER: Faction.SALVAGER,
}

ROLE_CN: dict[Role, str] = {
    Role.COMMANDER: "指挥官",
    Role.MEDIC: "医疗官",
    Role.ENGINEER: "工程师",
    Role.CREWMATE: "船员",
    Role.PRIME_ECHO: "主回响",
    Role.SECOND_ECHO: "次生回响",
    Role.SALVAGER: "拾荒者",
}

FACTION_CN: dict[Faction, str] = {
    Faction.CREW: "人类船员",
    Faction.ECHO: "回响体",
    Faction.SALVAGER: "拾荒者",
}

# 标准 8 人局配置
DEFAULT_ROLES: list[Role] = [
    Role.COMMANDER,
    Role.MEDIC,
    Role.ENGINEER,
    Role.CREWMATE,
    Role.CREWMATE,
    Role.PRIME_ECHO,
    Role.SECOND_ECHO,
    Role.SALVAGER,
]

SECTIONS = ["舰桥", "医疗舱", "引擎室", "生活区"]


class Phase(str, Enum):
    SETUP = "setup"
    NIGHT = "night"
    DAY_BRIEF = "day_brief"      # 播报夜间结果
    DAY_SPEECH = "day_speech"    # 顺序发言
    DAY_FREE = "day_free"        # 自由追加发言
    DAY_VOTE = "day_vote"        # 投票
    DAY_DEFENSE = "day_defense"  # 平票辩护
    GAME_OVER = "game_over"


class DeathCause(str, Enum):
    PURGED = "purged"      # 夜间清除
    FROZEN = "frozen"      # 白天冻结（放逐）


@dataclass
class Player:
    seat: int
    name: str
    persona: str            # 人格描述，影响发言风格
    role: Role = Role.CREWMATE
    alive: bool = True
    death_round: Optional[int] = None
    death_cause: Optional[DeathCause] = None

    # 技能状态
    used_mirror: bool = False       # 主回响：镜像
    used_silence: bool = False      # 次生回响：静默
    used_wiretap: bool = False      # 拾荒者：窃听
    used_self_heal: bool = False    # 医疗官：自保
    mirror_active: bool = False     # 镜像生效中（下次查验显示为人类）
    silenced_round: int = -1        # 被静默的白天轮次
    last_protected: Optional[int] = None  # 医疗官上一晚保护的座位

    @property
    def faction(self) -> Faction:
        return ROLE_FACTION[self.role]

    @property
    def role_cn(self) -> str:
        return ROLE_CN[self.role]

    @property
    def label(self) -> str:
        return f"{self.seat}号·{self.name}"


@dataclass
class Event:
    """一条对局事件。visible_to=None 表示全体公开。"""
    round_no: int
    phase: Phase
    kind: str                 # speech / vote / death / info / system / thought
    actor: Optional[int]      # 座位号
    text: str
    visible_to: Optional[list[int]] = None   # 私有事件的可见座位
    meta: dict[str, Any] = field(default_factory=dict)

    def is_public(self) -> bool:
        return self.visible_to is None

    def to_dict(self) -> dict:
        return {
            "round": self.round_no,
            "phase": self.phase.value,
            "kind": self.kind,
            "actor": self.actor,
            "text": self.text,
            "public": self.is_public(),
            "visible_to": self.visible_to,
            "meta": self.meta,
        }


@dataclass
class NightActions:
    """一个夜晚收集到的所有行动。"""
    purge_target: Optional[int] = None
    protect_target: Optional[int] = None
    inspect_target: Optional[int] = None
    engineer_section: Optional[str] = None
    silence_target: Optional[int] = None
    wiretap_pair: Optional[tuple[int, int]] = None
    echo_chat: list[tuple[int, str]] = field(default_factory=list)
