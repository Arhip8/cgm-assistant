FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app CGM_PUBLIC_DEMO=1 CGM_FRONTEND_DIST=/app/frontend/dist
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY external/kirill_db/db.py ./external/kirill_db/db.py
COPY external/hackathon_event_analyzer/event_analyzer.py ./external/hackathon_event_analyzer/event_analyzer.py
COPY simulator ./simulator
COPY demo/synthetic.csv ./demo/synthetic.csv
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN useradd --uid 10001 --create-home demo
USER demo
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=20s --start-period=60s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/health',timeout=5)"
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
