import pandas as pd

df = pd.DataFrame({"a": [1, 2]})
for name, col in df.iteritems():  # BREAK
    pass
vals = df.lookup([0], ["a"])  # BREAK
for name, col in df.items():
    pass
