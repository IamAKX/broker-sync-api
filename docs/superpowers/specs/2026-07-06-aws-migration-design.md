# Azure → AWS Migration Design (Dev Phase)

Migrate `broker-sync-api`'s deployment target from Azure to AWS. API logic, multi-tenant
model (schema-per-tenant), and business rules are unchanged — this is a deployment/infra
and database-dialect migration only. Scope is the **dev-phase** stack (low cost, matches
the existing Azure dev-phase philosophy of "cheapest tier that works, harden later").

Region: **ap-south-1** (Mumbai) — closest AWS region to Azure's `southindia`.

---

## 1. Database: Azure SQL Server → RDS PostgreSQL

**Why Postgres over keeping SQL Server on RDS**: native `CREATE SCHEMA` support fits
the schema-per-tenant model exactly, no ODBC driver dependency, and RDS Postgres
`db.t3.micro` is free-tier eligible (RDS SQL Server generally is not, and Express
edition caps at 10GB).

**Instance**: `db.t3.micro`, PostgreSQL 16, single-AZ, 20GB gp3 storage, `ap-south-1`.
Free tier: 750 hrs/month + 20GB storage for 12 months, then ~$13.60/month.

**Fresh start** — no data migration from the existing Azure SQL dev database. New
Postgres schema stands up via Alembic; tenants sign up again.

### Code changes (dialect-level only; business logic untouched)

| File | Change |
|---|---|
| `app/core/config.py` | `mssql+aioodbc` → `postgresql+asyncpg` (async URL); `mssql+pyodbc` → `postgresql+psycopg` (sync URL, for Alembic). Drop `driver=`/`Encrypt=yes`/`TrustServerCertificate=no` query params; add `sslmode=require`. |
| `app/models/central.py` | `sqlalchemy.dialects.mssql.UNIQUEIDENTIFIER` → `sa.Uuid(as_uuid=True)` (dialect-agnostic, maps to native Postgres `UUID`). `func.sysutcdatetime()` → `func.now()`. |
| `app/models/tenant.py` | `func.sysutcdatetime()` → `func.now()` (on `Metric.created_at`, `DailyStockValue.updated_at`). |
| `migrations/central/versions/0001_initial_central_schema.py` | `mssql.UNIQUEIDENTIFIER()` → `sa.Uuid()`; `sa.text("sysutcdatetime()")` → `sa.text("now()")`. |
| `app/repositories/daily_value_repo.py` | T-SQL `MERGE INTO ... WHEN MATCHED/NOT MATCHED` → Postgres `INSERT ... ON CONFLICT (trade_date, stock_id, metric_id) DO UPDATE SET ...`. Bracket quoting `[Table]` → double-quote `"Table"`. `SYSUTCDATETIME()` → `now()`. Update the `_MERGE_BATCH_SIZE` comment (SQL Server's ~2100 param ceiling doesn't apply to Postgres — batch size can stay for readability/lock scope, but the *reason* in the comment changes). |
| `app/services/provisioning_service.py` | `CREATE SCHEMA [name]` → `CREATE SCHEMA "name"`. SQL Server error code `"2714"` (duplicate schema) → Postgres SQLSTATE `"42P06"` (`duplicate_schema`). `SELECT schema_name FROM [Tenant]` → `SELECT schema_name FROM "Tenant"`. |
| `tests/conftest.py` | Teardown simplifies: Postgres `DROP SCHEMA "x" CASCADE` replaces the per-table `DROP TABLE` dance (that was itself a SQL-Server-can't-drop-nonempty-schema workaround). Bracket quoting → double-quote throughout. |
| `requirements.txt` | Remove `aioodbc`, `pyodbc`. Add `asyncpg`, `psycopg[binary]`. |
| `startup.sh` | Remove the ODBC driver install block entirely — pure-Python Postgres drivers need no system package. |
| `app/db/central_session.py`, `app/db/tenant_session.py` | Update comments referencing "Basic-tier Azure SQL (5 DTU)" / "Azure SQL's idle-connection resets" to describe RDS `db.t3.micro` pool sizing instead. Pool settings (`pool_size=5, max_overflow=5`) stay reasonable for `db.t3.micro`'s connection limits — no numeric change needed, just comment accuracy. |

---

## 2. Compute: Azure App Service → EC2

**Instance**: `t3.micro`, Amazon Linux 2023, `ap-south-1`. Free tier: 750 hrs/month for
12 months, then ~$8.50/month. Elastic IP attached so the address survives stop/start
(mirrors Azure's "tear down between sessions" cost-control pattern — instance gets
**stopped**, not terminated, between work sessions; EIP avoids re-doing DNS/config on
restart).

- No Docker/containerization — matches the current "no container, just run the process"
  approach. Same `gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker`
  command as today, run via a systemd unit (`brokersync.service`) instead of App
  Service's startup-command mechanism.
- Deploy: `rsync`/`git pull` the repo onto the instance, `pip install -r
  requirements.txt` into a venv, `systemctl restart brokersync`. Direct analog of the
  current manual `az webapp deploy` zip-deploy — CI/CD stays explicitly deferred, same
  as it is today.
- `startup.sh` becomes the systemd `ExecStart` command (minus the ODBC install step,
  removed per §1).

---

## 3. Secrets

Plain `.env` file on the EC2 instance (not committed to git), loaded into the
`brokersync` systemd service via `EnvironmentFile=/opt/brokersync/.env`. Direct
equivalent of Azure App Service's plaintext Application Settings for dev — no Secrets
Manager/Parameter Store yet, upgraded later the same way Key Vault was deferred on
Azure.

---

## 4. Networking

Default VPC, `ap-south-1`. Two security groups:

- `brokersync-ec2-sg` — inbound 22 (SSH) and 8000 (API) from your IP only.
- `brokersync-rds-sg` — inbound 5432 **only from `brokersync-ec2-sg`** (security-group
  reference, not an IP range) — actually tighter than Azure's current "Allow Azure
  services" blanket firewall rule, at no extra cost or complexity.

No ALB, no ACM cert, no VPC private subnets/NAT yet — all deferred to the
production-target track, same posture as Azure's dev-phase (public endpoint + firewall
rule only).

