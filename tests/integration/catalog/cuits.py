"""Valid CUITs for tests. Prefixes 23/27/33 never clash with tests/factories (20/30)."""

import itertools

from app.services.catalog import cuit_check_digit

_counter = itertools.count(10_000_000)


def valid_cuit(prefix: str = "27", *, dashed: bool = False) -> str:
    while True:
        body = f"{prefix}{next(_counter):08d}"
        digit = cuit_check_digit(body)
        if digit is not None:
            digits = f"{body}{digit}"
            return f"{digits[:2]}-{digits[2:10]}-{digits[10]}" if dashed else digits
