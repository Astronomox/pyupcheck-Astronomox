import matplotlib
from matplotlib import cm

cm.register_cmap(name="mine", cmap=None)  # BREAK
matplotlib.colormaps.register(cmap=None, name="mine2")
