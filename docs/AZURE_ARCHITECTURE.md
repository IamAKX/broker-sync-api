# Azure Infrastructure Architecture

Companion to [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md), which specifies the
FastAPI application's HLD/LLD. This document maps that application onto concrete Azure
services: what's needed to run it, secure it, observe it, and operate it in production.

Compute choice: **Azure App Service** (Linux, Python runtime) — simplest PaaS fit for
the current scale (tens of tenants), avoids container/orchestration overhead. CI/CD
pipeline is deliberately **out of scope for this pass** (deploy manually / via `az` CLI
for now; revisit GitHub Actions vs. Azure DevOps later).

This doc has two tracks:
- **§0 Dev-Phase Architecture** — what to actually provision right now. Minimal service
  set, cheapest SKUs, no network isolation. Fine for a solo/small-team dev environment
  where the data isn't sensitive yet and uptime doesn't matter.
- **§1–7 Production Target Architecture** — the hardened design to grow into before
  real tenant data or any public launch. Adds network isolation, secret management,
  and observability back in.

---

## 0. Dev-Phase Architecture (Minimal Cost)

Only what's required to run and reach the API. Everything else in §1's table is
**deferred until staging/prod** — see the "why deferred" column below.

The app is **schema-per-tenant** (see `BACKEND_ARCHITECTURE.md` §2.2): one Azure SQL
database total, with a shared `dbo` schema plus one schema per signed-up tenant. Cost
is therefore **flat regardless of tenant count** — the schema-per-tenant model is
exactly what keeps it that way, since Azure SQL bills per database, not per schema:

| Service | Role | Tier | Cost/month (East US, pay-as-you-go) |
|---|---|---|---|
| **Azure App Service** (Linux) | Hosts the FastAPI app | **B1 Basic** (1 vCPU/1.75GB) | **$12.41** ($0.017/hr × 730) |
| **Azure SQL Database** | `dbo` + every tenant schema, one database | **Basic** (5 DTU, 2GB max) | **$4.90** ($0.161/day × 30.44) |
| **Resource Group** | Deployment boundary | — | $0 |

**Total: ~$17.31/month, regardless of how many tenants sign up** — the entire point of
choosing schema-per-tenant over database-per-tenant (`BACKEND_ARCHITECTURE.md` §2.2):
Azure SQL bills per database, not per schema, so tenant count never multiplies this
number.

How this gets you a working, reasonably secure dev environment without the deferred
services:

- **TLS**: use the default `<app-name>.azurewebsites.net` hostname — App Service issues
  free TLS for it automatically. No custom domain or Managed Certificate needed yet.
- **Secrets** (JWT signing secret, SQL connection string): store directly in App
  Service **Application Settings**. These are encrypted at rest and never touch source
  control — sufficient for solo dev. No Key Vault, no Managed Identity.
- **SQL network access**: keep Azure SQL's **public endpoint**, locked down with a
  **server firewall rule** (either "Allow Azure services" or the App Service's specific
  outbound IPs). No VNet, Private Endpoint, or NSG required — those only pay for
  themselves once there's a compliance/security reason to remove the public endpoint
  entirely.
- **Deployment**: single slot, deploy directly via `az webapp deploy`. No
  staging/production slot pair yet — there's nothing to blue-green until there are
  real users.
- **Observability**: App Service's built-in log streaming (`az webapp log tail`) and
  the Azure Portal's Log Stream/Metrics are enough to debug during dev. No Application
  Insights, Log Analytics workspace, or Monitor Alerts — those cost real money
  ($2.30/GB ingested) and mostly pay off once there's traffic worth alerting on.

**Explicitly deferred vs. §1** (added back for staging/prod, in rough priority order):
1. **Key Vault + Managed Identity** — before any real secrets or tenant data.
2. **VNet + Private Endpoint + NSG** — before Azure SQL should lose its public endpoint.
3. **Application Insights + Log Analytics + Monitor Alerts** — once there's traffic/SLOs
   worth watching.
