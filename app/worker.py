import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import telemetry

PORT = int(os.environ.get("PORT", "8081"))
telemetry.SERVICE = os.environ.get("SERVICE_NAME", "campus-worker")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = telemetry.render_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"live"}')

    def log_message(self, fmt, *args):
        return


def claim():
    with db.connect() as conn:
        conn.execute("SET statement_timeout = 1000")
        row = conn.execute(
            """
            WITH picked AS (
              SELECT id FROM jobs
              WHERE status = 'queued'
              ORDER BY id
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE jobs AS j
            SET status = 'running', attempts = j.attempts + 1
            FROM picked
            WHERE j.id = picked.id
            RETURNING j.id, j.post_id, j.trace_id, j.parent_span_id, j.created_at
            """
        ).fetchone()
        conn.commit()
        return row


def finish(job_id, post_id):
    with db.connect() as conn:
        conn.execute("UPDATE posts SET processed_at = now() WHERE id = %s AND processed_at IS NULL", (post_id,))
        conn.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (job_id,))
        conn.commit()


def age():
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(created_at))), 0)
            FROM jobs
            WHERE status = 'queued'
            """
        ).fetchone()
        return float(row[0])


def loop():
    while True:
        try:
            db.init_schema()
            telemetry.set_gauge("campus_queue_oldest_seconds", {"queue": "posts"}, age())
            job = claim()
            if not job:
                time.sleep(0.4)
                continue
            job_id, post_id, trace_id, parent_span_id, created_at = job
            start_ns = time.time_ns()
            finish(job_id, post_id)
            telemetry.log("INFO", "job_done", trace_id=trace_id, post_id=int(post_id), job_id=int(job_id))
            telemetry.send_span(
                trace_id,
                telemetry.new_span_id(),
                "process post",
                start_ns,
                time.time_ns(),
                parent_id=parent_span_id,
                attributes={"queue": "posts", "app.version": telemetry.VERSION},
            )
            telemetry.inc("campus_jobs_done_total", {"queue": "posts"})
        except Exception as exc:
            telemetry.log("ERROR", "worker_loop_failed", error=type(exc).__name__)
            time.sleep(1)


if __name__ == "__main__":
    __import__("threading").Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
