from werkzeug.wrappers import BaseResponse  # BREAK
from werkzeug.wrappers import Response


def handler():
    return BaseResponse("x")  # BREAK


def ok_handler():
    return Response("x")