4. **Custom domain + Managed Certificate, deployment slots** — once there's a public
   launch and zero-downtime releases matter.
5. **Premium v3 App Service tier** — only needed to support #2 (VNet integration); B1
   has no VNet integration support.

---

## 1. Production Target — Service Inventory

| Service | Role | Notes / Tier Guidance |
|---|---|---|
| **Azure App Service** (Linux) | Hosts the FastAPI app (via Gunicorn+Uvicorn workers) | Plan: **P0v3/P1v3** (Premium v3) — needed for VNet integration; App Service Plan supports multiple deployment slots (`staging`, `production`) |
| **Azure SQL Database** | Primary datastore — `dbo` + per-tenant schemas | Start on **General Purpose (serverless), Gen5**, autoscale vCores — cost-efficient at low/bursty load; move to Provisioned if traffic becomes steady |
| **Azure Key Vault** | Secrets: JWT signing secret, SQL connection string/credentials, any third-party API keys | App Service reads via **Key Vault references** in Application Settings, not baked into env files |
| **Managed Identity** (system-assigned, on App Service) | Lets the app authenticate to Key Vault and (optionally) Azure SQL **without stored credentials** | Grant `Key Vault Secrets User` role; optionally enable **Azure AD auth to SQL** using the identity instead of a SQL login/password |
| **Virtual Network (VNet) + Subnets** | Network isolation boundary | One subnet for App Service (regional VNet integration, outbound only), one for the SQL private endpoint |
| **Private Endpoint** (for Azure SQL) | Removes Azure SQL's public internet exposure; traffic from App Service → SQL stays on the Microsoft backbone | Pair with **Private DNS Zone** (`privatelink.database.windows.net`) linked to the VNet |
| **Network Security Group (NSG)** | Subnet-level allow/deny rules | Deny inbound to the SQL subnet from anything except the App Service subnet |
| **Application Insights** | APM: request tracing, latency, exceptions, dependency calls (SQL) | Correlate with `structlog`'s `tenant_id` + `request_id` via custom dimensions on each log/trace |
| **Log Analytics Workspace** | Backing store for App Insights + App Service diagnostic logs; KQL queries for debugging/audits | One workspace per environment (dev/staging/prod) |
| **Azure Monitor Alerts** | Paging/notification on SLO breaches | Alert rules: 5xx rate, p95 latency, SQL DTU/vCore saturation, failed login spike (brute-force signal) |
| **App Service Managed Certificate + Custom Domain** | TLS for the public API hostname | Free, auto-renewing; bind to custom domain (e.g. `api.yourproduct.com`) |
| **Azure AD / Entra ID** | **Not** used for end-user auth (app has its own JWT/bcrypt auth per `BACKEND_ARCHITECTURE.md` §3.4) | Used only for **Azure resource RBAC** — who on the team can deploy, read Key Vault, view logs |
| **Resource Group(s)** | Deployment/lifecycle boundary | One per environment: `rg-brokersync-dev`, `rg-brokersync-staging`, `rg-brokersync-prod` |
| **Azure SQL automated backups** | Point-in-time restore (built-in, no separate service) | Default 7–35 day PITR retention; consider **geo-redundant backup storage** for prod |

### Deferred / explicitly out of scope for this pass

| Service | Why deferred |
|---|---|
| **CI/CD** (GitHub Actions or Azure DevOps Pipelines) | Explicitly deferred per user — will decide later; deploy manually via `az webapp deploy` / `az acr build` until then |
| **Azure API Management (APIM)** | Would front the API for rate limiting, quota, and API key management — `BACKEND_ARCHITECTURE.md` §4 already lists rate limiting as future scope; add APIM when that's picked up |
| **Azure Front Door / WAF** | Only warranted once the API is broadly public and needs edge caching, geo-routing, or WAF rules beyond App Service's own TLS/network controls |
| **Azure Container Registry + Container Apps/AKS** | Not needed while running directly on App Service's native Python runtime; revisit only if the app needs to be containerized for portability |
| **Blob Storage** | No current requirement to persist raw upload files or export artifacts — add if audit/raw-file retention becomes a requirement |

