import jinja2


@jinja2.contextfilter  # BREAK
def f1(ctx, v):
    return v


@jinja2.environmentfilter  # BREAK
def f2(env, v):
    return v


@jinja2.evalcontextfilter  # BREAK
def f3(ectx, v):
    return v


env = jinja2.Environment()
