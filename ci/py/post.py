import json
import os
import urllib.error
import urllib.request

payload = {"body": os.environ["BODY"]}
if os.environ.get("EMAIL"):
    payload["email"] = os.environ["EMAIL"]
data = json.dumps(payload).encode()
headers = {"Content-Type": "application/json"}
if os.environ.get("KEY"):
    headers["Idempotency-Key"] = os.environ["KEY"]
req = urllib.request.Request("http://campus-api:8080/posts", data=data, headers=headers, method="POST")
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as exc:
    print(exc.code)
    print(exc.read().decode())
