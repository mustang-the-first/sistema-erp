# CLAUDE.md — Sistema ERP

Mini-ERP order-to-cash (pedido → remito → factura → cobranza) con asientos contables automáticos
por partida doble. Contexto Argentina (IVA 21% y 10,5%, facturas A/B simplificadas, sin ARCA).

**Fuente de verdad del diseño:** [`docs/plan.md`](docs/plan.md) — modelo de datos, contratos de la
API, asientos por operación y división del trabajo por olas. Si algo de este archivo o del código
contradice el plan, pará y preguntá; no improvises.

## Reglas no negociables

- **Alcance cerrado.** Solo los módulos del plan: auth/usuarios, catálogo y stock, ventas,
  contabilidad, cobranzas/cuenta corriente, reportes. Nada de features extra (ni notas de crédito,
  ni anulaciones, ni compras) salvo pedido explícito.
- **Capas:** `routers → services → repositories → models`.
  - `routers/api` (JSON `/api/v1`) y `routers/web` (Jinja2 + HTMX) son finos: validan entrada
    (schemas), llaman a un service, serializan. Sin lógica de negocio ni queries.
  - `services` tiene toda la lógica de negocio. Reciben `Session`, hacen `flush()`, **nunca
    `commit()`**. Lanzan excepciones de dominio de `app/core/errors.py`.
  - `repositories` encapsulan las queries. Sin reglas de negocio.
  - `models` son SQLAlchemy 2 (`Mapped[...]`, `mapped_column`).
- **Idioma:** código, identificadores, comentarios y commits en **inglés**. README y docs en
  **español**.
- **Dinero:** siempre `Decimal`, nunca `float`. Montos `Numeric(14,2)`, cantidades `Numeric(12,3)`.
  IVA por línea con `ROUND_HALF_UP` a 2 decimales; totales = suma de líneas (`app/core/money.py`).
- **Documentos emitidos son inmutables:** remitos, facturas, cobranzas y asientos no se editan ni
  borran.
- **Asientos:** solo vía `app/services/posting.py` (`post_entry`). Nadie inserta `journal_lines`
  a mano. Las cuentas se resuelven por `system_accounts`, nunca por código hardcodeado.
- **Saldo de factura:** solo vía `invoice_balance` de la Fundación. Ventas, Cobranzas y Reportes
  la usan; nadie la reimplementa.
- **Locks:** stock y numeración con `SELECT ... FOR UPDATE`. Varios productos → lockear **siempre
  ordenado por `product_id`** (evita deadlocks entre remitos concurrentes).
- **Transacción por request:** la dependency de sesión se declara con
  `Depends(get_db, scope="function")` para que el commit ocurra **antes** de enviar la respuesta;
  si el commit falla, la API responde 500.
- **Esquema:** solo la Fundación (Ola 1) toca `app/models/` y `migrations/`. En olas posteriores,
  cualquier cambio de esquema se reporta, no se hace.
- **Propiedad de archivos:** cada agente toca solo los archivos que le asigna `docs/plan.md` y solo
  importa de archivos de la Fundación (nunca de otro agente de la misma ola).

## Comandos

