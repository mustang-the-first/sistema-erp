"""Shared Jinja2 environment for the web routers."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.money import format_ars

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["ars"] = format_ars
