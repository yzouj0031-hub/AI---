# 给接手这个仓库的 AI 的说明

这份文件是给 AI 助手看的（Claude Code / Cursor / Copilot 等都会自动读取）。
人类请看 [README.md](README.md)。

## 一句话

两个太空主题的社交推理游戏，共同点是：**AI 玩家的推理必须建立在受限信息之上**。
这是整个仓库的核心约束，所有设计决定都从它推出。

## 仓库结构

```
echo-station/     Python，8 个 LLM agent 全自动对局，人类观战
impostor-log/     单文件 HTML，Among Us 式地图 + 移动，人类当内鬼，AI 当船员
docs/             截图
```

两个项目**互相独立**，没有共享代码。放在一起是因为它们是同一个想法的两种实现：
一个把"目击"当作引擎发的事件，一个把"目击"从坐标和视距里算出来。

## 最重要的一条：不要破坏信息隔离

这是两个项目共同的、也是唯一不可协商的不变量。

**echo-station**：agent 只能通过 `game.visible_events(seat)` 拿信息。
事件的 `visible_to` 字段决定谁能看见。回响体的密谈、指挥官的查验结果，
在数据层就到不了别人的上下文里。

```python
# engine/models.py
@dataclass
class Event:
    visible_to: Optional[list[int]] = None   # None = 全体公开
```

**impostor-log**：每个 AI 每 0.5 秒记一条 `{t, room, saw, tk, dark}`。
`saw` 只包含同房间且距离小于视距的人。断电时视距从 265 掉到 130。

```js
// sample() —— 目击日记的唯一来源
if(zoneOf(q.x,q.y)===z && Math.hypot(q.x-p.x,q.y-p.y)<vision()) saw.push(q.id);
```

**行为层同样受这条约束**。船员的怀疑度只从 `a.log`（它自己的日记）里算，
盯梢的"最后目击点"只在 `canSee(a,tgt)` 为真时才刷新：

```js
// chaseStep() —— 看不见还去追新位置，等于读了它不该有的坐标
if(!canSee(a,tgt))staleWrite++;   // 恒为 0；改坏了 test_meeting.py 会抓到
a.chase=tgt.id; a.cx=tgt.x; a.cy=tgt.y;
```

想让 AI 更聪明的时候，最省事的写法永远是直接读 `P[i].x/y`。别这么干。
追到最后目击点然后扑空，是**对的**行为；实时跟踪一个看不见的人不是。

**如果你要加功能**：任何时候都不要图省事，直接把全局状态塞进 prompt。
一旦 AI 能看到它不该看到的东西，整个游戏的推理就变成了表演。
`tests/test_invariants.py` 里的 `test_isolation` 会抓这个，改动后务必跑。

## 第二条：引擎不信任模型的任何输出

真实 LLM 会返回空字符串、markdown 代码块、`"我觉得是3号但也可能是5号"`、
超出范围的数字、以及纯粹的乱码。

- echo-station：所有座位号决策过 `Game._coerce_seat()`，非法值随机兜底
- impostor-log：`grabJSON()` 剥 markdown 围栏、修尾逗号，失败返回 null

新增任何模型调用点，都要走这两个函数，不要直接 `int(resp)` 或 `JSON.parse(resp)`。
`test_malformed` 用 400 局垃圾输入压测这一点。

**但兜底必须留下痕迹**。模型抽风时对局不会报错，只会静默退化成随机，
从输出上看不出来。所以 echo-station 里每次兜底都要经过 `Game._note_fallback()`：

```python
self._note_fallback(self._cur_kind or "seat")   # _coerce_seat 里
```

新增兜底分支时记得也调它，否则遥测会漏报。`test_agents.py` 会检查
垃圾端点下兜底率显著偏高、正常 agent 下恒为 0（不误报）。

## 第三条：模型挂了游戏也要能玩

两个项目都保留了完整的规则版 AI 作为兜底：

- echo-station：`LLMClient(mock=True)`，或不配置 API key 时自动启用
- impostor-log：`read()` 和 `defendRule()` 是 `readLLM()` / `defend()` 的降级路径

改动会议/推理逻辑时，**两条路径都要能跑通**。测试里有专门的降级用例。

---

## echo-station 速览

```bash
cd echo-station
LLM_MOCK=1 python run_cli.py          # 不花钱跑一局
python tests/test_invariants.py       # 引擎不变量（约 1-2 分钟）
python tests/test_agents.py           # agent 层 + 真实 HTTP 路径
python tests/test_web.py              # 观战服务
uvicorn web.server:app --port 8100    # Web 观战
```

三个测试文件对应三层，改哪层跑哪个，**改 agents/ 一定要跑 test_agents.py**：
它是唯一会检查"送进模型的 prompt 里有没有越权信息"的地方——
`test_invariants.py` 验的是 `visible_events()`，prompt 拼装那层手滑它抓不到。

