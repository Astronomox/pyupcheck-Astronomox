import datetime

from django.utils import timezone
from django.utils.timezone import utc  # BREAK

now = timezone.now()
when = datetime.datetime(2024, 1, 1, tzinfo=timezone.utc)  # BREAK
fine = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
