# Deployment Runbook — Dev Phase

Step-by-step, from zero, to a running deployment of the **dev-phase stack**
([AZURE_ARCHITECTURE.md §0](AZURE_ARCHITECTURE.md#0-dev-phase-architecture-minimal-cost)):
App Service **B1 Basic** + Azure SQL **Basic**, ~$17/month. No Key Vault, VNet,
Application Insights, custom domain, or deployment slots — see
[AZURE_SETUP.md](AZURE_SETUP.md) for what each config value means once provisioned.

Replace anything in `<angle-brackets>` with your own values. Names below are examples —
pick your own globally-unique names where noted.

---

## 0. Prerequisites — Azure Account & CLI

1. Create an Azure account (skip if you already have one): https://azure.microsoft.com/free/
2. Install the Azure CLI:
   ```bash
   # macOS
   brew update && brew install azure-cli

   # verify
   az version
   ```
3. Log in — opens a browser for auth:
   ```bash
   az login
   ```
4. Confirm your subscription (create/select if you have more than one):
   ```bash
   az account list --output table
   az account set --subscription "<subscription-name-or-id>"
   ```

## 1. Set Shared Variables

Keeps the rest of the commands copy-pasteable without repeating values:

```bash
export RG_NAME="rg-brokersync-dev"
export LOCATION="eastus"
export SQL_SERVER_NAME="brokersync-dev-sql"        # must be globally unique
export SQL_DB_NAME="brokersync"
export SQL_ADMIN_USER="brokersync_admin"
export SQL_ADMIN_PASSWORD="BrokerSync@2026"
export APP_SERVICE_PLAN="brokersync-dev-plan"
export WEBAPP_NAME="brokersync-dev-api"            # must be globally unique
export JWT_SECRET="62726f6b657273796e632d6465762d7365637265742d6b65793332"
```

## 2. Resource Group

```bash
az group create --name "$RG_NAME" --location "$LOCATION"
```

## 3. Azure SQL — Basic Tier

```bash
# Logical server
az sql server create \
  --name "$SQL_SERVER_NAME" \
  --resource-group "$RG_NAME" \
  --location "$LOCATION" \
  --admin-user "$SQL_ADMIN_USER" \
  --admin-password "$SQL_ADMIN_PASSWORD"

# Database — Basic tier (5 DTU, 2GB, ~$4.90/month). Holds dbo plus every tenant
# schema created dynamically at signup — no per-tenant database provisioning here.
az sql db create \
  --resource-group "$RG_NAME" \
  --server "$SQL_SERVER_NAME" \
  --name "$SQL_DB_NAME" \
  --edition Basic \
  --capacity 5

# Firewall rule: allow Azure services (App Service) to reach this server
az sql server firewall-rule create \
  --resource-group "$RG_NAME" \
  --server "$SQL_SERVER_NAME" \
  --name "AllowAzureServices" \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0

# Firewall rule: allow your current machine to connect (for migrations/local dev)
MY_IP=$(curl -s https://api.ipify.org)
az sql server firewall-rule create \
  --resource-group "$RG_NAME" \
  --server "$SQL_SERVER_NAME" \
  --name "AllowMyIP" \
  --start-ip-address "$MY_IP" \
  --end-ip-address "$MY_IP"
```

## 4. App Service — B1 Basic Linux

```bash
# Plan
az appservice plan create \
  --name "$APP_SERVICE_PLAN" \
  --resource-group "$RG_NAME" \
  --location "$LOCATION" \
  --sku B1 \
  --is-linux

# Web app — Python 3.12 runtime
az webapp create \
  --name "$WEBAPP_NAME" \
  --resource-group "$RG_NAME" \
  --plan "$APP_SERVICE_PLAN" \
  --runtime "PYTHON:3.12"
```

## 5. Configure Application Settings

Sets the env vars documented in [AZURE_SETUP.md §1](AZURE_SETUP.md):

```bash
az webapp config appsettings set \
  --name "$WEBAPP_NAME" \
  --resource-group "$RG_NAME" \
  --settings \
    ENVIRONMENT="production" \
    SQL_SERVER="${SQL_SERVER_NAME}.database.windows.net" \
    SQL_DATABASE="$SQL_DB_NAME" \
    SQL_USER="$SQL_ADMIN_USER" \
    SQL_PASSWORD="$SQL_ADMIN_PASSWORD" \
    SQL_DRIVER="ODBC Driver 18 for SQL Server" \
    JWT_SECRET="$JWT_SECRET" \
    JWT_ACCESS_EXPIRY_MINUTES="30" \
    JWT_REFRESH_EXPIRY_DAYS="7" \
    CORS_ORIGINS="*" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"

# Startup command — runs startup.sh, which installs the ODBC driver then launches Gunicorn
az webapp config set \
  --name "$WEBAPP_NAME" \
  --resource-group "$RG_NAME" \
  --startup-file "startup.sh"
```

## 6. Deploy the Code

From the repo root (zip-deploy — App Service builds via `requirements.txt` because of
`SCM_DO_BUILD_DURING_DEPLOYMENT=true` set above):

```bash
zip -r deploy.zip . -x ".venv/*" ".git/*" "__pycache__/*" "*.pyc" "docs/*" ".env"

az webapp deploy \
  --name "$WEBAPP_NAME" \
  --resource-group "$RG_NAME" \
  --src-path deploy.zip \
  --type zip

rm deploy.zip
```

## 7. Run the Central Migration

The central (`dbo`) Alembic chain does not run automatically — run it once against the
deployed database from your local machine (it's on the firewall allowlist from §3):

```bash
export SQL_SERVER="${SQL_SERVER_NAME}.database.windows.net"
export SQL_DATABASE="$SQL_DB_NAME"
export SQL_USER="$SQL_ADMIN_USER"
export SQL_PASSWORD="$SQL_ADMIN_PASSWORD"
export SQL_DRIVER="ODBC Driver 18 for SQL Server"

alembic -c alembic_central.ini upgrade head
```

Tenant schemas have no migration chain to run here — each is created atomically
(`CREATE SCHEMA` + `metadata.create_all()`, one transaction) by the running app the
moment a user signs up, per `BACKEND_ARCHITECTURE.md` §2.4.

## 8. Smoke Test

```bash
BASE_URL="https://${WEBAPP_NAME}.azurewebsites.net"

# Health check
curl "$BASE_URL/health"

# Full signup -> upload -> snapshot round-trip
# (this creates a real tenant schema, e.g. "smoke_dss" — see §9 for cleanup)
curl -s -X POST "$BASE_URL/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"name":"Smoke","email":"smoke@test.com","password":"Str0ngPassw0rd!"}' \
  | tee /tmp/signup.json

ACCESS_TOKEN=$(python3 -c "import json;print(json.load(open('/tmp/signup.json'))['access_token'])")

curl -s -X POST "$BASE_URL/data/daily-upload" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trade_date":"2026-07-01","rows":[{"symbol":"TEST","metrics":{"PMC":100.5}}]}'

curl -s "$BASE_URL/data/latest" -H "Authorization: Bearer $ACCESS_TOKEN"
```

If the last command returns the uploaded row with `"PMC": 100.5`, the deployment is
fully working end-to-end.

## 9. Tear Down (cost control between work sessions)

Everything in this stack lives in one resource group, so a single command removes all
of it (App Service, SQL server/database, plan) and stops billing:

```bash
az group delete --name "$RG_NAME" --yes --no-wait
```

Re-running §1–8 recreates the environment from scratch — nothing here is meant to be
precious infrastructure at this phase.

## 10. What's Deliberately Not Set Up Yet

Per `AZURE_ARCHITECTURE.md` §0's deferred list — add these back when the trigger
condition is met, not before:

| Deferred item | Add it when... |
|---|---|
| Key Vault + Managed Identity | Real secrets or real tenant data exist |
| VNet + Private Endpoint + NSG | Azure SQL needs to lose its public endpoint |
| Application Insights + Log Analytics + Alerts | There's real traffic/SLOs worth watching |
| Custom domain + Managed Certificate + deployment slots | There's a public launch and zero-downtime releases matter |
| CI/CD (GitHub Actions / Azure DevOps) | Explicitly deferred by request — this doc's `az webapp deploy` step is the manual equivalent |
