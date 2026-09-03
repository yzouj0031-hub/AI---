"""LLM 客户端：OpenAI 兼容接口，可指向 OpenAI / DeepSeek / 智谱 / Ollama 等。

配置通过环境变量：
  LLM_BASE_URL   例如 https://api.deepseek.com/v1
  LLM_API_KEY
  LLM_MODEL      例如 deepseek-chat / gpt-4o-mini / qwen2.5:14b
  LLM_MOCK=1     使用本地假回复，不调用网络
  LLM_TIMEOUT    单次请求超时秒数，默认 30
  LLM_RETRIES    单次请求重试次数，默认 1
"""
from __future__ import annotations

import os
import random
import re
from typing import Optional


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        mock: Optional[bool] = None,
        temperature: float = 0.9,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.temperature = temperature
        # 不设超时的话，端点挂起会让整局永远卡住
        self.timeout = float(os.getenv("LLM_TIMEOUT", "30")) if timeout is None else timeout
        self.max_retries = int(os.getenv("LLM_RETRIES", "1")) if max_retries is None else max_retries
        if mock is None:
            mock = os.getenv("LLM_MOCK", "").strip() in ("1", "true", "yes") or not self.api_key
        self.mock = mock
        self._client = None
        if not self.mock:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )

    def chat(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 400,
        kind: str = "",
    ) -> str:
        if self.mock:
            return _mock_reply(kind, messages)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}] + messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # 网络/额度问题不应中断对局
            return f"[通讯中断：{type(e).__name__}]"


# ---------------- Mock ----------------

_MOCK_SPEECH = [
    "我昨晚一直在引擎室做例行检查，谁能证明自己不在场？先说清楚这个。",
    "我不急着投人。现在信息太少，投错一个人等于送回响体一轮。",
    "有人的发言太干净了，干净得像预先写好的。我盯着呢。",
    "扫描仪不是万能的。如果 ECHO 能改人的记忆，它当然也能改读数。",
    "上一轮谁在带节奏，我记得很清楚。今天他又是第一个开口的。",
    "我保留意见。但如果今晚再死一个人，我就不客气了。",
    "说实话我有点怕。不是怕死，是怕我自己已经不是我了。",
    "别把注意力都放在沉默的人身上，会说话的才危险。",
    "从投票走向看，有两个人在互相保。这个组合我不喜欢。",
    "我提议按舱段划分排查，谁不肯说自己昨晚在哪，谁就有问题。",
]

_MOCK_ECHO_CHAT = [
    "今晚别动那个话多的，先处理有信息的那个。",
    "我明天会把矛头指向 3 号，你顺着我说但别太用力。",
    "稳住，现在盘面对我们有利，别急着表态。",
]


def _mock_reply(kind: str, messages: list[dict]) -> str:
    """按决策类型分支，而不是靠关键词嗅探正文（正文里混有历史发言，会误判）。"""
    text = messages[-1]["content"] if messages else ""

    if kind == "echo_chat":
        return random.choice(_MOCK_ECHO_CHAT)
    if kind == "defense":
        return "我是人类。你们投错了，明天就会知道。"
    if kind in ("speech", "free_speech"):
        return random.choice(_MOCK_SPEECH)
    if kind == "night_engineer":
        return random.choice(["舰桥", "医疗舱", "引擎室", "生活区"])
    if kind == "night_wiretap":
        return "0"

    # 座位号类决策：从"可选座位号：..."那一行里取候选
    m = re.search(r"可选座位号：([\d、,，\s]+)", text)
    pool = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []
    pool = [x for x in pool if x != 0]
    return str(random.choice(pool)) if pool else "0"
