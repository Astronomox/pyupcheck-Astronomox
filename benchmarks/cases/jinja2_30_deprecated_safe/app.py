import jinja2
from jinja2 import Environment, Markup

env = Environment()
m = Markup("<b>")
e = jinja2.escape("<i>")