---

## 5. Documentation Restructuring

Replace `AZURE_ARCHITECTURE.md`, `AZURE_DEPLOYMENT.md`, `AZURE_SETUP.md` with **two**
AWS docs (no requirement to keep a 1:1 file mapping):

- **`docs/AWS_ARCHITECTURE.md`** — merges AZURE_ARCHITECTURE.md's role. Two tracks like
  today:
  - §0 Dev-phase: EC2 + RDS Postgres, cost table, component diagram, security posture.
  - §1+ Production-target: ALB + ACM (TLS), Secrets Manager, VPC private subnets + NAT
    gateway, Multi-AZ RDS, CloudWatch Logs + Alarms, IAM roles (replacing Managed
    Identity) — one-to-one mapping of every item in the current Azure production-target
    table to its AWS equivalent.
- **`docs/AWS_DEPLOYMENT.md`** — merges AZURE_DEPLOYMENT.md + AZURE_SETUP.md: AWS CLI
  prerequisites, step-by-step `aws` CLI provisioning (VPC lookup, security groups, RDS
  instance, EC2 instance, EIP), config reference (env var table, Postgres connection
  string format), deploy steps, smoke test (same signup → upload → snapshot round-trip
  as today), teardown (`terminate-instances` + `delete-db-instance --skip-final-snapshot`
  — cost control between sessions, same spirit as `az group delete`).

**Light edits** (not full rewrites):
- `docs/BACKEND_ARCHITECTURE.md` — swap "Azure SQL Server"/"Azure SQL" → "RDS
  PostgreSQL", update §3.1/§3.2 SQL type columns (`UNIQUEIDENTIFIER`→`UUID`,
  `sysutcdatetime()`→`now()`, `NVARCHAR`→`VARCHAR`, `BIT`→`BOOLEAN`), update §2.1/§2.2
  diagrams and rationale text ("Azure SQL bills per database" → "RDS bills per
  instance, not per schema" — same underlying argument for schema-per-tenant), update
  §3.4 ("secret from Azure Key Vault" → "secret from env var, later Secrets Manager"),
  §3.5 tech stack (`pyodbc`/`aioodbc` → `asyncpg`/`psycopg`).
- `docs/API_CONTRACT.md` — update base-URL convention (drop `*.azurewebsites.net`
  example, note EC2 public IP/DNS or later custom domain).
- `README.md` — update "Infra" bullet, quickstart (drop ODBC driver install step),
  "Deploying" section pointing at `AWS_DEPLOYMENT.md`, doc table.

---

## 6. Cost Summary

| | Azure (current) | AWS (new) |
|---|---|---|
| Compute | App Service B1 (~$12.41/mo) | EC2 t3.micro (~$8.50/mo after free tier; **$0** first 12mo) |
| Database | Azure SQL Basic (~$4.90/mo) | RDS db.t3.micro Postgres (~$13.60/mo after free tier; **$0** first 12mo) |
| **Total** | **~$17.31/mo flat** | **~$0/mo for 12 months, then ~$22/mo** |

Decision: keep RDS separate from EC2 (rather than co-hosting Postgres on the EC2
instance) — during the free-tier year both options cost $0, and co-hosting would
contend for `t3.micro`'s 1GB RAM and forfeit RDS's automated backups/patching. Revisit
only if the free tier expires and cost pressure reappears.

---

## 7. Out of Scope (deferred, same posture as Azure track)

- CI/CD (GitHub Actions) — explicitly deferred, same as today.
- Load balancer / TLS custom domain — deferred to production-target track.
- Secrets Manager / Parameter Store — deferred, plain `.env` for now.
- VPC private subnets, NAT gateway, Multi-AZ RDS — deferred to production-target track.
- CloudWatch Logs/Alarms — deferred, EC2 console/SSH log tailing sufficient for dev.
