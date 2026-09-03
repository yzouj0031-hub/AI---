# 部署

两个项目的部署难度差很多，分开说。

| | impostor-log · 目击日记 | echo-station · 深空回响 |
|---|---|---|
| 形态 | 静态 HTML | Python 常驻服务 |
| 能不能放 GitHub Pages | **能** | 不能（Pages 只托管静态文件） |
| 费用 | 免费 | 免费额度够用，看平台 |
| 现状 | 已配好，推 main 自动发布 | 提供 Dockerfile，需自己开一个托管 |

---

## 一、目击日记 → GitHub Pages（已配好）

`.github/workflows/pages.yml` 已经在仓库里了。**你只需要点一次开关：**

1. 打开 <https://github.com/yzouj0031-hub/AI---/settings/pages>
2. **Build and deployment → Source** 选 **GitHub Actions**（不是 "Deploy from a branch"）
3. 回到 Actions 页面，跑一次 `Deploy site to GitHub Pages`
   （改动 `site/`、`impostor-log/index.html`、`docs/` 后会自动触发，也可以手动 Run workflow）

发布后的地址：

```
落地页    https://yzouj0031-hub.github.io/AI---/
直接开玩  https://yzouj0031-hub.github.io/AI---/impostor-log/
```

站点内容由 workflow 组装，不需要你手动维护 `_site`：

```
_site/
  index.html              ← site/index.html（落地页）
  impostor-log/index.html ← 游戏本体
  docs/*.png              ← 截图
```

### 关于 API Key（重要）

游戏**不填 key 也能完整游玩**，走内置规则 AI。填了 key 的话：

- key 只存在访客自己浏览器的 `localStorage`，请求从浏览器直接发往访客填的端点，
  **不经过 GitHub Pages**——这个站没有后端
- 所以**千万不要**把你自己的 key 写死进 `index.html`。那是公开静态文件，
  等于把 key 贴在网上

两个已知限制，都是浏览器直连 API 的通病：

- **必须用 https 端点。** Pages 是 https 站点，浏览器会拦掉 http 请求（混合内容）
- **端点必须允许跨域（CORS）。** 不是所有厂商的接口都对浏览器开放；
  被拦时右上角「AI」按钮会变红，游戏自动退回规则 AI，不会卡死

---

## 二、深空回响 → 需要一个能跑 Python 的托管

它是 FastAPI + WebSocket 的常驻服务，Pages 放不了。仓库里已经准备好
`echo-station/Dockerfile` 和 `render.yaml`。

### Render（免费档，最省事）

1. <https://render.com> 注册，**New → Blueprint**
2. 选这个仓库，它会自动读根目录的 `render.yaml`
3. 直接 Create——默认 `LLM_MOCK=1`，**不花钱、不用填 key**，部署完就能看完整对局

要接真模型：在 Render 后台把 `LLM_MOCK` 改成 `0`，再加三个环境变量
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。

> 免费实例闲置约 15 分钟会休眠，下次访问要等几十秒冷启动。

### 其它平台

`Dockerfile` 是通用的，Railway / Fly.io / Koyeb 都能直接用。
容器会读 `$PORT`，这几家都会自动注入。也可以本地跑：

```bash
docker build -t echo-station ./echo-station
docker run -p 8100:8000 echo-station
# 打开 http://localhost:8100
```

### 公开部署前要知道的两件事

1. **一次只有一局，所有访客看的是同一局。** `Session` 是全局单例，
   任何人都能调 `POST /api/start`。作为演示没问题，当产品用需要改成每人一局
2. **接了真模型就是你在付钱。** key 在服务端，访客点一次「开始」就烧一局的
   token。公开挂出去建议保持 `LLM_MOCK=1`，或者自己加个访问口令

---

## 三、本地预览落地页

```bash
mkdir -p _site/impostor-log _site/docs
cp site/index.html _site/
cp impostor-log/index.html _site/impostor-log/
cp docs/*.png _site/docs/
cd _site && python -m http.server 8080
# 打开 http://localhost:8080
```
