import matplotlib
from matplotlib import cm

cm.unregister_cmap("mine")  # BREAK
matplotlib.colormaps.unregister("mine")
