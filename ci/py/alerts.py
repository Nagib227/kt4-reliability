import urllib.request

print(urllib.request.urlopen("http://campus-api:8080/internal/alerts", timeout=5).read().decode())
