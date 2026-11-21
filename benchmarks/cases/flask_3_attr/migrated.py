"""Uses flask.Markup / flask.escape through the module, not from-imports."""
import os

import flask
import markupsafe

app = flask.Flask(__name__)


@app.route("/")
def index():
    name = flask.request.args.get("name", "")
    safe = markupsafe.escape(name)  # BREAK
    return markupsafe.Markup("<b>{}</b>").format(safe)  # BREAK
