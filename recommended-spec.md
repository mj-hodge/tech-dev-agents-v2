# Recommended VM Spec — Dev Agent Pool

> Last updated: 2026-03-30

## VM Configuration

| Field | Value | Rationale |
|-------|-------|-----------|
| **SKU** | B2ms (Burstable) | Best cost/perf for bursty agent workloads — idle during API calls, spike during Docker builds and test runs |
| **vCPU** | 2 | Sufficient for Docker Compose stack + agent CLI + Playwright headless |
| **RAM** | 8 GB | Covers heaviest project stack (tech-datawarehouse w/ SQL Server: ~3 GB) + agent overhead (~1.5 GB) + 3.5 GB headroom |
| **OS Disk** | 64 GB Premium SSD (P6) | Docker images + project repos + build cache. Premium SSD for Docker layer I/O |
| **OS** | Ubuntu 24.04 LTS | Long-term support, native Docker, matches production base images |
| **Region** | Same as gc-development subscription (East US) | Low latency to ACR, shared DB, and Azure services |
| **Networking** | vnet-dev-agents, NSG allows SSH (22) from your IP only | Agents don't need public HTTP — Docker services are internal only |
| **Identity** | System-assigned managed identity | Access to ACR pull, Key Vault secrets, Log Analytics — no stored credentials |
| **Auto-shutdown** | 7 PM → 7 AM local, weekends | Cuts cost ~60%. Agents schedule work within active hours |

## Pool Size

| Count | Purpose | Monthly Cost (always-on) | Monthly Cost (with auto-shutdown) |
|-------|---------|--------------------------|----------------------------------|
| 3 | One per concurrent agent | ~$180 | ~$70-90 |

## Pre-Installed Software

| Software | Version | Purpose |
|----------|---------|---------|
| Docker Engine + Compose | Latest stable | Project dev stacks |
| Node.js | 22 LTS | Claude Code CLI |
| Python | 3.12 | Backend projects (via uv) |
| uv | Latest | Python package management |
| Git | Latest | Source control |
| GitHub CLI (gh) | Latest | PR creation, workflow triggers |
| Azure CLI (az) | Latest | Terraform, ACR, infra operations |
| Terraform | Latest stable | Infrastructure drift checks (Phase 11) |
| Playwright | Latest (system deps) | Browser testing (headless Chromium) — install via `npx playwright install --with-deps chromium` |
| trivy | Latest | Container CVE scanning (Phase 11) |
| gitleaks | Latest | Secrets scanning (Phase 11) |

## Resource Budget Per Project Stack

| Project | Docker RAM (idle) | Docker RAM (under test) | Fits on B2ms? |
|---------|-------------------|------------------------|----------------|
| advertising-amazon | ~1.3 GB | ~2.0 GB | Yes (6 GB headroom idle) |
| product-health-dashboard | ~0.5 GB | ~1.0 GB | Yes (7 GB headroom idle) |
| tech-datawarehouse | ~2.3 GB | ~3.0 GB | Yes (4.5 GB headroom idle) |
| tech-project-mapping | ~0.5 GB | ~0.7 GB | Yes (7 GB headroom idle) |

## Agent Overhead (constant per VM)

| Component | RAM | Notes |
|-----------|-----|-------|
| Agent process (Claude Code CLI) | ~300 MB | Node.js, bursty CPU |
| Docker daemon | ~200 MB | Always running |
| OS + git + filesystem buffers | ~500 MB | Baseline |
| Playwright (headless Chromium) | ~512 MB | Only during browser tests |
| **Total** | **~1.5 GB** | Leaves ~6.5 GB for project Docker stack |

## Floating Agent Model

Agents are **not dedicated** to a single project. Any agent picks up any story from any project.

**Cold start cost:** First run on a new project requires `git clone` + `docker compose pull` (~2-5 min). Subsequent runs use cached images and local repo.

**Repo management:** All project repos pre-cloned to `/home/agent/projects/`. Agent runs `git fetch && git checkout <branch>` before starting work.

## Port Allocation Strategy

Each VM runs **one agent working on one story at a time**, so no port conflicts. Standard ports:

| Service | Port |
|---------|------|
| API / MCP Server | 8000 |
| Auth / secondary | 8001 |
| PostgreSQL | 5432 |
| SQL Server | 1433 |
| Redis | 6379 |
| Azurite | 10000-10002 |
| Prometheus | 9090 |
| Grafana | 3000 |
| Loki | 3100 |
| Frontend | 3001 |

No port offsets needed — one agent, one VM, one stack.

## Cost Optimization

| Strategy | Savings | Implementation |
|----------|---------|---------------|
| Auto-shutdown (nights/weekends) | ~60% | `az vm auto-shutdown` — 12 hrs active / 12 hrs off + weekends |
| Deallocate between stories | ~70-80% | Agent calls `az vm deallocate` when story is done; orchestrator calls `az vm start` when new story is assigned |
| Spot instances (future) | ~60-90% | B2ms spot: ~$6-18/mo. Risk: eviction mid-work. Mitigate with frequent git commits (already enforced by SDLC Phase 8) |
| Reserved instances (if stable) | ~35-40% | 1-year reservation if consistently running 3 agents |

## Scaling Path

| Agents | VMs | SKU | Monthly Cost (with auto-shutdown) |
|--------|-----|-----|----------------------------------|
| 1 | 1 | B2ms | ~$25-30 |
| 3 | 3 | B2ms | ~$70-90 |
| 5 | 5 | B2ms | ~$120-150 |
| 5+ | Pool | B2ms + orchestrator | Consider VMSS or container-based agents |

## Azure Resource Structure

```
gc-development (subscription)
└── rg-dev-agents
    ├── vm-agent-01        (B2ms, Ubuntu 24.04)
    ├── vm-agent-02        (B2ms, Ubuntu 24.04)
    ├── vm-agent-03        (B2ms, Ubuntu 24.04)
    ├── acr-gcdev           (Basic tier, shared container registry)
    ├── vnet-dev-agents     (10.1.0.0/16)
    │   └── snet-agents     (10.1.1.0/24)
    ├── nsg-agents          (SSH from admin IP only)
    └── kv-dev-agents       (API keys, tokens — accessed via managed identity)
```
