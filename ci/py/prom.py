import os
import urllib.parse
import urllib.request

query = os.environ["QUERY"]
url = "http://prometheus:9090/api/v1/query?query=" + urllib.parse.quote(query)
print(urllib.request.urlopen(url, timeout=5).read().decode())
