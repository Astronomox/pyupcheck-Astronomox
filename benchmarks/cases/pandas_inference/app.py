import pandas as pd


def load() -> pd.DataFrame:
    return pd.read_csv("data.csv")


def build():
    df = pd.DataFrame({"a": [1]})
    return df


frame = load()
frame = frame.append({"a": 2}, ignore_index=True)  # BREAK
built = build().append({"a": 3}, ignore_index=True)  # BREAK
chained = pd.DataFrame({"a": [1]}).append({"a": 4}, ignore_index=True)  # BREAK
csv = pd.read_csv("x.csv")
csv2 = csv.append({"a": 5}, ignore_index=True)  # BREAK
ok = pd.concat([frame, built])
total = frame["a"].sum()
