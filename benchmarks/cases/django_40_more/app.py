from django.utils.encoding import smart_text  # BREAK
from django.utils.http import urlunquote  # BREAK
from django.utils.translation import ungettext  # BREAK
from django.utils.translation import ngettext

msg = ngettext("a", "b", 2)
