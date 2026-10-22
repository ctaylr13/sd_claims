FROM node:24-bookworm-slim AS frontend-build
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive
COPY index.html vite.config.ts tsconfig*.json ./
COPY src ./src
COPY public ./public
RUN yarn build

FROM nginx:stable-alpine AS frontend
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80

FROM python:3.13-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements-backend.txt ./
RUN pip install --no-cache-dir -r requirements-backend.txt
RUN useradd --create-home --uid 10001 claims && mkdir -p /app/cache && chown claims:claims /app/cache
COPY backend ./backend
COPY examples ./examples
COPY drive-sources.json ./
USER claims
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
