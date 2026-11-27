"""Business calendar. Document dates are Argentine dates, whatever the server timezone."""

import datetime as dt
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def today() -> dt.date:
    return dt.datetime.now(BUSINESS_TZ).date()
