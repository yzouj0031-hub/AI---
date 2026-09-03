# 深空回响 · echo-station

多智能体太空狼人杀：8 个 LLM agent 各自扮演深空科考站的船员，独立推理、撒谎、结盟、投票。
你在浏览器里实时观战。

![](../docs/echo-station.png)

## 快速开始

```bash
pip install fastapi uvicorn openai websockets

# 1) 先用 mock 模式跑通（不消耗 API 额度，秒出结果）
LLM_MOCK=1 python run_cli.py

# 2) 接真模型
export LLM_BASE_URL=https://api.deepseek.com/v1   # 任何 OpenAI 兼容端点
export LLM_API_KEY=sk-xxx
export LLM_MODEL=deepseek-chat
python run_cli.py

# 3) 实时 Web 观战
uvicorn web.server:app --port 8100
# 打开 http://127.0.0.1:8100 ，点「开始对局」
```

### 支持的模型端点

任何 OpenAI 兼容接口都能直接用，只改环境变量：

| 服务 | LLM_BASE_URL | LLM_MODEL |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 月之暗面 | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:14b` |

其他环境变量：`GAME_SPEED`（Web 端每条事件的停顿秒数，默认 1.2）。

## 项目结构

```
engine/
  models.py       角色、阵营、玩家、事件的数据模型
  game.py         状态机：夜间结算、发言、投票、胜负判定
agents/
  llm.py          OpenAI 兼容客户端 + mock 回复
  player_agent.py 把游戏状态翻译成 prompt，把回复解析回决策
web/
  server.py       FastAPI + WebSocket，引擎跑在后台线程
  static/index.html  太空终端风格观战界面
run_cli.py        命令行跑一局
RULES.md          完整规则与世界观设定
```

## 设计要点

**信息隔离由引擎保证，不靠 prompt 自觉。**
每个 agent 只能拿到 `game.visible_events(seat)` —— 引擎按事件的 `visible_to`
字段做物理过滤。回响体的密谈、指挥官的查验结果、工程师的舱段报告，
在数据层就到不了其他 agent 的上下文里。已用 12 万次可见性检查验证零泄露。

**故意制造信息噪声。**
指挥官每 3 次查验有 1 次结果被反转，而且他自己不知道是哪次。
这让"查杀"不再是终局锤，AI 的推理链会出现真实的错误，白天必须真正辩论。
主回响的【镜像】技能可以额外伪造一次结果。

**三方阵营。**
拾荒者不属于任何一边，只想活到最后两人。它会在后期主动搅局，
防止人类或回响体任何一方形成稳定的信息垄断。

**引擎不信任 agent 的任何返回值。**
LLM 会返回空字符串、`"我觉得是3号但也可能是5号"`、markdown 代码块、
超出范围的数字。所有决策都经过 `_coerce_seat` 强制校验，非法值随机兜底。
已用 400 局畸形输入压测，零崩溃。

## 测试

```bash
python tests/test_invariants.py    # 约 1-2 分钟
```

四项不变量：胜负判定一致性、信息隔离（7.6 万次可见性检查）、
畸形输入健壮性（400 局垃圾输入）、平衡性对推理能力的敏感度。

## 平衡性数据

用不同"推理准确率"的模拟 agent 跑 400 局（准确率 = 人类把票投向真回响体的概率）：

| 推理准确率 | 人类胜 | 回响体胜 | 拾荒者胜 | 平均回合 |
|---|---|---|---|---|
| 0%（纯随机） | 10.0% | 84.5% | 5.5% | 3.1 |
| 20% | 27.8% | 62.0% | 10.2% | 3.0 |
| 35% | 48.2% | 43.0% | 8.8% | 2.8 |
| 50% | 64.8% | 25.5% | 9.8% | 2.6 |
| 65% | 83.5% | 11.2% | 5.2% | 2.4 |

胜率对推理能力高度敏感，50/50 平衡点落在 35% 准确率附近 —— 说明这个盘面
既不会让弱 agent 白捡胜利，也给强 agent 留了发挥空间。拾荒者稳定拿到 5-10%，
作为混沌因子存在感恰当。

> 关键平衡阀是胜利条件：回响体需要**严格多于**人类（而非"不少于"）。
> 早期版本用 `>=`，回响体胜率高达 85% 且拾荒者几乎无法获胜。

## 扩展方向

- **人类玩家接入**：`Game.agent_fn` 是单一入口，按 `req.seat` 分流到人类输入即可
- **换更强的模型对打**：不同 agent 用不同模型，看谁的社交推理更强
- **加角色**：在 `models.py` 加 `Role`，在 `game.py` 的夜间流程加结算分支
- **记忆压缩**：目前 agent 每轮拿到增量事件，长局可以加一层信念摘要
