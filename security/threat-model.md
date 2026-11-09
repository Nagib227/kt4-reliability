# Угрозы и контроли

Границы доверия: клиент снаружи пода API; API и worker к PostgreSQL; API и worker к collector; collector к своему логу; оператор к API Kubernetes; namespace `campus-intruder` снаружи `campus-ready`.

| Угроза | Контроль | Что проверяется | Остаточный риск |
| --- | --- | --- | --- |
| Чужой namespace читает Secret БД | У API, worker, collector и backup нет права get secrets. Token SA не монтируется. incident-operator правит только ConfigMap | `kubectl auth can-i` даёт no | Админ kubeconfig этой машины может всё. Это доступ оператора стенда, не workload |
| Секрет в Git или логе | Пароль создаётся в Secret из `.local/`, файл в `.gitignore`. В команды evidence пароль не подставляется | Поиск по репозиторию и журналу | Дамп лежит в emptyDir job и удаляется вместе с подом. Отдельного KMS нет |
| Небезопасный Pod | `campus-guard` с `enforce=restricted`. Рабочие API/worker без root, без capabilities, read-only root | Примена unsafe Pod отклоняется | Postgres и backup стартуют не под restricted: entrypoint образа Postgres хочет писать data dir. Namespace стенда поэтому baseline |
| Лишний сетевой путь | Default deny и явные ingress/egress | TCP из `campus-intruder` к Postgres не устанавливается | Один узел kind. Policy не заменяет отдельную сеть облака |
| Подмена telemetry | Collector удаляет `user.email` до debug-экспорта. Приложение не пишет это поле в лог | Одинаковый trace_id в API, worker и collector; адреса почты в collector нет | OTLP внутри namespace не на TLS |

Identity разделены: `campus-api`, `campus-worker`, `otel-collector`, `backup-runner`, `incident-operator`. Одной admin identity на CI, runtime и разбор инцидента нет. CI этой контрольной — локальный `kubectl`, не GitLab Runner из КТ3.

Шифрование at rest — диск Docker, не KMS. Шифрование in transit между API и Postgres нет: учебный пароль по сети Pod. Владелец ключа в облаке не назначается, потому что облачного KMS на стенде нет.
