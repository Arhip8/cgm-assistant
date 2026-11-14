# CGM Assistant

Прототип модульного монолита для анализа исторических показателей глюкозы и
информационных уведомлений. API принимает измерения, вызывает демонстрационный
анализатор из `external/hackathon_event_analyzer/` и сохраняет измерения,
события, состояние анализа и уведомления в SQLite на основе моделей
`external/kirill_db/`. React Dashboard показывает историю измерений, события
алгоритма и отдельные информационные уведомления. Web Push не реализован.

## Структура

- `backend/app/main.py` — создание FastAPI-приложения и подключение маршрутов.
- `backend/app/api/routes/` — HTTP-маршруты `GET /health`,
  `/api/v1/readings`, `/api/v1/events` и `/api/v1/simulation`.
- `backend/app/api/schemas/` — Pydantic-схемы API.
- `backend/app/services/` — обработка измерений, событий, состояния анализатора
  и правил уведомлений.
- `backend/app/integrations/algorithm.py` — адаптер к демонстрационному
  алгоритму.
- `external/hackathon_event_analyzer/` — исходный модуль алгоритма и его тесты.
- `frontend/src/` — React Dashboard, API-клиент, типы, компоненты и стили.
- `frontend/vite.config.ts` — dev proxy `/api` на локальный FastAPI.
- `backend/app/db/database.py` — конфигурация SQLite и фабрика сессий.
- `frontend/` — React Dashboard на Vite и TypeScript.
- `simulator/` — CSV-симулятор и клиент POST `/api/v1/readings`.
- `data/` — данные проекта.
- `tests/` — тесты проекта.
- `data/kristina/` — локальные исходные материалы симулятора; каталог не
  добавляется в Git.

## Запуск backend

Требуется Python 3.10 или новее.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir backend --reload
```

По умолчанию рабочая БД находится в `data/cgm_assistant.sqlite3`. Можно задать
другой SQLAlchemy URL через `CGM_DATABASE_URL`. Историческая
`diabetes_helper.db` не используется. Для разработки и тестов установите
дополнительные зависимости командой `python -m pip install -r requirements-dev.txt`.

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

Измерения, события, состояние анализатора и уведомления сохраняются между
перезапусками. Контракты API и анализатора описаны в
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) и
[`docs/ALGORITHM_CONTRACT.md`](docs/ALGORITHM_CONTRACT.md).

### Запуск исторического симулятора

Укажите фактический часовой пояс исходных дат без offset. Для CSV Кристины
`EventDateTime` задан без часового пояса, поэтому параметр `--timezone`
обязателен. Симулятор не угадывает его. `Europe/Moscow` ниже приведён только
как пример: до реального воспроизведения подтвердите зону исходной выгрузки и
замените значение, если она отличается.

```bash
python -m simulator \
  --csv data/kristina/cleaned_glucose_data.csv \
  --timezone Europe/Moscow \
  --run-id kristina-demo \
  --speed-multiplier 60 \
  --max-records 100
```

`--speed-multiplier` меняет только задержки между запросами, временные метки
из CSV остаются исходными. При сетевой ошибке или конфликте симулятор выводит
ошибку и оставляет текущий `source_index` для повторной отправки. Для остановки
и продолжения в одном процессе используйте `pause()`, `resume()` и `stop()` на
экземпляре `GlucoseSimulator`; следующий вызов `run_simulation()` продолжит с
текущего индекса и сохранит тот же `run_id`. После перезапуска процесса укажите
тот же `--run-id` и позицию следующей CSV-строки из результата как
`--start-index`, например:

```bash
python -m simulator \
  --csv data/kristina/cleaned_glucose_data.csv \
  --timezone Europe/Moscow \
  --run-id kristina-demo \
  --start-index 100
```

Из CSV отправляются только `run_id`, `source_index`, `measured_at` и
`glucose_mmol_l`. Готовые `Anomaly_Type` и `Action_Recommended` не передаются:
события определяет серверный демонстрационный алгоритм.

### Серверное управление симуляцией

Сервер использует только `data/kristina/cleaned_glucose_data.csv` либо
операторский путь `CGM_SIMULATOR_CSV` внутри `data/kristina/`. Путь нельзя
передать через API. Для исходных дат без часового пояса обязательно задайте
`CGM_SIMULATOR_TIMEZONE`; если он не указан, запуск отклоняется. Например,
`UTC` можно использовать только как явно выбранное тестовое допущение, а не
как подтверждённый пояс источника.

```bash
export CGM_SIMULATOR_TIMEZONE=UTC
```

Для старта отправьте множитель скорости от `0` (не включая) до `3600`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/simulation/start \
  -H 'Content-Type: application/json' \
  -d '{"speed_multiplier":60}'
```

Управление и прогресс:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/simulation/pause
curl -X POST http://127.0.0.1:8000/api/v1/simulation/resume
curl -X POST http://127.0.0.1:8000/api/v1/simulation/stop
curl http://127.0.0.1:8000/api/v1/simulation/status
```

Симуляция выполняется в отдельном потоке и передаёт показания через тот же
`ReadingProcessor`, что и `POST /api/v1/readings`. `current_source_index` —
следующий индекс к отправке (он равен числу уже сохранённых измерений).
Скорость меняет только задержки; метки `measured_at` сохраняются из CSV.
Одновременно разрешена одна активная симуляция на экземпляр FastAPI. После
перезапуска автоматическое воспроизведение не запускается: незавершённый
запуск отображается как `stopped`, его можно продолжить через `resume` с
сохранённого `source_position`, если CSV и настройка часового пояса не
изменились. Контроллер сверяет их отпечаток и не продолжает запуск на других
данных.

### Тесты

```bash
CGM_TESTING=1 PYTHONPATH=backend python -m unittest discover -s tests -v
```

Тесты синтетического CSV-симулятора входят в этот набор.

Тесты исходной SQLAlchemy-схемы запускаются из её каталога:

```bash
cd external/kirill_db
CGM_TESTING=1 python -m pytest -v
```

Оригинальные тесты внешнего алгоритма запускаются отдельно:

```bash
CGM_TESTING=1 PYTHONPATH=external/hackathon_event_analyzer \
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
backend не удаляет данные из SQLite; он останавливает незавершённое
воспроизведение до явного продолжения. Интерфейс и алгоритм — демонстрационный
прототип, не предназначенный для принятия медицинских решений.
