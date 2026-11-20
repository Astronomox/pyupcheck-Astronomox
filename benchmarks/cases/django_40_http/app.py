from django.utils.http import urlquote  # BREAK
from django.utils.http import urlencode

q = urlquote("a b")  # BREAK
e = urlencode({"a": 1})
