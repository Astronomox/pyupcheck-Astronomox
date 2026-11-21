import flask
from flask import json

dec = flask.json.JSONDecoder  # BREAK
out = json.dumps({"a": 1})
