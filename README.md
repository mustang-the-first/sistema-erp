# Sistema ERP

Mini-ERP con flujo **order-to-cash** (pedido → remito → factura → cobranza) que genera asientos
contables automáticos por partida doble. Pensado para el contexto argentino: IVA 21% y 10,5%,
facturas A/B simplificadas (sin integración con ARCA).

> Proyecto de portfolio en construcción. El diseño completo está en [`docs/plan.md`](docs/plan.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL · Jinja2 + HTMX · pytest ·
Docker Compose · GitHub Actions.

## Cómo correrlo

Requisitos: [uv](https://docs.astral.sh/uv/) y Docker.

```bash
cp .env.example .env
make install      # dependencias
make db           # Postgres en Docker (bases erp y erp_test)
make migrate      # migraciones
make run          # http://localhost:8000  ·  docs de la API en /docs
```

O todo en contenedores: `make up`.

## Tests y calidad

```bash
make lint   # ruff + mypy
make test   # pytest con cobertura
make check  # lo mismo que corre el CI
```

## Estado

- [x] Ola 0 — esqueleto (`/health`, Docker, CI)
- [ ] Ola 1 — fundación (modelos, migración, motor de asientos)
- [ ] Ola 2 — módulos (auth, catálogo/stock, ventas, contabilidad, cobranzas, reportes)
- [ ] Ola 3 — integración y test end-to-end
- [ ] Ola 4 — deploy y demo
