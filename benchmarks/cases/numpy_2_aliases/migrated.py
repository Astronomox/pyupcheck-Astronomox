import numpy as np

a = np.array([1.0, 2.0])
ok = np.all(a > 0)  # BREAK
some = np.any(a > 1)  # BREAK
cp = np.cumprod(a)  # BREAK
r = np.round(a, 1)  # BREAK
big = np.inf  # BREAK
fine = np.all(a > 0)
fine2 = np.cumprod(a)
fine3 = np.inf
