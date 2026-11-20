from django.utils.encoding import force_str as force_text  # BREAK
from django.utils.encoding import force_str
from django.utils.translation import gettext as ugettext  # BREAK
from django.utils.translation import gettext
from django.urls import re_path as url  # BREAK
from django.urls import path

label = force_text(b"x")  # BREAK
title = ugettext("hello")  # BREAK
ok_label = force_str(b"x")
ok_title = gettext("hello")
