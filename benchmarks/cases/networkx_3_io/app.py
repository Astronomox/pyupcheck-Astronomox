import networkx as nx

g = nx.Graph()
nx.write_gpickle(g, "g.pkl")  # BREAK
h = nx.read_gpickle("g.pkl")  # BREAK
summary = nx.info(g)  # BREAK
n = g.number_of_nodes()
