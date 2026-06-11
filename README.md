# AI Радар


## Запуск

```bash
docker compose up -d --build
```

После запуска:

- приложение: http://localhost:8000
- админка: http://localhost:8000/admin
- Grafana: http://localhost:3000

## Grafana

Логин по умолчанию задается в `.env`:

- `GRAFANA_ADMIN_USER=admin`
- `GRAFANA_ADMIN_PASSWORD=admin`

Dashboard `AI Radar Overview` создается автоматически через provisioning и использует Postgres datasource `AI Radar Postgres`.

В dashboard доступны:

- период: неделя, месяц, все время;
- фильтр по источнику;
- AI-фильтр как заглушка;
- график сбора по дням;
- количество данных по источникам;
- таблица спарсенных `raw_items`.


# Перестроить FAISS индекс (после наполнения БД):
curl -X POST http://localhost:8000/api/v1/vector/rebuild