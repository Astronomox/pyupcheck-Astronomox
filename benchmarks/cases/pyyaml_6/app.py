import yaml

with open("c.yml") as f:
    a = yaml.load(f)  # BREAK
    b = yaml.load(f, Loader=yaml.SafeLoader)
    c = yaml.safe_load(f)
    d = yaml.dump({"x": 1})
