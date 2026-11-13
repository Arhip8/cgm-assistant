# CGM Assistant

Прототип модульного монолита для анализа исторических показателей глюкозы и
информационных уведомлений. API принимает измерения, вызывает демонстрационный
анализатор из `external/hackathon_event_analyzer/` и временно хранит события и
состояние анализа в оперативной памяти. React Dashboard показывает историю
измерений, события алгоритма и отдельные информационные уведомления.
Постоянная база данных и Web Push пока не реализованы.

## Структура

- `backend/app/main.py` — создание FastAPI-приложения и подключение маршрутов.
- `backend/app/api/routes/` — HTTP-маршруты `GET /health`,
  `/api/v1/readings` и `/api/v1/events`.
- `backend/app/api/schemas/` — Pydantic-схемы API.
- `backend/app/services/` — временное хранение измерений и событий, а также
  координация состояния анализатора и правил уведомлений.
- `backend/app/integrations/algorithm.py` — адаптер к демонстрационному
  алгоритму.
- `external/hackathon_event_analyzer/` — исходный модуль алгоритма и его тесты.
- `frontend/src/` — React Dashboard, API-клиент, типы, компоненты и стили.
- `frontend/vite.config.ts` — dev proxy `/api` на локальный FastAPI.
- `backend/app/db/` — место для будущей конфигурации БД; пока не используется.
- `frontend/` — React Dashboard на Vite и TypeScript.
- `simulator/` — модуль симулятора, разрабатываемый отдельно.
- `data/` — данные проекта.
- `tests/` — тесты проекта.

## Запуск backend

Требуется Python 3.10 или новее.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir backend --reload
```

Проверка состояния:

```bash
curl http://127.0.0.1:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

### Приём измерения

```bash
curl -X POST http://127.0.0.1:8000/api/v1/readings \
  -H 'Content-Type: application/json' \
  -d '{"run_id":"demo-001","source_index":17,"measured_at":"2026-11-12T12:05:00+03:00","glucose_mmol_l":8.6}'
```

История измерений, при необходимости отфильтрованная по запуску симулятора:

```bash
curl 'http://127.0.0.1:8000/api/v1/readings?run_id=demo-001'
```

События анализа:

```bash
curl 'http://127.0.0.1:8000/api/v1/events?run_id=demo-001'
```

Информационные уведомления:

```bash
curl 'http://127.0.0.1:8000/api/v1/notifications?run_id=demo-001'
```

Подтверждение ознакомления:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/notifications/NOTIFICATION_ID/acknowledge
```

Измерения, события, состояние анализатора и уведомления хранятся только в
оперативной памяти процесса и теряются при перезапуске. Контракты API и анализатора
описаны в [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) и
[`docs/ALGORITHM_CONTRACT.md`](docs/ALGORITHM_CONTRACT.md).

### Тесты

```bash
PYTHONPATH=backend python -m unittest discover -s tests -v
```

Оригинальные тесты внешнего алгоритма запускаются отдельно:

```bash
PYTHONPATH=external/hackathon_event_analyzer \
  python external/hackathon_event_analyzer/tests.py
```

## Запуск React Dashboard

В отдельном терминале из корня проекта:

```bash
cd frontend
npm install
npm run dev
```

Откройте адрес Vite, показанный в терминале (по умолчанию
`http://localhost:5173`). Для локальной разработки frontend проксирует запросы
`/api` на FastAPI по адресу `http://127.0.0.1:8000`; сначала запустите backend.

Сборка production-версии и проверка TypeScript:

```bash
cd frontend
npm run build
```

Панель обновляет историю показаний и событий каждые две секунды. Перезапуск
backend очищает его временные данные. Интерфейс и алгоритм — демонстрационный
прототип, не предназначенный для принятия медицинских решений.
