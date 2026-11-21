import pandas as pd

df = pd.DataFrame({"a": [1, 2]})
out = pd.concat([df, df])
s = df["a"].sum()
