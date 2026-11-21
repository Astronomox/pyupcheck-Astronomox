import pandas as pd

arr = pd.np.array([1, 2])  # BREAK
s = pd.Series([1, 2])
for k, v in s.iteritems():  # BREAK
    pass
for k, v in s.items():
    pass