---

## 2. Updated Component Diagram

```
                              Internet
                                 │
                                 │  HTTPS (TLS via App Service
                                 │  Managed Certificate)
                                 ▼
                    ┌────────────────────────┐
                    │   Azure App Service     │   Linux, Python runtime
                    │   (Premium v3 plan)     │   Gunicorn + Uvicorn workers
                    │   FastAPI application   │   Deployment slots: staging / production
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼───────────────────────┐
              │  System-assigned Managed Identity        │
              │  (no stored credentials)                 │
              ▼                                          ▼
   ┌─────────────────────┐                    ┌────────────────────────┐
   │   Azure Key Vault     │                    │  Regional VNet          │
   │  - JWT secret         │                    │  Integration (outbound) │
   │  - SQL credentials    │                    └───────────┬─────────────┘
   └─────────────────────┘                                │
                                                            │  Private Endpoint
                                                            ▼
                                          ┌──────────────────────────────────┐
                                          │        Azure SQL Database         │
                                          │                                    │
                                          │ ┌──────────────────┐               │
                                          │ │ dbo (central)     │ Tenant, User, │
                                          │ │                   │ RefreshToken  │
                                          │ ├──────────────────┤               │
                                          │ │ sundar_dss        │ Stock, Metric,│
                                          │ │                   │ DailyStockValue│
                                          │ ├──────────────────┤               │
                                          │ │ ravi_dss          │ Stock, Metric,│
                                          │ │                   │ DailyStockValue│
                                          │ └──────────────────┘               │
                                          │  Automated backups (PITR)          │
                                          └──────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  Observability (cross-cutting, wired into App Service)           │
   │  Application Insights → Log Analytics Workspace → Monitor Alerts │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 3. Networking & Security Topology

- **Public surface**: only the App Service's HTTPS endpoint (custom domain + managed
  cert) is internet-facing. HTTP→HTTPS redirect enforced, TLS 1.2+ minimum.
- **Azure SQL has no public endpoint** — reachable only through the Private Endpoint
  from the App Service's VNet-integrated subnet. This is a stronger boundary than
  SQL firewall rules (which only filter by IP, still traverse the public internet).
- **Secrets never touch source control or App Service's plaintext Application
  Settings** — Key Vault references (`@Microsoft.KeyVault(...)`) resolve at runtime via
  the app's managed identity.
- **Managed identity → SQL** (optional but recommended over a SQL login/password):
  create a **contained database user mapped to the managed identity** in `dbo`; the app
  connects with Azure AD token auth (`pyodbc`/`aioodbc` support this via the
  `ActiveDirectoryMsi` authentication mode). Removes one more long-lived secret.
- **NSGs** on both subnets: App Service subnet allows outbound to the SQL subnet only
  on port 1433 (plus Key Vault/Monitor over Azure backbone via service endpoints/private
  link); SQL subnet denies all inbound except from the App Service subnet.
- **Environment isolation**: separate resource groups (and separate Azure SQL logical
  servers or at minimum separate databases) for dev/staging/prod — a bug in staging
  should never be able to touch prod tenant schemas.

---

## 4. Observability Wiring

Maps directly onto `BACKEND_ARCHITECTURE.md` §3.5's `structlog` requirement
("every log line carries `tenant_id` and `request_id`"):

- App Insights SDK (`opencensus`/`azure-monitor-opentelemetry`) instruments FastAPI
  automatically for request/dependency (SQL call) tracing.
- Custom log processor attaches `tenant_id` and `request_id` as **custom dimensions**
  on every trace — enables querying "all errors for tenant X" in Log Analytics via KQL,
  without scanning raw text logs.
- Recommended alert rules (Azure Monitor, on Log Analytics queries or App Insights
  metrics):
  - HTTP 5xx rate > threshold over 5 min
  - p95 latency > threshold over 5 min
  - Azure SQL DTU/vCore utilization > 80% sustained
  - Spike in `401`/`InvalidCredentialsError` responses (possible credential stuffing)
  - App Service deployment slot swap failures

---

## 5. Environment & Deployment Strategy

- **Resource groups**: `rg-brokersync-dev`, `rg-brokersync-staging`, `rg-brokersync-prod`
  — each with its own App Service Plan, Azure SQL (server or database), Key Vault, and
  Log Analytics workspace. No shared infra between environments.
- **Deployment slots** (prod App Service Plan): `staging` slot for pre-production
  validation, swap into `production` slot for zero-downtime releases. This is
  independent of whatever CI/CD tool is picked later — slots work the same whether the
  swap is triggered manually or by a pipeline.
- **Database migrations on deploy**: Alembic's central-chain migrations run against
  `dbo` as a pre-swap step. Tenant schemas have **no migration chain at all** — their
  tables are created once via SQLAlchemy `metadata.create_all()` at signup
  (`BACKEND_ARCHITECTURE.md` §2.4) and never altered afterward, since the EAV design
  means metric changes are row-level, not schema-level. There is nothing equivalent to
  "replay a migration across all existing tenants" to build here.

---

## 6. Cost Estimate (East US, pay-as-you-go, priced against Azure's retail rates)

| Service | Basis | Cost/month |
|---|---|---|
| App Service Plan P0v3 (Linux) | $0.0775/hr × 730 hrs | **$56.58** |
| Azure SQL — GP Serverless Gen5 compute | $0.5218/vCore-hr, ~1 vCore active ~6 hrs/day | **~$94** |
| Azure SQL — storage | $0.115/GB/month × ~20GB | **~$2** |
| Azure SQL — PITR backups | included up to 100% of DB size | $0 |
| Key Vault (Standard) | $0.03/10K ops, app-scale secret reads | **~$1** |
| App Insights + Log Analytics | $2.30/GB ingested × ~10GB/month | **~$23** |
| Private Endpoint (SQL) | ~$0.01/hr + ~$0.01/GB processed | **~$8** |
| VNet, Subnets, NSGs | no gateway | $0 |
| Managed Certificate, deployment slots | free / no extra compute charge | $0 |

**Total: ~$185/month per environment, regardless of tenant count** (P1v3 instead of
P0v3: **~$242/month**) — schema-per-tenant (§0/§2 above) means this doesn't multiply
as tenants sign up; one Azure SQL database serves every tenant's schema.

Dominant costs:
1. **App Service Premium v3** (required for VNet integration) — fixed cost regardless
   of traffic; the main reason to delay APIM/Front Door until actually needed.
2. **Azure SQL serverless compute** — the biggest variable. Scales with actual active
   vCore-seconds; a daily-upload-driven access pattern that auto-pauses nights/weekends
   keeps this cheap, but sustained usage could push it past $200/month on its own.
3. **App Insights + Log Analytics** — usage-based, scales with log/trace volume.

Running staging and dev on this same hardened track roughly triples the total (isolated
resource groups, no shared infra) — which is exactly why **§0's minimal dev-phase stack
exists**: dev doesn't need VNet isolation or Premium v3 to be useful, so it runs on
~$17/month instead of ~$185/month until there's an actual reason (real tenant data,
compliance, public launch) to harden it.

Re-evaluate compute tier (App Service → Container Apps) only if tenant count or traffic
grows enough that Premium v3's fixed cost stops being the cheaper option.

---

## 7. Open Items (deferred, tracked)

Carried over / added to `BACKEND_ARCHITECTURE.md` §4:

- CI/CD platform and pipeline — explicitly deferred by request.
- Rate limiting / API quota — likely via APIM once needed.
- Azure AD token auth to SQL (vs. SQL login/password) — recommended but not mandatory
  for a first deploy; can be layered in without app code changes beyond the connection
  string/auth mode.
