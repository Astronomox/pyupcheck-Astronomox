import pandas as pd

df = pd.DataFrame({"a": [1, 2]})
df = df.append({"a": 3}, ignore_index=True)  # BREAK
idx = pd.Int64Index([1, 2, 3])  # BREAK
merged = pd.concat([df, df])
total = df["a"].sum()
