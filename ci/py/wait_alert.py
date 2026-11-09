import json
import os
import time
import urllib.request

name = os.environ["ALERT"]
status = os.environ.get("STATUS", "firing")
after = os.environ.get("AFTER", "")
need = 1 if after else int(os.environ.get("BEFORE", "0")) + 1
deadline = time.time() + int(os.environ.get("WAIT", "150"))
last = ""
while time.time() < deadline:
    for _ in range(8):
        try:
            urllib.request.urlopen("http://campus-api:8080/boards", timeout=5).read()
        except Exception:
            pass
    try:
        last = urllib.request.urlopen("http://campus-api:8080/internal/alerts", timeout=5).read().decode()
        hits = [
            item
            for item in json.loads(last).get("alerts", [])
            if item.get("alertname") == name
            and item.get("status") == status
            and (not after or item.get("received_at", "") >= after)
        ]
    except Exception as exc:
        last = type(exc).__name__
        hits = []
    if len(hits) >= need:
        print(last)
        raise SystemExit(0)
    time.sleep(3)
print(last)
raise SystemExit(1)
