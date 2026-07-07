# AWS Infrastructure Architecture

Companion to [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md), which specifies the
FastAPI application's HLD/LLD. This document maps that application onto concrete AWS
services: what's needed to run it, secure it, observe it, and operate it in production.

Compute choice: **EC2** (t3.micro, no container) — simplest fit for the current scale
(tens of tenants), avoids container/orchestration overhead. CI/CD pipeline is
deliberately **out of scope for this pass** (deploy manually via `scp`/`git pull` +
`systemctl restart` for now).

This doc has two tracks:
- **§0 Dev-Phase Architecture** — what to actually provision right now. Minimal service
  set, cheapest SKUs (free-tier eligible), no network isolation beyond security groups.
  Fine for a solo/small-team dev environment where the data isn't sensitive yet and
  uptime doesn't matter.
- **§1+ Production Target Architecture** — the hardened design to grow into before real
  tenant data or any public launch. Adds network isolation, secret management, and
  observability back in.

---

## 0. Dev-Phase Architecture (Minimal Cost)

Only what's required to run and reach the API. Everything else in §1's table is
**deferred until staging/prod**.

The app is **schema-per-tenant** (see `BACKEND_ARCHITECTURE.md` §2.2): one RDS
PostgreSQL instance total, with a shared `public` schema plus one schema per signed-up
tenant. Cost is therefore **flat regardless of tenant count** — the schema-per-tenant
model is exactly what keeps it that way, since RDS bills per instance, not per schema:

| Service | Role | Tier | Cost/month (ap-south-1) |
|---|---|---|---|
| **EC2** | Hosts the FastAPI app | **t3.micro** (2 vCPU burstable, 1GB RAM) | **$0** (750 free hrs/mo, first 12mo), then ~**$8.50** |
| **RDS PostgreSQL** | `public` + every tenant schema, one instance | **db.t3.micro** (2 vCPU burstable, 1GB RAM, 20GB gp3) | **$0** (750 free hrs/mo + 20GB storage, first 12mo), then ~**$13.60** |
| **VPC (default)** | Network boundary | — | $0 |
| **Elastic IP** (attached to a running instance) | Stable address across stop/start | — | $0 while attached to a running instance |

**Total: $0/month for the first 12 months, then ~$22.10/month, regardless of how many
tenants sign up** — the entire point of choosing schema-per-tenant over
database-per-tenant (`BACKEND_ARCHITECTURE.md` §2.2): RDS bills per instance, not per
schema, so tenant count never multiplies this number.

How this gets you a working, reasonably secure dev environment without the deferred
services:

- **TLS**: none yet — plain HTTP on the EC2 instance's public IP for local/dev API
  calls. No custom domain or ACM certificate needed yet (mirrors the equivalent
  Azure dev-phase choice of relying on the free platform-issued hostname instead of a
  custom domain).
- **Secrets** (JWT signing secret, DB connection details): stored directly in a `.env`
  file on the EC2 instance, loaded into the systemd service via `EnvironmentFile=`.
  Never committed to source control — sufficient for solo dev. No Secrets Manager, no
  IAM database authentication.
- **DB network access**: RDS instance created **without a public endpoint** — reachable
  only from the EC2 instance's security group. This is actually tighter than the
  Azure-firewall-rule equivalent, at no extra cost.
- **Deployment**: single instance, deploy directly via `scp`/`git pull` +
  `systemctl restart`. No blue-green, no deployment slots — there's nothing to swap
  until there are real users.
