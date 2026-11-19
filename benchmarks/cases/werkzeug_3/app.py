from werkzeug.urls import url_quote  # BREAK
from werkzeug.urls import url_parse  # BREAK
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

quoted = url_quote("a b")  # BREAK
parts = url_parse("https://example.com")  # BREAK
name = secure_filename("../x.txt")
hashed = generate_password_hash("pw")
