import urllib.request

print(urllib.request.urlopen("http://campus-api:8080/admin/fault", timeout=3).read().decode())
