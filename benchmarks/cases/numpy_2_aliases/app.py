import numpy as np

a = np.array([1.0, 2.0])
ok = np.alltrue(a > 0)  # BREAK
some = np.sometrue(a > 1)  # BREAK
cp = np.cumproduct(a)  # BREAK
r = np.round_(a, 1)  # BREAK
big = np.Inf  # BREAK
fine = np.all(a > 0)
fine2 = np.cumprod(a)
fine3 = np.inf