- **Observability**: `journalctl -u brokersync -f` (systemd's built-in log tailing) is
  enough to debug during dev. No CloudWatch Logs/Alarms — those cost real money once
  there's meaningful log volume, and mostly pay off once there's traffic worth alerting
  on.

**Explicitly deferred vs. §1** (added back for staging/prod, in rough priority order):
1. **Secrets Manager / SSM Parameter Store** — before any real secrets or tenant data.
2. **VPC private subnets + NAT gateway** — before the EC2 instance needs to be fully
   unreachable except via a bastion/VPN.
3. **CloudWatch Logs + Alarms** — once there's traffic/SLOs worth watching.
4. **ALB + ACM certificate, custom domain** — once there's a public launch and
   zero-downtime releases matter.
5. **RDS Multi-AZ** — only needed once uptime during a single instance's maintenance
   window actually matters.

---

## 1. Production Target — Service Inventory

| Service | Role | Notes / Tier Guidance |
|---|---|---|
| **Application Load Balancer (ALB)** | Fronts the FastAPI app, terminates TLS | Pairs with an **ACM** certificate for the public hostname; enables zero-downtime deploys via target-group swaps |
| **EC2 Auto Scaling Group** (or ECS Fargate) | Hosts the FastAPI app across ≥2 instances | Replaces the single dev-phase instance once uptime during a deploy/AZ failure matters |
| **RDS PostgreSQL — Multi-AZ** | Primary datastore — `public` + per-tenant schemas | Start on `db.t3.micro`/`db.t4g.micro`, scale instance class with load; Multi-AZ gives automatic failover |
| **AWS Secrets Manager** | Secrets: JWT signing secret, DB credentials, any third-party API keys | EC2 reads via IAM role + `GetSecretValue` at boot, not baked into `.env` files |
| **IAM Role** (EC2 instance profile) | Lets the app authenticate to Secrets Manager and (optionally) RDS **without stored credentials** | Grant least-privilege `secretsmanager:GetSecretValue`; optionally enable **IAM database authentication** to RDS instead of a password |
| **VPC private subnets + NAT Gateway** | Network isolation boundary | EC2 instances in private subnets (no public IP), NAT for outbound-only internet access; ALB in public subnets |
| **Security Groups** | Instance/RDS-level allow/deny rules | ALB security group allows inbound 443 from the internet; EC2 security group allows inbound only from the ALB; RDS security group allows inbound only from the EC2 security group |
| **CloudWatch Logs** | Centralized application/systemd logs | Correlate with `structlog`'s `tenant_id` + `request_id` via a log group per environment |
| **CloudWatch Alarms** | Paging/notification on SLO breaches | Alarms: 5xx rate, p95 latency (via ALB target-group metrics), RDS CPU/connections, failed-login spike (brute-force signal) |
| **ACM Certificate + Route 53 (optional)** | TLS for the public API hostname | Free (ACM), auto-renewing; bind to a custom domain via Route 53 or an external DNS provider |
| **IAM Users/Roles (team access)** | **Not** used for end-user auth (app has its own JWT/bcrypt auth per `BACKEND_ARCHITECTURE.md` §3.4) | Used only for **AWS resource access control** — who on the team can deploy, read Secrets Manager, view logs |
| **RDS automated backups** | Point-in-time restore (built-in, no separate service) | Default 7-day retention (configurable up to 35 days); consider cross-region backup copy for prod |

### Deferred / explicitly out of scope for this pass

| Service | Why deferred |
|---|---|
| **CI/CD** (GitHub Actions or CodePipeline) | Explicitly deferred per user — will decide later; deploy manually via `scp`/`git pull` until then |
| **API Gateway** | Would front the API for rate limiting, quota, and API key management — `BACKEND_ARCHITECTURE.md` §4 already lists rate limiting as future scope; add when that's picked up |
| **CloudFront + WAF** | Only warranted once the API is broadly public and needs edge caching, geo-routing, or WAF rules beyond ALB's own TLS/security-group controls |
| **ECR + ECS/EKS** | Not needed while running directly on EC2's native Python runtime; revisit only if the app needs to be containerized for portability |
| **S3** | No current requirement to persist raw upload files or export artifacts — add if audit/raw-file retention becomes a requirement |

---

## 2. Updated Component Diagram

```
                              Internet
                                 │
                                 │  HTTPS (TLS via ALB + ACM cert)
                                 ▼
                    ┌────────────────────────┐
                    │  Application Load       │
                    │  Balancer (ALB)         │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼───────────────────────┐
              │  EC2 Auto Scaling Group (private subnet) │
              │  IAM instance role (no stored creds)     │
              ▼                                          ▼
   ┌─────────────────────┐                    ┌────────────────────────┐
   │  AWS Secrets Manager │                    │  VPC private subnets   │
   │  - JWT secret         │                    │  + NAT Gateway         │
   │  - DB credentials     │                    │  (outbound only)       │
   └─────────────────────┘                    └───────────┬─────────────┘
                                                            │
                                                            ▼
                                          ┌──────────────────────────────────┐
                                          │     RDS PostgreSQL (Multi-AZ)     │
                                          │                                    │
                                          │ ┌──────────────────┐               │
                                          │ │ public (central)  │ Tenant, User, │
                                          │ │                   │ RefreshToken  │
                                          │ ├──────────────────┤               │
                                          │ │ sundar_dss        │ Stock, Metric,│
                                          │ │                   │ HistoricalStockValue│
                                          │ ├──────────────────┤               │
                                          │ │ ravi_dss          │ Stock, Metric,│
                                          │ │                   │ HistoricalStockValue│
                                          │ └──────────────────┘               │
                                          │  Automated backups (PITR)          │
                                          └──────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  Observability (cross-cutting, wired into EC2/ALB)                │
   │  CloudWatch Logs → Log groups → CloudWatch Alarms                 │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 3. Networking & Security Topology

- **Public surface**: only the ALB's HTTPS listener (ACM cert) is internet-facing.
  HTTP→HTTPS redirect enforced, TLS 1.2+ minimum.
- **RDS has no public endpoint** — reachable only from the EC2 security group inside
  the VPC. This is a stronger boundary than IP-based firewall rules, since it doesn't
  depend on the caller's source IP at all.
- **Secrets never touch source control or a plaintext `.env` file in production** —
  Secrets Manager entries are fetched at instance boot via the IAM instance role.
- **IAM database authentication** (optional but recommended over a password): generates
  short-lived auth tokens via `rds:GenerateDBAuthToken`, removing one more long-lived
  secret from the stack.
- **Security groups** on both tiers: ALB security group allows inbound 443 from the
  internet; EC2 security group allows inbound only from the ALB security group; RDS
  security group allows inbound only from the EC2 security group.
- **Environment isolation**: separate VPCs (or at minimum separate RDS instances and
  EC2 instances) for dev/staging/prod — a bug in staging should never be able to touch
  prod tenant schemas.

---

## 4. Observability Wiring

Maps directly onto `BACKEND_ARCHITECTURE.md` §3.5's `structlog` requirement
("every log line carries `tenant_id` and `request_id`"):

- The systemd service's stdout/stderr ships to a CloudWatch Logs log group via the
  CloudWatch agent.
- Custom log processor attaches `tenant_id` and `request_id` as structured JSON fields
  on every log line — enables querying "all errors for tenant X" in CloudWatch Logs
  Insights without scanning raw text logs.
- Recommended alarms (CloudWatch, on ALB target-group metrics or Logs Insights queries):
  - HTTP 5xx rate > threshold over 5 min
  - p95 latency > threshold over 5 min
  - RDS CPU/connection utilization > 80% sustained
  - Spike in `401`/`invalid_credentials` responses (possible credential stuffing)
  - EC2 instance health-check failures

---

## 5. Environment & Deployment Strategy

- **Per-environment isolation**: separate VPCs (or at minimum separate security groups
  and RDS instances) for dev/staging/prod — no shared infra between environments.
- **Deployment**: dev-phase is a single EC2 instance, direct `systemctl restart` on
  deploy. Production-target adds an Auto Scaling Group behind the ALB — new instances
  register with the target group, old ones drain and terminate, giving zero-downtime
  releases independent of whatever CI/CD tool is picked later.
- **Database migrations on deploy**: Alembic's central-chain migrations run against
  the `public` schema as a pre-deploy step. Tenant schemas have **no migration chain at
  all** — their tables are created once via SQLAlchemy `metadata.create_all()` at
  signup (`BACKEND_ARCHITECTURE.md` §2.4) and never altered afterward, since the EAV
  design means metric changes are row-level, not schema-level.

---

## 6. Cost Estimate (ap-south-1, on-demand rates, post-free-tier)

| Service | Basis | Cost/month |
|---|---|---|
| EC2 Auto Scaling (2× t3.small) | ~$0.023/hr × 730 hrs × 2 | **~$33.60** |
| RDS PostgreSQL Multi-AZ (db.t3.small) | ~$0.052/hr × 730 hrs × 2 (primary+standby) | **~$76.00** |
| RDS storage | $0.12/GB/month × ~20GB | **~$2.40** |
| RDS automated backups | included up to 100% of DB size | $0 |
| Secrets Manager | $0.40/secret/month × ~3 secrets | **~$1.20** |
| ALB | $0.0225/hr × 730 hrs + LCU usage | **~$20** |
| CloudWatch Logs | $0.50/GB ingested × ~10GB/month | **~$5** |
| NAT Gateway | $0.045/hr × 730 hrs + data processing | **~$35** |

**Total: ~$173/month per environment, regardless of tenant count** — schema-per-tenant
(§0/§2 above) means this doesn't multiply as tenants sign up; one RDS instance serves
every tenant's schema.

Dominant costs:
1. **NAT Gateway** — fixed hourly cost regardless of traffic; the main reason to delay
   the private-subnet architecture until actually needed (a public subnet + tightly
   scoped security group is a reasonable middle ground before this is warranted).
2. **RDS Multi-AZ** — doubles compute cost for automatic failover.
3. **ALB** — fixed hourly cost plus usage-based LCU charges.

Running staging and dev on this same hardened track roughly triples the total (isolated
VPCs, no shared infra) — which is exactly why **§0's minimal dev-phase stack exists**:
dev doesn't need Multi-AZ or a NAT Gateway to be useful, so it runs on ~$0/month during
the free-tier year (then ~$22/month) instead of ~$173/month until there's an actual
reason (real tenant data, compliance, public launch) to harden it.

---

## 7. Open Items (deferred, tracked)

Carried over / added to `BACKEND_ARCHITECTURE.md` §4:

- CI/CD platform and pipeline — explicitly deferred by request.
- Rate limiting / API quota — likely via API Gateway once needed.
- IAM database authentication to RDS (vs. password) — recommended but not mandatory for
  a first deploy; can be layered in without app code changes beyond the connection
  string/auth mode.
