"""Users JSON API (/api/v1).

Owner: agent A · Auth (docs/plan.md). Registered in app/main.py; stub until Wave 2.
"""

from fastapi import APIRouter

router = APIRouter(tags=["users"])
