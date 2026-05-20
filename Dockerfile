# Stage 1: Build the React frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
# Build the frontend pointing to the same server (relative URL fallback)
ENV REACT_APP_API_URL=""
RUN npm run build

# Stage 2: Build the FastAPI backend and embed the database
FROM python:3.10-slim
WORKDIR /app

# Install system utilities needed for building packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend files
COPY backend/ ./backend/
# Copy built static frontend files so the backend can serve them
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# Set environment variable to make sure DB and text files are written in correct paths
WORKDIR /app/backend

# Pre-populate the SQLite database (argo_index.db) during the docker build phase
# This prevents slow downloads and CPU timeouts on boot when running on free tiers.
# Uvicorn port defaults to 8080 (Railway / Render standard)
ENV PORT=8080
RUN python -c "import asyncio; from main import init_db; asyncio.run(init_db())"

# Command to run the production server
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
