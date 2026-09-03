"""假 OpenAI 兼容端点，用于在不花钱、不联网的情况下测真实 HTTP 路径。

    /v1        正常返回（故意混入 markdown 围栏和自然语言，模拟真实模型习惯）
    /garbage   永远返回垃圾（空串、代码块、超范围数字、乱码）
    /bad       永远 500

用法：python tests/mock_llm_server.py [port]
"""
from __future__ import annotations

import json
import random
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SECTIONS = ["舰桥", "医疗舱", "引擎室", "生活区"]

SPEECHES = [
    "昨晚我在引擎室待了一整夜，谁能证明自己不在场？先说清楚这个。",
    "我不急着投人。现在信息太少，投错一个人等于送回响体一轮。",
    "有人的发言太干净了，干净得像预先写好的。我盯着呢。",
]

ECHO_CHATS = [
    "今晚别动那个话多的，先处理有信息的那个。",
    "稳住，现在盘面对我们有利，别急着表态。",
]

GARBAGE = [
    "",
    "```json\n{\"seat\": \"不确定\"}\n```",
    "我觉得是3号但也可能是5号，你们自己判断吧",
    "999",
    "-42",
    "�����",
    "As an AI language model, I cannot participate in this game.",
]


def _seats(text: str) -> list[int]:
    m = re.search(r"可选座位号：([\d、,，\s]+)", text)
    if not m:
        return []
    return [int(x) for x in re.findall(r"\d+", m.group(1)) if int(x) != 0]


def _reply(system: str, user: str, mode: str) -> str:
    if mode == "garbage":
        return random.choice(GARBAGE)

    if "回响体密谈" in user:
        return random.choice(ECHO_CHATS)
    if "辩护" in user and "可选座位号" not in user:
        return "我是人类。你们投错了，明天就会知道。"
    if "可选舱段" in user:
        # 故意裹一层 markdown，测引擎的清洗
        return f"```\n{random.choice(SECTIONS)}\n```"
    if "只输出两个座位号" in user:
        pool = _seats(user) or [1, 2]
        picked = random.sample(pool, 2) if len(pool) >= 2 else [0]
        return ",".join(str(x) for x in picked)
    pool = _seats(user)
    if pool:
        pick = random.choice(pool)
        # 一半时候夹带自然语言，测 _first_int
        return str(pick) if random.random() < 0.5 else f"我选 {pick} 号。"
    return random.choice(SPEECHES)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 保持测试输出干净
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or "{}")

        if self.path.startswith("/bad"):
            return self._send(500, {"error": {"message": "boom"}})

        mode = "garbage" if self.path.startswith("/garbage") else "good"
        msgs = body.get("messages", [])
        system = msgs[0]["content"] if msgs else ""
        user = msgs[-1]["content"] if msgs else ""
        out = _reply(system, user, mode)
        self._send(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "mock"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": out},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _send(self, code: int, payload: dict):
        b = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8793
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
