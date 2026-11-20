from django.utils.encoding import force_str
from django.utils.http import urlencode
from django.urls import path

x = force_str(b"x")
y = urlencode({"a": 1})
