# Deployment Runbook — Dev Phase

Step-by-step, from zero, to a running deployment of the **dev-phase stack**
([AWS_ARCHITECTURE.md §0](AWS_ARCHITECTURE.md#0-dev-phase-architecture-minimal-cost)):
EC2 **t3.micro** + RDS PostgreSQL **db.t3.micro**, $0/month for the first 12 months
(then ~$22/month). No Secrets Manager, private subnets, CloudWatch, custom domain, or
Auto Scaling Group.

Replace anything in `<angle-brackets>` with your own values. Names below are examples —
pick your own where noted.

---

## 0. Prerequisites — AWS Account & CLI

1. Create an AWS account (skip if you already have one): https://aws.amazon.com/free/
2. Install the AWS CLI:

   ```bash
   # macOS
   brew install awscli

   # verify
   aws --version
   ```

3. Configure credentials (or SSO login, per your organization's setup):
   ```bash
   aws configure
   # or: aws sso login --profile <your-profile>
   ```
4. Confirm the account/region you're targeting:
   ```bash
   aws sts get-caller-identity
   aws configure set region ap-south-1
   ```

## 1. Set Shared Variables

Keeps the rest of the commands copy-pasteable without repeating values:

```bash
export AWS_REGION="ap-south-1"
export VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
export DB_INSTANCE_ID="brokersync-dev-db"
export DB_NAME="brokersync"
export DB_USER="brokersync_admin"
export DB_PASSWORD="BrokerSync-2024-Secure_DevPass123"
export EC2_KEY_NAME="brokersync-dev-key"
export EC2_INSTANCE_NAME="brokersync-dev-api"
export JWT_SECRET="62726f6b657273796e632d6465762d7365637265742d6b65793332"
```

## 2. Security Groups

```bash
# EC2 security group: inbound 22 (SSH) and 8000 (API) from your IP only
MY_IP=$(curl -s https://checkip.amazonaws.com)
EC2_SG_ID=$(aws ec2 create-security-group \
  --group-name brokersync-ec2-sg \
  --description "Broker Sync API dev EC2" \
  --vpc-id "$VPC_ID" \
  --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$EC2_SG_ID" \
  --protocol tcp --port 22 --cidr "${MY_IP}/32"
aws ec2 authorize-security-group-ingress --group-id "$EC2_SG_ID" \
  --protocol tcp --port 8000 --cidr "${MY_IP}/32"

# RDS security group: inbound 5432 only from the EC2 security group
RDS_SG_ID=$(aws ec2 create-security-group \
  --group-name brokersync-rds-sg \
  --description "Broker Sync API dev RDS" \
  --vpc-id "$VPC_ID" \
  --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$RDS_SG_ID" \
  --protocol tcp --port 5432 --source-group "$EC2_SG_ID"
```

## 3. RDS — PostgreSQL, db.t3.micro

```bash
aws rds create-db-instance \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 16 \
  --allocated-storage 20 \
  --storage-type gp3 \
  --master-username "$DB_USER" \
  --master-user-password "$DB_PASSWORD" \
  --db-name "$DB_NAME" \
  --vpc-security-group-ids "$RDS_SG_ID" \
  --no-publicly-accessible \
  --no-multi-az \
  --backup-retention-period 1

# Wait for it to become available (a few minutes)
aws rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE_ID"

RDS_ENDPOINT=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --query 'DBInstances[0].Endpoint.Address' --output text)
echo "RDS endpoint: $RDS_ENDPOINT"
```

## 4. EC2 — t3.micro, Amazon Linux 2023

```bash
# Key pair for SSH access
aws ec2 create-key-pair --key-name "$EC2_KEY_NAME" \
  --query 'KeyMaterial' --output text > "${EC2_KEY_NAME}.pem"
chmod 400 "${EC2_KEY_NAME}.pem"

# Latest Amazon Linux 2023 AMI
AMI_ID=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=al2023-ami-*-x86_64" "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.micro \
  --key-name "$EC2_KEY_NAME" \
  --security-group-ids "$EC2_SG_ID" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$EC2_INSTANCE_NAME}]" \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

# Elastic IP so the address survives stop/start between work sessions
ALLOC_ID=$(aws ec2 allocate-address --domain vpc --query 'AllocationId' --output text)
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID"

EC2_PUBLIC_IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
  --query 'Addresses[0].PublicIp' --output text)
echo "EC2 public IP: $EC2_PUBLIC_IP"
```

## 5. Configure the Instance

SSH in and set up the app:

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP"
```

On the instance:

```bash
sudo dnf install -y python3.12 python3.12-pip git
git clone git@github.com:IamAKX/broker-sync-api.git /home/ec2-user/broker-sync-api
cd /home/ec2-user/broker-sync-api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `/home/ec2-user/broker-sync-api/.env` (see [§1 config reference](#1-required-configuration-values)
below) with the values from your local shell (substitute `$RDS_ENDPOINT`, `$DB_USER`,
`$DB_PASSWORD`, `$DB_NAME`, `$JWT_SECRET` from §1/§3 above):

```bash
cat > /home/ec2-user/broker-sync-api/.env <<EOF
ENVIRONMENT=production
SQL_SERVER=$RDS_ENDPOINT
SQL_DATABASE=$DB_NAME
SQL_USER=$DB_USER
SQL_PASSWORD=$DB_PASSWORD
JWT_SECRET=$JWT_SECRET
JWT_ACCESS_EXPIRY_MINUTES=30
JWT_REFRESH_EXPIRY_DAYS=7
CORS_ORIGINS=*
EOF
```

Create the systemd service:

```bash
sudo tee /etc/systemd/system/brokersync.service <<'EOF'
[Unit]
Description=Broker Sync API
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/broker-sync-api
EnvironmentFile=/home/ec2-user/broker-sync-api/.env
ExecStart=/bin/bash -c 'source /home/ec2-user/broker-sync-api/.venv/bin/activate && exec /home/ec2-user/broker-sync-api/.venv/bin/python -m gunicorn --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 app.main:app'
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now brokersync
```

## 6. Run the Central Migration

The central (`public`) Alembic chain does not run automatically — run it once, either
from the EC2 instance (it can already reach RDS via the security-group rule) or from
your local machine if you additionally allow your IP on the RDS security group:

```bash
# From the EC2 instance, inside /home/ec2-user/broker-sync-api with the venv active:
alembic -c alembic_central.ini upgrade head
```

Tenant schemas have no migration chain to run here — each is created atomically
(`CREATE SCHEMA` + `metadata.create_all()`, one transaction) by the running app the
moment a user signs up, per `BACKEND_ARCHITECTURE.md` §2.4.

## 7. Smoke Test

```bash
BASE_URL="http://${EC2_PUBLIC_IP}:8000"

curl "$BASE_URL/health"

curl -s -X POST "$BASE_URL/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"name":"Smoke","email":"smoke@test.com","password":"Str0ngPassw0rd!"}' \
  | tee /tmp/signup.json

ACCESS_TOKEN=$(python3 -c "import json;print(json.load(open('/tmp/signup.json'))['access_token'])")

curl -s -X POST "$BASE_URL/historic/daily-upload" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trade_date":"2026-07-01","rows":[{"symbol":"TEST","metrics":{"PMC":100.5}}]}'

curl -s "$BASE_URL/historic/latest" -H "Authorization: Bearer $ACCESS_TOKEN"
```

If the last command returns the uploaded row with `"PMC": 100.5`, the deployment is
fully working end-to-end.

## 8. Connect to the Database Locally

RDS is created `--no-publicly-accessible` (§3) and its security group only allows
port 5432 from the EC2 security group (§2) — your laptop can't reach it directly.
Two ways around that:

### 8a. SSH Tunnel via EC2 (recommended, no security group change)

EC2 can already reach RDS, so tunnel through it:

```bash
ssh -i "${EC2_KEY_NAME}.pem" -N -L 5432:"$RDS_ENDPOINT":5432 ec2-user@"$EC2_PUBLIC_IP"
```

Leave that running, then in another terminal connect to `localhost:5432` as normal:

```bash
psql "postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME"
```

No RDS security group change needed — traffic rides the existing EC2→RDS rule.

### 8b. Direct Connection (temporary, opens RDS to your IP)

Only if you need a GUI client that can't tunnel, or don't have EC2 access. Adds a
network rule exposing RDS to your current public IP — **remove it when done**:

```bash
MY_IP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id "$RDS_SG_ID" \
  --protocol tcp --port 5432 --cidr "${MY_IP}/32"

psql "postgresql://$DB_USER:$DB_PASSWORD@$RDS_ENDPOINT:5432/$DB_NAME"

# revoke once finished — don't leave RDS open to the internet
aws ec2 revoke-security-group-ingress --group-id "$RDS_SG_ID" \
  --protocol tcp --port 5432 --cidr "${MY_IP}/32"
```

Your IP changes across networks/reconnects — re-run the `authorize` step with a fresh
`MY_IP` if the connection stops working.

## 9. Tear Down (cost control between work sessions)

Stop (don't terminate) the EC2 instance to pause compute billing while keeping the EIP,
disk, and config intact:

```bash
aws ec2 stop-instances --instance-ids "$INSTANCE_ID"
```

Restart later with:

```bash
aws ec2 start-instances --instance-ids "$INSTANCE_ID"
```

To fully tear down everything (end of the project, not just a pause between sessions):

```bash
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID"
aws ec2 release-address --allocation-id "$ALLOC_ID"
aws rds delete-db-instance --db-instance-identifier "$DB_INSTANCE_ID" --skip-final-snapshot
aws ec2 delete-security-group --group-id "$EC2_SG_ID"
aws ec2 delete-security-group --group-id "$RDS_SG_ID"
```

Re-running §2-§7 recreates the environment from scratch — nothing here is meant to be
precious infrastructure at this phase.

## 10. Redeploy Changes

Pushed new code to the repo and need it live on EC2:

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP"
```

On the instance:

```bash
cd /home/ec2-user/broker-sync-api
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only needed if dependencies changed

# only if the central migration chain gained new revisions:
alembic -c alembic_central.ini upgrade head

sudo systemctl restart brokersync
sudo systemctl status brokersync   # confirm "active (running)"
```

One-liner from your local machine (skips the interactive shell):

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP" \
  'cd /home/ec2-user/broker-sync-api && git pull && source .venv/bin/activate && pip install -r requirements.txt && sudo systemctl restart brokersync'
```

Then smoke test with the `curl "$BASE_URL/health"` check from §7 to confirm the
restarted service is actually up before walking away.

## 11. Check Server Logs

The systemd service (§5) logs to the journal (`StandardOutput=journal` /
`StandardError=journal`) — no separate log file to find.

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP"

# tail live logs (Ctrl+C to stop)
sudo journalctl -u brokersync -f

# last 200 lines
sudo journalctl -u brokersync -n 200

# logs since a given time
sudo journalctl -u brokersync --since "1 hour ago"

# only errors/warnings
sudo journalctl -u brokersync -p err
```

One-liner from your local machine:

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP" 'sudo journalctl -u brokersync -n 200 --no-pager'
```

Also check the service is actually running (crashed/failed units show here, not just
in the log tail):

```bash
ssh -i "${EC2_KEY_NAME}.pem" ec2-user@"$EC2_PUBLIC_IP" 'sudo systemctl status brokersync'
```

---

## 1. Required Configuration Values

All settings are read by [`app/core/config.py`](../app/core/config.py) via
`pydantic-settings`. Same variable names are used locally (`.env`) and on the EC2
instance — only _where_ the file lives differs.

| Variable                    | Example                                                | Description                                               |
| --------------------------- | ------------------------------------------------------ | --------------------------------------------------------- |
| `ENVIRONMENT`               | `development` / `production`                           | Toggles debug behavior (e.g. SQL echo, docs exposure)     |
| `SQL_SERVER`                | `brokersync-dev-db.xxxxx.ap-south-1.rds.amazonaws.com` | RDS instance endpoint hostname                            |
| `SQL_DATABASE`              | `brokersync`                                           | Database name — holds `public` and every tenant schema    |
| `SQL_USER`                  | `brokersync_admin`                                     | RDS master username                                       |
| `SQL_PASSWORD`              | `<secret>`                                             | RDS master password                                       |
| `JWT_SECRET`                | `<random 32+ byte string>`                             | HS256 signing secret for access tokens                    |
| `JWT_ACCESS_EXPIRY_MINUTES` | `30`                                                   | Access token lifetime, per `BACKEND_ARCHITECTURE.md` §3.4 |
| `JWT_REFRESH_EXPIRY_DAYS`   | `7`                                                    | Refresh token lifetime                                    |
| `CORS_ORIGINS`              | `http://localhost:5173`                                | Comma-separated allowed origins                           |

## 2. Where Each Value Lives

| Location                                                  | Used for          | Notes                                                                                                                                                                                |
| --------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.env` (gitignored, copy from `.env.example`)             | Local development | Loaded automatically by `pydantic-settings`; never committed                                                                                                                         |
| `/home/ec2-user/broker-sync-api/.env` on the EC2 instance | Deployed app      | Loaded into the systemd service via `EnvironmentFile=` — plain file, not encrypted at rest (dev-phase only; upgrade to Secrets Manager for production, see `AWS_ARCHITECTURE.md` §1) |

## 3. Connection Details

No ODBC driver or client-side install is needed — `asyncpg`/`psycopg[binary]` are
pure-Python/bundled-binary PostgreSQL drivers.

Connection string format, built at runtime in `app/core/config.py`:

```
postgresql+asyncpg://<SQL_USER>:<SQL_PASSWORD>@<SQL_SERVER>:5432/<SQL_DATABASE>
```

There's only one database (and therefore one connection string) — per-tenant isolation
happens via `schema_translate_map` at the session level
(`app/db/tenant_session.py`), not via a different connection target.

## 4. Migrations: Central Only

There is **one** Alembic chain, for `public`:

| Config                | Targets                                            | When it runs                             |
| --------------------- | -------------------------------------------------- | ---------------------------------------- |
| `alembic_central.ini` | `public` schema (`Tenant`, `User`, `RefreshToken`) | Manually, once per deploy — see §6 above |

**Tenant schemas have no migration chain.** Their tables (`Stock`, `Metric`,
`HistoricalStockValue`) are created once, directly via SQLAlchemy's `metadata.create_all()`
(schema-bound via `schema_translate_map`), when a tenant schema is provisioned at
signup — see `BACKEND_ARCHITECTURE.md` §2.4/§3.2.

## 5. Local Quickstart

```bash
docker run -d --name brokersync-pg \
  -e POSTGRES_USER=brokersync -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=brokersync \
  -p 5432:5432 postgres:16

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SQL_* and JWT_SECRET (defaults match the docker run above)
alembic -c alembic_central.ini upgrade head
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/docs`.
