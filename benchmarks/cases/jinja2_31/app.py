import jinja2
from jinja2 import Environment, FileSystemLoader
from jinja2 import Markup  # BREAK

env = Environment(loader=FileSystemLoader("templates"))
safe = jinja2.escape("<b>")  # BREAK


@jinja2.contextfunction  # BREAK
def helper(ctx):
    return ctx


tpl = env.from_string("{{ x }}")
out = tpl.render(x=Markup("<i>"))  # BREAK
