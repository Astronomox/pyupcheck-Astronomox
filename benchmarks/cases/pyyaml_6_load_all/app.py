import yaml

with open("c.yml") as f:
    docs = list(yaml.load_all(f))  # BREAK
    ok = list(yaml.load_all(f, Loader=yaml.SafeLoader))
    ok2 = list(yaml.safe_load_all(f))
