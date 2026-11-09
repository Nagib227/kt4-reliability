import json
import os
import threading
import time
import urllib.request
import uuid

SERVICE = os.environ.get("SERVICE_NAME", "campus-api")
VERSION = os.environ.get("APP_VERSION", "local")
DIGEST = os.environ.get("IMAGE_DIGEST", "unknown")
OTEL = os.environ.get("OTEL_ENDPOINT", "").rstrip("/")
BUCKETS = (0.1, 0.4, 1.0, 5.0)

_lock = threading.Lock()
_counters = {}
_gauges = {}
_hist = {}


def new_trace_id():
    return uuid.uuid4().hex


def new_span_id():
    return uuid.uuid4().hex[:16]


def log(severity, msg, trace_id="", **fields):
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "severity": severity,
        "service": SERVICE,
        "version": VERSION,
        "digest": DIGEST,
        "trace_id": trace_id,
        "msg": msg,
    }
    for key, value in fields.items():
        if key in ("user.email", "email", "password", "authorization"):
            continue
        rec[key] = value
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def _label_key(labels):
    return tuple(sorted(labels.items()))


def inc(name, labels, value=1):
    key = (name, _label_key(labels))
    with _lock:
        _counters[key] = _counters.get(key, 0) + value


def set_gauge(name, labels, value):
    key = (name, _label_key(labels))
    with _lock:
        _gauges[key] = value


def observe(name, labels, value):
    key = (name, _label_key(labels))
    with _lock:
        bucket, total, count = _hist.get(key, ({}, 0.0, 0))
        total += value
        count += 1
        for bound in list(BUCKETS) + ["+Inf"]:
            limit = float("inf") if bound == "+Inf" else bound
            if value <= limit:
                bucket[bound] = bucket.get(bound, 0) + 1
        _hist[key] = (bucket, total, count)


def _fmt_labels(labels):
    if not labels:
        return ""
    body = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + body + "}"


def render_metrics():
    lines = [
        "# HELP campus_info Release metadata for this process.",
        "# TYPE campus_info gauge",
        f'campus_info{{service="{SERVICE}",version="{VERSION}",digest="{DIGEST}"}} 1',
    ]
    with _lock:
        counters = list(_counters.items())
        gauges = list(_gauges.items())
        hist = list(_hist.items())
    seen = set()
    for (name, labels), value in counters:
        if name not in seen:
            lines.append(f"# TYPE {name} counter")
            seen.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {value}")
    seen = set()
    for (name, labels), value in gauges:
        if name not in seen:
            lines.append(f"# TYPE {name} gauge")
            seen.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {value}")
    seen = set()
    for (name, labels), (bucket, total, count) in hist:
        if name not in seen:
            lines.append(f"# TYPE {name} histogram")
            seen.add(name)
        for bound in list(BUCKETS) + ["+Inf"]:
            le = "+Inf" if bound == "+Inf" else str(bound)
            labels_le = list(labels) + [("le", le)]
            lines.append(f"{name}_bucket{_fmt_labels(labels_le)} {bucket.get(bound, 0)}")
        lines.append(f"{name}_sum{_fmt_labels(labels)} {total}")
        lines.append(f"{name}_count{_fmt_labels(labels)} {count}")
    return "\n".join(lines) + "\n"


def send_span(trace_id, span_id, name, start_ns, end_ns, parent_id="", attributes=None):
    marked = False
    attrs = []
    for key, value in (attributes or {}).items():
        if key == "user.email":
            marked = True
        attrs.append({"key": key, "value": {"stringValue": str(value)}})
    if marked:
        inc("campus_attributes_marked_total", {"key": "user.email"})
    if not OTEL:
        return
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": SERVICE}},
                        {"key": "service.version", "value": {"stringValue": VERSION}},
                        {"key": "service.instance.digest", "value": {"stringValue": DIGEST}},
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "parentSpanId": parent_id,
                                "name": name,
                                "kind": 2,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "attributes": attrs,
                            }
                        ]
                    }
                ],
            }
        ]
    }

    def _post():
        try:
            req = urllib.request.Request(
                OTEL + "/v1/traces",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                resp.read()
        except Exception:
            inc("campus_telemetry_export_failed_total", {"signal": "traces"})
            log("WARN", "telemetry_export_failed", trace_id=trace_id, signal="traces")

    threading.Thread(target=_post, daemon=True).start()
