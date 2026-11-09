import urllib.request

text = urllib.request.urlopen("http://campus-api:8080/metrics", timeout=5).read().decode()
for line in text.splitlines():
    if line.startswith("campus_dependency_deadline_total") or line.startswith("campus_db_retries_total") or line.startswith("campus_auth_denied_total") or line.startswith("campus_queue") or line.startswith("campus_info"):
        print(line)
