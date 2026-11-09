# Надёжность, наблюдаемость и безопасность

Локальный контур CampusBoard в том же kind-кластере `campus`, контекст `kind-campus`. Облачный поставщик не использовался: на машине нет отдельного managed Kubernetes, и рядом уже работают GitLab Omnibus и стенд КТ2. Namespace `gitlab`, `campus-dev` и `campus-stage` этот прогон не меняет.

Пользовательский путь здесь — открыть доску и создать запись. API на Python, очередь в PostgreSQL, worker забирает запись. Желаемое состояние fault лежит в ConfigMap `campus-fault`. Kill switch — `deploy/fault-off.yaml`. Он применяется до эксперимента, а не во время поиска причины.

## Что стоит в namespace campus-ready

| Часть | Роль |
| --- | --- |
| `campus-api`, 2 реплики | `GET /boards`, `POST /posts`, метрики, приём alert webhook |
| `campus-worker` | очередь `jobs`, тот же `trace_id` |
| `postgres` | основная БД |
| `postgres-dr` | отдельное назначение restore, исходную БД не перезаписывает |
| `otel` | OTLP, удаляет `user.email` до экспорта |
| `prometheus` и `alertmanager` | scrape, page/ticket, webhook обратно в API |

Образ: `localhost:5001/campus-ready:kt4`. Digest последнего прогона — в `evidence/00-architecture/image.txt`.

Readiness смотрит на БД, liveness — только на живой процесс. Падение БД не крутит restart loop. PDB держит минимум один API. Topology spread задан, но узел один: оба пода остаются в одном failure domain. Это принятый остаточный риск, не защита от отказа ноды.

## SLO

Два journey и формулы бюджета — в `slo/profile.md`. Короткое окно считает Prometheus. Окно 30 дней на стенде не наблюдается, поэтому page-порог адаптирован: доля 5xx на `GET /boards` выше 10% в течение 30 секунд. Это учебная гипотеза для синтетического отказа.

Owner — оператор стенда. Runbook доски: `runbooks/slo-boards.md`. Runbook записи: `runbooks/slo-posts.md`. Инцидент: `runbooks/incident.md`. Restore: `runbooks/restore.md`.

## Повторить прогон

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ci\run-lab.ps1
```

Скрипт собирает образ, поднимает namespace и проходит T01–T12. Пароль БД создаётся в `.local/` и в Secret, в журнал не пишется. Повторный запуск не меняет уже созданный Secret, чтобы не разойтись с данными на диске.

Журнал лежит в `evidence/`. Оглавление — `evidence/evidence-index.csv`.

Убрать только этот стенд:

```powershell
kubectl --context kind-campus delete ns campus-ready campus-intruder campus-guard
```

`kind delete cluster --name campus` удалит и GitLab, и КТ2.

## Что показал прогон 2026-11-09

Время ниже — UTC+3. Журнал — в `evidence/`.

| Проверка | Факт |
| --- | --- |
| T01 | 30 из 30 `GET /boards` успешны. Формула бюджета в `slo/profile.md`. Пятиминутный SLI в Prometheus в тот момент был 0,805: в окно ещё попали ошибки предыдущего inject |
| T02 | Синтетические 5xx открыли page `CampusBoardFastBurn`, первый webhook 13:34:26 |
| T03 | Удалён один Pod API. Во время удаления 20 из 20 `GET /boards` успешны |
| T04 | `mode=dbslow` отдал `degraded=true` и не пошёл в БД. Счётчик deadline стал 3 |
| T05 | Restore в `postgres-dr` за 6 с. Строка, записанная после dump, есть только на primary. RPO около 5 с, RTO 6 с |
| T06 | DR на том же образе и с тем же Secret, чужой namespace к нему не подключён |
| T07 | `trace_id=332ff4a8d7fc462abc1b8c835114c4a7` есть в API, worker и collector |
| T08 | `student@example.test` нет ни в логе API, ни в логе collector |
| T09 | Page и ticket различаются severity. Page закрылся webhook-ом `resolved` в 13:49:27. Ticket по latency держался дольше минуты |
| T10 | `incident-operator` может менять ConfigMap и не может читать Secret. `operator` получает 202, `intruder` — 403 |
| T11 | Privileged Pod отклонён Pod Security. TCP к Postgres из другого namespace — `TimeoutError` |
| T12 | Inject 13:43:35, ticket 13:44:42, kill switch 13:47:26, page `resolved` 13:49:27, затем 10 из 10 успешных чтений. Разбор — `evidence/06-incident/postmortem.md` |

После прогона найден дефект приёмника: лог webhook падал, потому что поле `severity` совпадало с аргументом `telemetry.log`. Alertmanager видел обрыв и повторял доставку. Исправление выкачено тегом `kt4b`, контрольный POST вернул 202.
