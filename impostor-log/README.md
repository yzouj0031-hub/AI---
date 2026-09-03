# 目击日记 · impostor-log

Among Us 式的太空内鬼游戏。你是内鬼，四个 AI 船员各自记录目击日记，
开会时拿日记和你的说辞逐条对账。

单文件，零依赖，零构建。

![](../docs/impostor-meeting.png)

## 玩

```bash
python -m http.server 8000
# 打开 http://127.0.0.1:8000/index.html
```

直接双击 `index.html` 也能玩（规则 AI 模式）。要用真模型则需要通过 http 服务打开，
否则浏览器会因为 `file://` 协议拦掉跨域请求。

**接模型**：点右上角「AI」按钮，填 OpenAI 兼容端点：

| 服务 | 地址 | 模型 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Ollama | `http://localhost:11434/v1` | `qwen2.5:14b` |

可选再填一组备用端点，主端点失败会自动切换。不填任何配置则使用内置规则 AI。

> ⚠️ **密钥安全**：key 存在浏览器 localStorage 里，只在你这台设备上。
> 但它会随请求出现在浏览器「网络」面板 —— 录屏或直播前点「清除密钥」。
> **不要把填好 key 的页面部署到公网。**

## 操作

| | |
|---|---|
| 移动 | 摇杆 / WASD / 方向键 |
| 刀 | 靠近船员，12 秒冷却 |
| 报告 | 站在尸体旁 |
| 钻管道 | 站在通风口上（被看见就完了） |
| 断电 | 全船视野砍半，任务全停 |
| 反应堆熔毁 | 30 秒内没有两人去修，你直接赢 |

## 核心机制：目击日记

每个 AI **每 0.5 秒**记一条：

```json
{ "t": 12.5, "room": "电力室", "saw": ["红","绿"], "doing_task": ["绿"] }
```

`saw` 只包含**同房间且距离小于视距**的人。断电时视距从 265 掉到 130，
日记自动变模糊 —— 信息噪声是从世界规则里长出来的，不是硬塞的。

开会时这份 JSON 原样喂给模型。会议结束后可以点「查看目击日记」展开看
喂进去的原始输入。

## 两个模型调用点

| 函数 | 时机 | 输出 |
|---|---|---|
| `readLLM()` | 会议开始，四人并行 | `{line, suspect}` 发言 + 怀疑度 |
| `defend()` | 你打字辩解之后 | `{verdict, line, delta}` 对账结果 |

`verdict` 三种：`contradict`（戳穿你，带具体时间和房间）、
`confirm`（替你作证）、`unknown`（说不清）。

**铁证不交给模型判断**：日记里有 `SAW_KILL` 或 `SAW_VENT` 时，
代码里硬加 120 分怀疑度。模型可能被你的话术说服，但"亲眼看见你捅刀"
不能取决于模型的心情。

## 降级路径

三层，游戏永远不会卡死在等待里：

1. 主端点失败 → 重试一次
2. 仍失败 → 切备用端点
3. 全挂 → 回落到规则 AI，并在会议里明说「⚠ 模型没能应答，本轮改用内置规则」

## 测试

```bash
pip install playwright && playwright install chromium
python tests/test_meeting.py
```

自动拉起假 LLM 端点，验证三条路径：正常调用、主端点 500 切备用、无配置走规则。

## 文件

```
index.html                      游戏本体（唯一需要的文件）
docs-original-rulesonly.html    接模型之前的纯规则版本，留作对照
tests/mock_llm_server.py        假 OpenAI 端点
tests/test_meeting.py           浏览器端到端测试
```
