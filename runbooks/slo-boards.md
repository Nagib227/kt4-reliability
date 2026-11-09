# GET /boards

Симптом: alert `CampusBoardFastBurn` (page) или `CampusBoardSlowTicket` (ticket).

1. Открыть `GET /admin/fault`. Если `mode` не `off`, это учебный inject, а не неизвестный дефект.
2. Kill switch: `kubectl --context kind-campus apply -f deploy/fault-off.yaml`. Ждать, пока `/admin/fault` не покажет `mode=off`.
3. Дать хороший трафик на `GET /boards` и дождаться `resolved` в `GET /internal/alerts`.
4. Если mode уже `off`, смотреть trace в логе collector по `trace_id` из ответа и лог API с тем же `trace_id`.
5. Остановка эксперимента раньше плана: тот же kill switch, если доля 5xx не падает или страдают namespace `gitlab`, `campus-dev`, `campus-stage`.
