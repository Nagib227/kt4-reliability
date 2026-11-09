import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import telemetry

PORT = int(os.environ.get("PORT", "8080"))
FAULT_PATH = os.environ.get("FAULT_PATH", "/etc/campus/fault.json")
READY = {"ok": False}
CACHE = {"boards": None}


def load_fault():
    fault = {"mode": "off", "error_ratio": 0, "latency_ms": 0, "deadline_ms": 400}
    try:
        with open(FAULT_PATH, encoding="utf-8") as handle:
            fault.update(json.load(handle))
    except Exception:
        telemetry.log("WARN", "fault_file_unreadable")
    fault["error_ratio"] = float(fault.get("error_ratio") or 0)
    fault["latency_ms"] = int(fault.get("latency_ms") or 0)
    fault["deadline_ms"] = int(fault.get("deadline_ms") or 400)
    return fault


def route_of(path):
    path = path.split("?", 1)[0]
    if path in ("/boards", "/posts", "/ready", "/live", "/metrics", "/internal/alerts", "/admin/fault"):
        return path
    return "other"


def status_class(code):
    return f"{code // 100}xx"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = route_of(self.path)
        if route == "/live":
            self._send(200, {"status": "live"})
            return
        if route == "/metrics":
            body = telemetry.render_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/ready":
            code = 200 if READY["ok"] else 503
            self._send(code, {"status": "ready" if READY["ok"] else "not-ready"})
            return
        if route == "/admin/fault":
            self._send(200, load_fault())
            return
        if route == "/internal/alerts":
            rows, _ = db.run_query(
                lambda conn: conn.execute(
                    "SELECT alertname, severity, status, received_at::text FROM alert_events ORDER BY id DESC LIMIT 20"
                ).fetchall(),
                2000,
                retries=1,
            )
            self._send(200, {"alerts": [{"alertname": r[0], "severity": r[1], "status": r[2], "received_at": r[3]} for r in rows]})
            return
        if route == "/boards":
            self._journey("GET", "/boards", self._boards)
            return
        self._send(404, {"error": "not-found"})

    def do_POST(self):
        route = route_of(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad-json"})
            return
        if route == "/internal/alerts":
            names = []

            def save(conn):
                for item in payload.get("alerts", []):
                    alertname = item.get("labels", {}).get("alertname", "")
                    severity = item.get("labels", {}).get("severity", "")
                    status = item.get("status", "")
                    names.append(alertname)
                    conn.execute(
                        "INSERT INTO alert_events (alertname, severity, status) VALUES (%s, %s, %s)",
                        (alertname, severity, status),
                    )
                    telemetry.log(
                        "INFO",
                        "alert_received",
                        alertname=alertname,
                        alert_severity=severity,
                        alert_status=status,
                    )

            try:
                db.run_query(save, 2000, retries=1)
                self._send(202, {"accepted": names})
            except Exception:
                telemetry.log("ERROR", "alert_store_failed")
                self._send(500, {"error": "alert-store"})
            return
        if route == "/admin/fault":
            actor = self.headers.get("X-Actor", "")
            if actor != "operator":
                telemetry.inc("campus_auth_denied_total", {"route": "/admin/fault"})
                telemetry.log("WARN", "actor_denied", actor=actor or "missing")
                self._send(403, {"error": "denied"})
                return
            self._send(202, {"kill_switch": "kubectl apply -f deploy/fault-off.yaml"})
            return
        if route == "/posts":
            self._journey("POST", "/posts", lambda trace, fault: self._create_post(trace, fault, payload))
            return
        self._send(404, {"error": "not-found"})

    def _boards(self, trace_id, fault):
        def query(conn):
            row = conn.execute("SELECT count(*) FROM posts").fetchone()
            return int(row[0])

        count, attempts = db.run_query(query, fault["deadline_ms"], retries=2)
        if attempts:
            telemetry.inc("campus_db_retries_total", {"route": "/boards"}, attempts)
        body = {"boards": [{"id": 1, "name": "general", "posts": count}], "degraded": False}
        CACHE["boards"] = body
        return 200, body

    def _create_post(self, trace_id, fault, payload):
        text = str(payload.get("body", "")).strip()
        if not text or len(text) > 500:
            return 400, {"error": "bad-body"}
        key = self.headers.get("Idempotency-Key") or None
        email = payload.get("email")
        span_email = {"user.email": email} if email else {}

        def query(conn):
            row = conn.execute(
                """
                INSERT INTO posts (board_id, body, idempotency_key, trace_id)
                VALUES (1, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (text, key, trace_id),
            ).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    "SELECT id, trace_id FROM posts WHERE idempotency_key = %s",
                    (key,),
                ).fetchone()
            post_id = int(row[0])
            if created:
                span = telemetry.new_span_id()
                conn.execute(
                    """
                    INSERT INTO jobs (post_id, trace_id, parent_span_id, status)
                    VALUES (%s, %s, %s, 'queued')
                    """,
                    (post_id, trace_id, span),
                )
            return {"id": post_id, "created": created}

        result, attempts = db.run_query(query, fault["deadline_ms"], retries=2)
        if attempts:
            telemetry.inc("campus_db_retries_total", {"route": "/posts"}, attempts)
        result["span_email"] = span_email
        return (201 if result["created"] else 200), result

    def _journey(self, method, route, action):
        started = time.perf_counter()
        start_ns = time.time_ns()
        trace_id = telemetry.new_trace_id()
        span_id = telemetry.new_span_id()
        fault = load_fault()
        code = 500
        body = {"error": "unknown"}
        extra_attrs = {}
        try:
            if fault["mode"] in ("latency", "incident", "dbslow") and fault["latency_ms"] > 0:
                time.sleep(fault["latency_ms"] / 1000)
            if fault["mode"] in ("errors", "incident"):
                slot = (time.time_ns() % 1000) / 1000
                if slot < fault["error_ratio"]:
                    telemetry.inc("campus_synthetic_errors_total", {"route": route})
                    code, body = 500, {"error": "synthetic"}
                    raise StopIteration()
            if fault["mode"] == "dbslow" and (time.perf_counter() - started) * 1000 >= fault["deadline_ms"]:
                telemetry.inc("campus_dependency_deadline_total", {"route": route})
                telemetry.log(
                    "WARN",
                    "dependency_deadline",
                    trace_id=trace_id,
                    route=route,
                    retries=0,
                    deadline_ms=fault["deadline_ms"],
                )
                if route == "/boards" and CACHE["boards"]:
                    body = dict(CACHE["boards"])
                    body["degraded"] = True
                    code = 200
                else:
                    code, body = 503, {"error": "degraded", "degraded": True}
                raise StopIteration()
            code, body = action(trace_id, fault)
            extra_attrs = body.pop("span_email", {}) if isinstance(body, dict) else {}
        except StopIteration:
            pass
        except db.Deadline:
            telemetry.inc("campus_dependency_deadline_total", {"route": route})
            telemetry.log("WARN", "dependency_deadline", trace_id=trace_id, route=route, retries=0)
            code, body = 503, {"error": "deadline", "degraded": True}
        except Exception:
            telemetry.log("ERROR", "request_failed", trace_id=trace_id, route=route, error="db")
            code, body = 503, {"error": "unavailable"}
        elapsed = time.perf_counter() - started
        labels = {"service": "campus-api", "method": method, "route": route, "status": status_class(code)}
        telemetry.inc("campus_http_requests_total", labels)
        telemetry.observe("campus_http_request_seconds", {"service": "campus-api", "route": route}, elapsed)
        if elapsed > 0.4:
            telemetry.inc("campus_http_slow_total", {"route": route})
        telemetry.log(
            "INFO" if code < 500 else "ERROR",
            "request",
            trace_id=trace_id,
            route=route,
            method=method,
            status=code,
            elapsed_ms=round(elapsed * 1000, 1),
        )
        telemetry.send_span(
            trace_id,
            span_id,
            f"{method} {route}",
            start_ns,
            time.time_ns(),
            attributes={
                "http.route": route,
                "http.status_code": str(code),
                "app.version": telemetry.VERSION,
                **extra_attrs,
            },
        )
        body["trace_id"] = trace_id
        self._send(code, body)

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if isinstance(body, dict) and body.get("trace_id"):
            self.send_header("X-Trace-Id", body["trace_id"])
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return


def boot():
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            db.init_schema()
            READY["ok"] = True
            telemetry.log("INFO", "schema_ready")
            return
        except Exception as exc:
            telemetry.log("WARN", "schema_wait", error=type(exc).__name__)
            time.sleep(2)
    telemetry.log("ERROR", "schema_failed")


if __name__ == "__main__":
    threading_boot = __import__("threading").Thread(target=boot, daemon=True)
    threading_boot.start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
