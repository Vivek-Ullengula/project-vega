# ── Stage 1: Build React Frontend ─────────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /frontend

# Install dependencies first (layer caching)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy source and build
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Application ─────────────────────────────────────────────
# Build on top of the existing ECR image to inherit all pre-compiled ARM64 dependencies
FROM 513847850768.dkr.ecr.us-east-1.amazonaws.com/vega-platform:latest AS base

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY . .

# Copy built frontend assets from Stage 1
COPY --from=frontend-build /frontend/dist /app/frontend/dist

EXPOSE 8080

# ── Target A: ECS / Fargate REST API Layer ───────────────────────────
FROM base AS api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# ── Target B: Native Serverless AgentCore Runtime Listener ───────────
FROM base AS runtime
CMD ["python", "entrypoints/agent_gateway.py"]
