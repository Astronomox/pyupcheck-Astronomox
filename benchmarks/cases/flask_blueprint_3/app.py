from flask import Blueprint, Flask, jsonify

app = Flask(__name__)
bp = Blueprint("api", __name__, template_folder="templates")


@bp.route("/items", methods=("GET",))
def items():
    return jsonify([])


@bp.route("/items", methods=("POST",))
def create():
    return jsonify({}), 201


@app.before_first_request  # BREAK
def warm_up():
    pass


folder = bp.template_folder
root = bp.root_path
app.register_blueprint(bp)