**架构**：引擎是同步阻塞状态机，通过 `agent_fn(DecisionRequest) -> Any` 单一入口
向外索取决策。它不知道对面是 LLM、规则、还是人类。

```
Game.run()
  └─ run_night()  回响体密谈 → 清除 → 守护 → 查验 → 检修 → 窃听 → 结算
  └─ run_day()    顺序发言 → 自由发言 → 投票（平票则辩护重投）
  └─ check_winner()
```

**要加人类玩家**：`agent_fn` 是唯一入口，按 `req.seat` 分流即可，引擎不用改。

**观战服务是一对多的**。`Session` 给每个 WebSocket 连接发一个独立队列，
`publish()` 扇出给所有订阅者。早期版本所有连接共用一个 `queue.Queue`，
两个观众会把事件瓜分掉，谁都看不到完整对局。
加新的推送时用 `session.publish()`，不要直接往某个队列里塞。
`test_web.py` 会用两个真 WebSocket 检查事件流逐条一致。

**要加角色**：在 `models.py` 加 `Role` 和 `ROLE_FACTION` 映射，
在 `game.py` 的 `run_night()` 里加结算分支，在 `player_agent.py` 的 `ROLE_BRIEF` 里写角色 prompt。

**平衡性是调过的，别随手改胜利条件**。回响体需要**严格多于**人类
（`echo_n > human_n`）。早期版本用 `>=`，回响体胜率 85%、拾荒者永远赢不了。
`test_balance` 会检查胜率对推理能力单调敏感，改了会失败。

## impostor-log 速览

```bash
cd impostor-log
python -m http.server 8000      # 打开 index.html 即可玩
python tests/test_meeting.py    # 会议流程端到端测试（需要 playwright）
python tests/test_mobile.py     # 手机 / 平板适配（5 种视口 + 触摸 + 键盘 + PWA）
```

**静态地图是烘焙的。** `bakeMap()` 把地板、墙、房名、通风口、暗角画进离屏
canvas，`draw()` 每帧只 `drawImage` 再叠动态层。往地图上加静态装饰要加进
`bakeMap()`，加进 `draw()` 会白白付 60 次/秒的代价；反过来，任何会变的东西
（任务点归属、角色、尸体、光照）必须留在 `draw()` 里，放进 `bakeMap()` 就永远不更新了。

**摇杆的几何不要写死。** 早期版本把中心 52、半径 42、旋钮偏移 31 直接写在
`mv()` 里——那三个数只对 104px 的摇杆成立，横屏想把摇杆缩小就会失灵。
现在全部由 `stickGeo()` 从实际尺寸算出来，CSS 里随便改大小都不用动 JS。
`test_mobile.py` 会在 5 种尺寸下真的拖一次摇杆，确认角色位移一致。

单文件，无构建、无依赖。右上角「AI」按钮填 OpenAI 兼容端点。

**两个模型调用点**：

| 函数 | 时机 | 输入 | 输出 |
|---|---|---|---|
| `readLLM()` | 会议开始 | 该船员的目击日记 JSON | `{line, suspect}` |
| `defend()` | 玩家辩解后 | 日记 + 玩家那句话 | `{verdict, line, delta}` |

四个船员**并行**调用（`Promise.all`），否则开一次会要等四轮往返。

**铁证不交给模型判断**：日记里有 `SAW_KILL` / `SAW_VENT` 时，代码里硬加 120 分
怀疑度。模型可能被玩家的话术说服，但"亲眼看见你捅刀"不能取决于模型的心情。
这是有意的越权，别把它优化掉。

**`docs-original-rulesonly.html`** 是接模型之前的纯规则版本，留作对照。
不要在它上面改功能。

---

## 常见任务的入手点

| 想做什么 | 改哪里 |
|---|---|
| 换模型 / 换服务商 | echo-station：环境变量；impostor-log：页面里的 AI 按钮 |
| 调 AI 的说话风格 | `agents/player_agent.py` 的 `ROLE_BRIEF`；`index.html` 的 `SYS_CREW` |
| 加新角色/技能 | `engine/models.py` + `engine/game.py` 的夜间流程 |
| 让 AI 当内鬼 | impostor-log 目前只有人类内鬼，需要新增一个内鬼 prompt 和决策循环 |
| 加地图/移动到 echo-station | 参考 impostor-log 的 `sample()`，把目击从坐标算出来 |

## 已知的取舍

- **echo-station 没有空间概念**。玩家只有 `seat`，没有坐标。"太空站"只是 prompt 里的
  世界观文字。要加空间就得重做目击生成。
- **impostor-log 的内鬼只能是人类玩家**。四个船员是 AI，内鬼是你。
- **impostor-log 把 API key 存在 localStorage**。单机自己玩没问题，
  但 key 会出现在浏览器网络面板里，**不要把填好 key 的页面部署到公网**。
- **echo-station 的 agent 每轮只拿增量事件**，长对局没有信念摘要，
  上下文会线性增长。8 人局跑不满 12 轮，暂时不是问题。
