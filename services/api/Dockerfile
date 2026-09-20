FROM python:3.12-slim
WORKDIR /app

COPY packages/shared packages/shared
COPY packages/domain packages/domain
COPY packages/integrations packages/integrations
COPY packages/agent packages/agent
COPY packages/app packages/app
COPY services/api services/api

RUN pip install --no-cache-dir \
    -e packages/shared -e packages/domain -e packages/integrations \
    -e packages/agent -e packages/app -e services/api

WORKDIR /app/services/api
ENV LOCAL_DATA_DIR=/app/data
EXPOSE 8000
CMD ["uvicorn", "promise_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
