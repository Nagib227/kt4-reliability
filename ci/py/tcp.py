import os
import socket

host = os.environ["HOST"]
port = int(os.environ.get("PORT", "5432"))
started = socket.socket()
started.settimeout(3)
try:
    started.connect((host, port))
    print("connected")
except Exception as exc:
    print(type(exc).__name__)
