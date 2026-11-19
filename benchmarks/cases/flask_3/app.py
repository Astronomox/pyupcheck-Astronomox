import flask
from flask import Flask, jsonify, request
from flask import Markup  # BREAK
from flask import escape  # BREAK

app = Flask(__name__)
encoder = flask.json.JSONEncoder  # BREAK


@app.route("/")
def index():
    name = request.args.get("name", "")
    return jsonify(greeting=str(escape(name)))  # BREAK
