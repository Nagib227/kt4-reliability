import json
import os
import urllib.error
import urllib.request

count = int(os.environ.get("COUNT", "20"))
ok = err = 0
last = ""
for _ in range(count):
    try:
        with urllib.request.urlopen("http://campus-api:8080/boards", timeout=5) as resp:
            last = resp.read().decode()
            ok += 1
    except urllib.error.HTTPError as exc:
        last = exc.read().decode()
        err += 1
    except Exception as exc:
        last = type(exc).__name__
        err += 1
print(json.dumps({"ok": ok, "err": err, "last": last}, ensure_ascii=False))
