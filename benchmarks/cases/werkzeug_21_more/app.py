from werkzeug.wrappers import BaseRequest  # BREAK
from werkzeug.wrappers import Request
from werkzeug.wrappers.json import JSONMixin  # BREAK


class MyRequest(Request):
    pass
