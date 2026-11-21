import urllib3
from urllib3.util.retry import Retry

http = urllib3.PoolManager()
retry = Retry(total=3, allowed_methods=["GET"])
