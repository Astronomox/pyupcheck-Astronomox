import networkx as nx

g = nx.Graph()
g.add_edge(1, 2)
m = nx.to_numpy_matrix(g)  # BREAK
h = nx.from_numpy_matrix(m)  # BREAK
arr = nx.to_numpy_array(g)
deg = dict(g.degree())
