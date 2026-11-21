"""Uses flask.Markup / flask.escape through the module, not from-imports."""
import os

import flask

app = flask.Flask(__name__)


@app.route("/")
def index():
    name = flask.request.args.get("name", "")
    safe = flask.escape(name)  # BREAK
    return flask.Markup("<b>{}</b>").format(safe)  # BREAK
