"""Accounting JSON API (/api/v1): chart of accounts, journal and ledger.

Owner: agent C · Accounting (docs/plan.md). Roles: accountant (admin always passes).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.enums import JournalSourceType, UserRole
from app.schemas.accounting import (
    AccountCreate,
    AccountFlatList,
    AccountRead,
    AccountTree,
    AccountTreeNode,
    AccountUpdate,
    JournalEntryList,
    JournalEntryRead,
    LedgerRead,
)
from app.services import accounting as service

router = APIRouter(tags=["accounting"], dependencies=[Depends(require_roles(UserRole.ACCOUNTANT))])

DateFrom = Annotated[dt.date | None, Query(alias="from")]
DateTo = Annotated[dt.date | None, Query(alias="to")]


@router.get(
    "/accounts",
    response_model=None,  # tree or flat: each branch returns its own validated model
    responses={200: {"model": AccountTree, "description": "Tree; flat list with ?flat=true"}},
)
def list_accounts(db: DbSession, flat: bool = False) -> AccountTree | AccountFlatList:
    accounts = service.list_accounts(db)
    if flat:
        return AccountFlatList(
            items=[AccountRead.model_validate(a) for a in accounts], total=len(accounts)
        )
    tree = service.build_account_tree(accounts)
    return AccountTree(items=[AccountTreeNode.from_node(n) for n in tree], total=len(accounts))


@router.post("/accounts", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreate, db: DbSession) -> AccountRead:
    account = service.create_account(db, **body.model_dump())
    return AccountRead.model_validate(account)


@router.patch("/accounts/{account_id}", response_model=AccountRead)
def update_account(account_id: int, body: AccountUpdate, db: DbSession) -> AccountRead:
    account = service.update_account(db, account_id, **body.model_dump(exclude_unset=True))
    return AccountRead.model_validate(account)


@router.get("/journal-entries", response_model=JournalEntryList)
def list_journal_entries(
    db: DbSession,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    source_type: JournalSourceType | None = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JournalEntryList:
    page = service.list_journal(
        db,
        date_from=date_from,
        date_to=date_to,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )
    return JournalEntryList(
        items=[JournalEntryRead.from_entry(e) for e in page.items], total=page.total
    )


@router.get("/journal-entries/{entry_id}", response_model=JournalEntryRead)
def get_journal_entry(entry_id: int, db: DbSession) -> JournalEntryRead:
    return JournalEntryRead.from_entry(service.get_journal_entry(db, entry_id))


@router.get("/ledger/{account_id}", response_model=LedgerRead)
def get_ledger(
    account_id: int, db: DbSession, date_from: DateFrom = None, date_to: DateTo = None
) -> LedgerRead:
    ledger = service.get_ledger(db, account_id, date_from=date_from, date_to=date_to)
    return LedgerRead.from_ledger(ledger)
