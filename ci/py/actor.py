import os
import urllib.error
import urllib.request

req = urllib.request.Request(
    "http://campus-api:8080/admin/fault",
    data=b"{}",
    headers={"Content-Type": "application/json", "X-Actor": os.environ.get("ACTOR", "")},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as exc:
    print(exc.code)
    print(exc.read().decode())
