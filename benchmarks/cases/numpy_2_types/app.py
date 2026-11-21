import numpy as np

s = np.unicode_("x")  # BREAK
b = np.string_(b"x")  # BREAK
c = np.cfloat(1)  # BREAK
low = np.NINF  # BREAK
m = np.mat([[1, 2]])  # BREAK
ok_s = np.str_("x")
ok_b = np.bytes_(b"x")
ok_c = np.complex128(1)
ok_m = np.asmatrix([[1, 2]])
