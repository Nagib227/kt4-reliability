# Restore в postgres-dr

Исходный `postgres` не перезаписывается. Назначение — сервис `postgres-dr`.

1. Записать контрольную строку через `POST /posts`.
2. Запустить job `deploy/backup-job.yaml`. Он делает `pg_dump` с primary и `pg_restore` в DR после `DROP SCHEMA public CASCADE` только на DR.
3. После успешного dump записать ещё одну строку на primary. Её не должно быть в DR: это хвост за точкой восстановления.
4. Сверить `count(*)`, контрольную строку и `max(created_at)` на primary и DR. Пароль не печатать: внутри пода достаточно локального `psql`.
5. RPO — расстояние от последней восстановленной `created_at` до контрольной строки, записанной после dump. RTO — от старта job до успешной сверки.
6. DR остаётся с тем же Secret, тем же NetworkPolicy default deny и без отдельной admin identity.
