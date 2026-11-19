from scipy import integrate
import numpy as np

y = np.array([1.0, 2.0, 3.0])
a = integrate.simps(y)  # BREAK
b = integrate.trapz(y)  # BREAK
c = integrate.cumtrapz(y)  # BREAK
d = integrate.simpson(y)
e = integrate.trapezoid(y)
