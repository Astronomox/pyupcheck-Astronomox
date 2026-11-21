from werkzeug import urls
from werkzeug.urls import url_encode  # BREAK

q = url_encode({"a": 1})  # BREAK
u = urls.url_unquote("a%20b")  # BREAK
iri = urls.iri_to_uri("http://example.com")
