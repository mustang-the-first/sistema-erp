"""Accounting HTML views (Jinja2 + HTMX): chart of accounts, journal and ledger.

Owner: agent C · Accounting (docs/plan.md). Roles: accountant (admin always passes).

HTMX writes (``hx-post`` / ``hx-patch``) answer with the re-rendered account tree. A domain or
validation error answers 200 with ``HX-Retarget: #flash`` so HTMX swaps the message into the
flash area (HTMX does not swap 4xx responses by default) and the tree stays untouched.
"""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.errors import BusinessRuleViolation, DomainError
from app.core.templating import templates
from app.models.enums import AccountType, JournalSourceType, UserRole
from app.schemas.accounting import AccountCreate, AccountUpdate
from app.services import accounting as service

router = APIRouter(
    prefix="/accounting",
    tags=["accounting"],
    dependencies=[Depends(require_roles(UserRole.ACCOUNTANT))],
)

JOURNAL_PAGE_SIZE = 50
TYPE_LABELS = {
    AccountType.ASSET: "Activo",
    AccountType.LIABILITY: "Pasivo",
    AccountType.EQUITY: "Patrimonio neto",
    AccountType.INCOME: "Ingresos",
    AccountType.EXPENSE: "Egresos",
}
SOURCE_LABELS = {
    JournalSourceType.INVOICE: "Factura",
    JournalSourceType.DELIVERY_NOTE: "Remito",
    JournalSourceType.RECEIPT: "Cobranza",
    JournalSourceType.STOCK_ADJUSTMENT: "Ajuste de stock",
}


def _parse_date(value: str | None, label: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BusinessRuleViolation(f"Invalid date for '{label}': {value}") from exc


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/error.html",
        {"detail": message},
        headers={"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"},
    )


def _tree_context(db: DbSession) -> dict[str, Any]:
    accounts = service.list_accounts(db)
    return {
        "tree": service.build_account_tree(accounts),
        "parents": [a for a in accounts if a.is_active and not a.is_postable],
        "type_labels": TYPE_LABELS,
    }


def _tree(request: Request, db: DbSession, message: str) -> HTMLResponse:
    context = _tree_context(db) | {"message": message, "level": "success", "oob_flash": True}
    return templates.TemplateResponse(request, "accounting/_tree.html", context)


# Chart of accounts ------------------------------------------------------------------------


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(request, "accounting/accounts.html", _tree_context(db))


@router.post("/accounts", response_class=HTMLResponse)
def create_account(
    request: Request,
    db: DbSession,
    code: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
    is_postable: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        parent = service.get_account(db, int(parent_id)) if parent_id.isdigit() else None
        if parent is None:
            raise BusinessRuleViolation("Elegí el rubro padre")
        body = AccountCreate(
            code=code, name=name, type=parent.type, parent_id=parent.id, is_postable=is_postable
        )
        account = service.create_account(db, **body.model_dump())
    except ValidationError as exc:
        return _error(request, _validation_message(exc))
    except DomainError as exc:
        return _error(request, exc.message)
    return _tree(request, db, f"Cuenta {account.code} creada")


@router.patch("/accounts/{account_id}", response_class=HTMLResponse)
def update_account(
    request: Request,
    account_id: int,
    db: DbSession,
    name: Annotated[str, Form()] = "",
    is_active: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        body = AccountUpdate(name=name, is_active=is_active)
        account = service.update_account(db, account_id, **body.model_dump(exclude_unset=True))
    except ValidationError as exc:
        return _error(request, _validation_message(exc))
    except DomainError as exc:
        return _error(request, exc.message)
    return _tree(request, db, f"Cuenta {account.code} actualizada")


# Journal ----------------------------------------------------------------------------------


@router.get("/journal", response_class=HTMLResponse)
def journal_page(
    request: Request,
    db: DbSession,
    source_type: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    page = max(page, 1)
    context: dict[str, Any] = {
        "date_from": raw_from or "",
        "date_to": raw_to or "",
        "source_type": source_type or "",
        "source_labels": SOURCE_LABELS,
        "entries": [],
        "total": 0,
        "page": page,
        "pages": 1,
    }
    try:
        kind = JournalSourceType(source_type) if source_type else None
        result = service.list_journal(
            db,
            date_from=_parse_date(raw_from, "desde"),
            date_to=_parse_date(raw_to, "hasta"),
            source_type=kind,
            limit=JOURNAL_PAGE_SIZE,
            offset=(page - 1) * JOURNAL_PAGE_SIZE,
        )
    except ValueError:
        context["error"] = "Tipo de origen inválido"
    except DomainError as exc:
        context["error"] = exc.message
    else:
        context |= {
            "entries": [(e, *service.entry_totals(e)) for e in result.items],
            "total": result.total,
            "pages": max(1, -(-result.total // JOURNAL_PAGE_SIZE)),
        }
    return templates.TemplateResponse(request, "accounting/journal.html", context)


# Ledger -----------------------------------------------------------------------------------


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request, db: DbSession, account_id: str | None = None) -> HTMLResponse:
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    context: dict[str, Any] = {
        "accounts": service.list_accounts(db),
        "account_id": int(account_id) if account_id and account_id.isdigit() else None,
        "date_from": raw_from or "",
        "date_to": raw_to or "",
        "ledger": None,
    }
    if context["account_id"] is not None:
        try:
            context["ledger"] = service.get_ledger(
                db,
                context["account_id"],
                date_from=_parse_date(raw_from, "desde"),
                date_to=_parse_date(raw_to, "hasta"),
            )
        except DomainError as exc:
            context["error"] = exc.message
    return templates.TemplateResponse(request, "accounting/ledger.html", context)
