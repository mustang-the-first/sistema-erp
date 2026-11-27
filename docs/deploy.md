# Deploy de la demo pública: Render + Neon

Web service gratis en **Render** (región Virginia, Blueprint [`render.yaml`](../render.yaml)) y
PostgreSQL gratis en **Neon** (`aws-us-east-1`). El reset diario de los datos corre en
**GitHub Actions** ([`demo-reset.yml`](../.github/workflows/demo-reset.yml)).

Ningún valor secreto va al repositorio: se cargan en los dashboards.

## 1. Neon

1. Crear el proyecto en la región **AWS US East 1 (N. Virginia)**.
2. En *Connect*, copiar el connection string **directo**: con *Connection pooling*
   **desactivado**, el host **no** termina en `-pooler`. Formato:

   ```
   postgresql://USUARIO:CONTRASEÑA@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   - Se usa **tal cual viene**. La app acepta `postgresql://` o `postgres://` y la convierte al
     driver psycopg 3. `sslmode=require` y `channel_binding=require` los entiende la libpq que
     trae psycopg (verificado: conexión TLS 1.3 con channel binding, migraciones y seed).
   - **Por qué el directo y no el pooler:** el pooler de Neon es PgBouncer en modo transacción,
     y psycopg 3 prepara sentencias del lado del servidor después de 5 ejecuciones; además las
     migraciones y el reset usan transacciones con DDL y `SET LOCAL`. La demo usa pocas
     conexiones, así que el directo alcanza y evita esos casos. Usá **el mismo string directo**
     en Render y en GitHub.

## 2. Generar los secretos (en tu máquina)

```bash
# ERP_SECRET_KEY: 64 caracteres aleatorios (mínimo exigido: 32)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# alternativa sin Python
openssl rand -base64 48

# ERP_DEMO_PASSWORD: la contraseña compartida de las 3 cuentas demo (mínimo 8 caracteres).
# Es pública a propósito: la página de login de la demo la muestra.
```

## 3. Render: variables del servicio `sistema-erp-demo`

Al sincronizar el Blueprint, Render pide estas variables (marcadas `sync: false`):

| Variable | Valor |
|---|---|
| `ERP_DATABASE_URL` | Connection string **directo** de Neon (paso 1) |
| `ERP_SECRET_KEY` | Salida del comando del paso 2 |
| `ERP_DEMO_MODE` | `true` |
| `ERP_DEMO_PASSWORD` | La contraseña demo elegida |

`ERP_ENV=production` ya viene fijada en `render.yaml`. Si falta alguna variable o es inválida,
el deploy falla al arrancar y el log dice cuál es (ej. `Refusing to start in production:
ERP_SECRET_KEY must be set (>= 32 chars)`).

## 4. GitHub: reset diario (`demo-reset.yml`)

*Settings → Secrets and variables → Actions*:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `DEMO_DATABASE_URL` | **El mismo** connection string directo cargado en Render |
| Secret | `DEMO_SECRET_KEY` | Cualquier valor de 32+ caracteres (puede ser el mismo de Render; el reset no lo usa, pero producción lo exige para arrancar) |
| Secret | `DEMO_PASSWORD` | **Exactamente la misma** que `ERP_DEMO_PASSWORD` en Render |
| Variable | `DEMO_RESET_ENABLED` | `true` |

> ⚠️ `DEMO_PASSWORD` tiene que coincidir con `ERP_DEMO_PASSWORD`: el reset vuelve a crear los
> usuarios con esa contraseña, y el login de la demo muestra la de Render. Si difieren, nadie
> puede entrar después del primer reset.

El workflow corre todos los días a las 06:00 UTC (03:00 de Argentina) y también a mano desde
*Actions → Demo reset → Run workflow*. GitHub ejecuta los workflows programados **solo desde la
rama por defecto** del repositorio.

## 5. Verificación después del deploy

1. `GET /health` → `{"status": "ok"}`.
2. Login con `admin@demo.local`, `vendedor@demo.local` y `contador@demo.local`.
3. Flujo pedido → remito → factura → cobranza con el vendedor.
4. *Actions → Demo reset → Run workflow* una vez, y volver a entrar.

## Notas del plan gratuito

- El servicio de Render se duerme tras 15 minutos sin tráfico; la primera visita después tarda
  alrededor de un minuto.
- Neon suspende el cómputo tras 5 minutos sin uso y despierta en la próxima conexión
  (`pool_pre_ping` descarta conexiones cortadas). Un proyecto gratis inactivo 90 días puede
  borrarse: el reset diario lo mantiene activo.
