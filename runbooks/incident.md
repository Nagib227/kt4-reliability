# Инцидент latency и 5xx

До inject известны kill switch и условие остановки.

Kill switch: `kubectl --context kind-campus apply -f deploy/fault-off.yaml`.

Остановка немедленно, если ошибка вышла из namespace `campus-ready` или не гаснет через 3 минуты после kill switch.

Inject только ConfigMap `campus-fault`: latency и доля 5xx на `/boards` и `/posts`. Параллельно тестовая identity `intruder` делает запрещённый запрос. Чужие namespace не трогать.

Containment: выключить fault и не расширять права intruder. Evidence не переписывать.

Recovery: хороший трафик, alert `resolved`, `GET /boards` снова 200, запрет intruder на Secret сохраняется.
