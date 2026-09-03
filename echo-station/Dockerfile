FROM python:3.11-slim

WORKDIR /app

# 先装依赖，改代码时这层还能命中缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 默认跑 mock：不花钱、不需要 API key，部署完就能看一局完整对局。
# 要接真模型，在托管平台的环境变量里设 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，
# 并把 LLM_MOCK 删掉或设为 0。
ENV LLM_MOCK=1 \
    GAME_SPEED=1.2 \
    PORT=8000

EXPOSE 8000

# 多数平台（Render / Railway / Fly）会注入自己的 $PORT
CMD ["sh", "-c", "uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
