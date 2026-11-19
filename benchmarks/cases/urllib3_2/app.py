import urllib3
from urllib3.util.retry import Retry

retry = Retry(total=3, method_whitelist=["GET"])  # BREAK
ok_retry = Retry(total=3, allowed_methods=["GET"])
http = urllib3.PoolManager(retries=retry)
resp = http.request("GET", "https://example.com")