Requisitos: [uv](https://docs.astral.sh/uv/) y Docker. Python 3.12 (lo instala uv).

| Qué | Comando |
|---|---|
| Instalar dependencias | `make install` (`uv sync`) |
| Levantar Postgres | `make db` (`docker compose up -d db`; crea `erp` y `erp_test`) |
| Migrar | `make migrate` (`uv run alembic upgrade head`) |
| Nueva migración (solo Fundación) | `uv run alembic revision --autogenerate -m "..."` |
| Correr la app | `make run` → http://localhost:8000 (docs en `/docs`) |
| Stack completo | `make up` (`docker compose up --build`) |
| Lint + tipos | `make lint` (`ruff check`, `ruff format --check`, `mypy`) |
| Formatear | `make format` |
| Tests | `make test` (`uv run pytest --cov`) |
| Solo unit | `make test-unit` (`uv run pytest -m unit`) |
| Todo lo de CI | `make check` |

Variables en `.env` (ver `.env.example`), prefijo `ERP_`. Los tests usan `ERP_TEST_DATABASE_URL`.

## Entorno de ejecución (sesiones cloud / agentes)

- **El contenedor puede reiniciarse en cualquier momento** y matar todos los procesos en
  background (dockerd, Postgres, servidores). Antes de asumir que algo está caído por tu código,
  verificá que el proceso exista.
- **Nunca** corras `dockerd`, `uvicorn` ni otro proceso largo en primer plano. Lanzalo con
  `nohup <cmd> > /tmp/<nombre>.log 2>&1 &` y esperá con un bucle acotado:
  `timeout 60 sh -c 'until docker info >/dev/null 2>&1; do sleep 2; done'`.
- Si Docker no responde en 60 s, no lo uses: instalá PostgreSQL 17 por apt, creá el usuario
  `erp` y las bases `erp` y `erp_test`, y apuntá `ERP_DATABASE_URL` / `ERP_TEST_DATABASE_URL`
  ahí. El build de la imagen lo verifica el job `docker` del CI.
- **Todo comando que pueda colgarse va con `timeout`**: `timeout 600 make check`,
  `timeout 300 uv run pytest ...`, `timeout 20 psql ...`. Nunca reintentes igual un comando que
  colgó: diagnosticá primero (`pg_stat_activity`, `pg_locks`, `pg_blocking_pids`).
- **Una base de test por agente:** `ERP_TEST_AGENT=b` hace que los tests usen `erp_test_b` (el
  conftest la crea si falta). Agentes en paralelo nunca comparten base de test.

## Tests

- `tests/unit/<module>/` — lógica pura (cálculo de IVA, armado de asientos, letra de factura),
  sin DB. Marca `@pytest.mark.unit`.
- `tests/integration/<module>/` — services, repos y API contra PostgreSQL real. Cada test corre
  dentro de una transacción con `join_transaction_mode="create_savepoint"` y rollback al final.
  Marca `@pytest.mark.integration`.
- Tests de concurrencia (stock y numeración) — conexiones separadas, limpieza por `TRUNCATE`.
  Marca `@pytest.mark.concurrency`. Usan `committed_factory` (engine propio, nunca el de
  savepoints; no se combina con `db`) y el helper `run_concurrently` (Session propia por thread,
  cerrada en `finally`; `join` con timeout).
- **Un cuelgue es un fallo, nunca una espera:** pytest-timeout 30 s (60 s para `concurrency`), y
  las conexiones de test llevan `lock_timeout=5s`, `statement_timeout=15s` e
  `idle_in_transaction_session_timeout=10s`. No subas estos límites para que un test pase.
- Repetir para detectar flakiness: `timeout 300 uv run pytest -m concurrency --count=10 -x`.
- `tests/e2e/` — flujo order-to-cash completo con chequeos cruzados. Marca `@pytest.mark.e2e`.
- Datos de prueba con `tests/factories/`; clientes HTTP por rol con el fixture `client_as(role)`.
- Nombres: `test_<qué>_<condición>_<resultado>`. Estructura Arrange / Act / Assert.

## Definición de "terminado"

Una tarea está terminada cuando **todo** esto es cierto:

1. `make check` pasa en limpio (ruff, format, mypy estricto sobre `app/core` y `app/services`,
   pytest).
2. Toda regla de negocio nueva tiene tests en services: camino feliz **y** cada rechazo
   (`BusinessRuleViolation`, 403, 404). Cobertura de `app/services` ≥ 90%.
3. Cada endpoint nuevo coincide con el contrato de `docs/plan.md` (ruta, método, roles, body) y
   tiene al menos un test de API, incluido el 403 por rol.
4. Toda operación que contabiliza deja un asiento balanceado, y hay un test que lo verifica con
   montos concretos.
5. Si hay migración: `alembic upgrade head` sobre DB vacía funciona y `alembic check` no detecta
   diferencias (solo Fundación).
6. No se tocaron archivos fuera de los asignados.
7. Commits en inglés, en imperativo y atómicos (`Add invoice letter resolution`); CI en verde.
