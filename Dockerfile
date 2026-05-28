# Stage 1: Build React frontend.
FROM node:20-alpine AS frontend-build

WORKDIR /frontend

# Install dependencies first for layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Application.
# Build on top of the existing ECR image to inherit pre-compiled ARM64 dependencies.
FROM 513847850768.dkr.ecr.us-east-1.amazonaws.com/vega-platform:latest AS base

WORKDIR /app

# Production runtime is KB-only. Local synced manual files are for ingestion/debug
# workflows and are excluded from the Docker build context.
ENV VEGA_LOCAL_MANUAL_FALLBACK=0
ENV VEGA_RERANKING_ENABLED=0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY --from=frontend-build /frontend/dist /app/frontend/dist

EXPOSE 8080

# Target A: ECS / Fargate REST API layer.
FROM base AS api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# Target B: Native serverless AgentCore runtime listener.
FROM base AS runtime
CMD ["python", "entrypoints/agent_gateway.py"]
