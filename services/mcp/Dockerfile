FROM node:22-slim AS ui-build
WORKDIR /ui
COPY services/mcp/ui/commitment-card .
RUN npm install && npm run build

FROM python:3.12-slim
WORKDIR /app

COPY packages/shared packages/shared
COPY packages/domain packages/domain
COPY packages/integrations packages/integrations
COPY packages/agent packages/agent
COPY packages/app packages/app
COPY services/mcp services/mcp
COPY --from=ui-build /ui/dist services/mcp/ui/commitment-card/dist

RUN pip install --no-cache-dir \
    -e packages/shared -e packages/domain -e packages/integrations \
    -e packages/agent -e packages/app -e services/mcp

WORKDIR /app/services/mcp
ENV LOCAL_DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "-m", "promise_mcp.server"]
