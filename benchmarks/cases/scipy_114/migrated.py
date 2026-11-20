from scipy import integrate
import numpy as np

y = np.array([1.0, 2.0, 3.0])
a = integrate.simpson(y)  # BREAK
b = integrate.trapezoid(y)  # BREAK
c = integrate.cumulative_trapezoid(y)  # BREAK
d = integrate.simpson(y)
e = integrate.trapezoid(y)
