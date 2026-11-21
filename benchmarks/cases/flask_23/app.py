from flask import Flask

app = Flask(__name__)


@app.before_first_request  # BREAK
def setup():
    pass


@app.before_request
def each():
    pass
