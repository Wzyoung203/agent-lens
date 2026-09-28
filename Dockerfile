# 两段式构建：先用 Node 打前端，再把产物塞进 Python 运行时。
# 最终镜像里只有运行期依赖 + dist，没有 node_modules 与工具链。

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_LENS_CONFIG=/etc/agent-lens/config.toml \
    AGENT_LENS_DB=/data/agent-lens.db

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
# --frozen 保证用锁文件里确定的版本；--no-dev 不带 pytest / ruff / httpx。
RUN uv sync --frozen --no-dev

COPY --from=web /web/dist ./web/dist
COPY deploy/config.toml /etc/agent-lens/config.toml

ENV PATH="/app/.venv/bin:$PATH"

# /codex/sessions 是只读挂进来的事实源；/data 是持久化的库与配置。
VOLUME ["/data"]
EXPOSE 8000

CMD ["agent-lens", "collect"]
