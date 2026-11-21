from scipy import integrate, stats

y = [1.0, 2.0, 3.0]
a = integrate.simpson(y)
m = stats.describe(y)
