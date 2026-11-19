import requests

s = requests.Session()
r = s.get("https://example.com", verify=False, timeout=3)
r.raise_for_status()
data = requests.post("https://example.com", json={"a": 1}).json()
