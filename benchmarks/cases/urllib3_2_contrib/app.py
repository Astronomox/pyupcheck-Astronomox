import urllib3
from urllib3.contrib import ntlmpool  # BREAK

pool = urllib3.PoolManager()
