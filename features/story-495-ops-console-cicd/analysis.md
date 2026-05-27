# Analysis Report

## Summary
| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | GitHub Actions + SCP Deploy |
| Confidence | High |
| Divergent assessments | 0 |

## Context Weights
| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 20% | Infrastructure work — must be reliable and not cause outages |
| Future Flexibility | 10% | Single VM target, staging env is out of scope (future story) |
| Business Value | 25% | Direct impact: two production outages in one day drove this story |
| Implementation Effort | 25% | Medium scope, constrained VM resources — simplicity is critical |
| Risk Profile | 20% | Production system that agents depend on for dispatch queue |

## Approaches Evaluated

### Approach A: GitHub Actions + SCP Deploy (Seed Proposal)
Build frontend on GitHub-hosted runner (7GB RAM), SCP pre-built artifacts to VM, docker cp backend files into container, restart with health check, rollback on failure.

### Approach B: GitHub Actions + Docker Image Rebuild
Build a new Docker image in CI containing both backend and frontend, push to a container registry (GHCR or ACR), pull on VM, swap containers.

### Approach C: GitHub Actions + Self-Hosted Runner on VM
Register the ops-console VM as a self-hosted runner, run the workflow locally but skip npm build (download pre-built artifact from a separate CI job on GH-hosted runner).

## Evaluation Agents
| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness + Flexibility | 3 |
| Business | Value + Effort | 3 |
| Risk | Risk Profile + Register | 3 |

## Scoring Matrix
| Approach | Technical | Flexibility | Value | Effort | Risk | Weighted |
|----------|-----------|------------|-------|--------|------|----------|
| A: SCP Deploy | 4/5 | 3/5 | 5/5 | 5/5 | 4/5 | **4.30** |
| B: Docker Rebuild | 5/5 | 5/5 | 4/5 | 2/5 | 3/5 | **3.60** |
| C: Self-Hosted Runner | 3/5 | 2/5 | 4/5 | 3/5 | 2/5 | **2.95** |

### Weighted Calculation Detail
- **A**: (4×0.20) + (3×0.10) + (5×0.25) + (5×0.25) + (4×0.20) = 0.80 + 0.30 + 1.25 + 1.25 + 0.80 = **4.30**
- **B**: (5×0.20) + (5×0.10) + (4×0.25) + (2×0.25) + (3×0.20) = 1.00 + 0.50 + 1.00 + 0.50 + 0.60 = **3.60**
- **C**: (3×0.20) + (2×0.10) + (4×0.25) + (3×0.25) + (2×0.20) = 0.60 + 0.20 + 1.00 + 0.75 + 0.40 = **2.95**

## Divergent Assessments
_No significant divergent assessments. All dimensions consistently favor Approach A for this context._

## Top 3 Ranking

### 1. GitHub Actions + SCP Deploy (Weighted: 4.30)
- **Why #1:** Directly addresses the root cause (building on the VM) with minimal moving parts. Matches the existing architecture exactly — static files to nginx, docker cp for backend, container restart. No new infrastructure required.
- **Key strength:** Implementation effort is minimal — 3 files (workflow YAML, deploy script, rollback script). No registry, no image builds, no runner registration.
- **Key risk:** SSH key management between GitHub Actions and the VM; NSG IP allowlisting for GitHub's runner IP ranges (which rotate).
- **Trade-off:** No container immutability — the container is mutated in place via docker cp rather than replaced. Acceptable for single-VM, but won't scale to multi-node.

### 2. Docker Image Rebuild (Weighted: 3.60)
- **Why #2:** Best technical architecture — immutable containers, clean rollback via image tags, natural path to staging/blue-green. However, significantly more effort: need a Dockerfile that builds frontend + backend, a container registry, image pull credentials on the VM, and a container swap strategy that preserves the database connection.
- **Key strength:** Future flexibility is excellent — registry-based deploys are the natural path to staging environments and multi-VM.
- **Key risk:** Container swap requires careful orchestration to avoid dropping the database socket. The current setup has app + postgres as separate containers without docker-compose — adding image-based deploys means either introducing compose or scripting the swap manually.
- **Trade-off:** 2-3x implementation effort for architectural benefits that are out of scope (staging env, multi-node).

### 3. Self-Hosted Runner (Weighted: 2.95)
- **Why #3:** Eliminates the SCP/SSH complexity but introduces new risks. A self-hosted runner on the VM would consume memory and CPU for the runner agent itself. The VM is already constrained (2GB usable RAM). Also, self-hosted runners on public repos have security implications.
- **Key strength:** No SSH key management or NSG rules needed — the runner phones home to GitHub.
- **Key risk:** Runner agent consumes resources on an already constrained VM. Runner security on a production system. If the runner crashes, deploys stop entirely with no fallback.
- **Trade-off:** Solves one problem (SSH access) but creates worse problems (resource contention, security surface).

## Risk Register Highlights (Top 3 approaches)
| ID | Approach | Risk | Severity | Mitigation |
|----|----------|------|----------|-----------|
| R-A-1 | SCP Deploy | GitHub Actions runner IPs change, breaking NSG rules | Medium | Use GitHub's published IP ranges API; add a scheduled workflow to update NSG rules, or use a wider CIDR block |
| R-A-2 | SCP Deploy | SSH deploy key compromise gives VM access | Medium | Restrict deploy key to specific commands via `authorized_keys` command restriction; key rotation schedule |
| R-A-3 | SCP Deploy | Health check passes but app is broken (false positive) | Medium | Health check should verify both HTTP 200 and a known response body (e.g., version endpoint returns new commit SHA) |
| R-A-4 | SCP Deploy | Partial deploy — frontend copies but container restart fails | High | Deploy script must be atomic: copy to staging dir, then swap. Rollback script restores previous dist + previous backend files |
| R-B-1 | Docker Rebuild | Container swap drops active API connections | High | Requires graceful shutdown signal handling in FastAPI; drain connections before swap |
| R-B-2 | Docker Rebuild | Registry credentials management on VM | Medium | Use GHCR with a PAT or ACR with managed identity |
| R-C-1 | Self-Hosted Runner | Runner OOM kills ops-console app | High | No good mitigation — the VM is already at capacity |
| R-C-2 | Self-Hosted Runner | Runner agent has full repo access on production VM | High | Requires careful isolation; not recommended for production |

## Recommendation
**Approach A: GitHub Actions + SCP Deploy** is the clear choice. It directly solves the problem that caused two production outages (building on the VM), requires the least implementation effort (3 files), and matches the existing architecture. The key risks (SSH access, NSG rules) are well-understood DevOps patterns with standard mitigations.

Approach B (Docker Rebuild) is the better long-term architecture, but the seed explicitly scopes out staging environments and multi-node — the effort overhead isn't justified now. It should be revisited when a staging environment story is planned.

Approach C (Self-Hosted Runner) is rejected — it trades SSH complexity for resource contention on an already-constrained VM, which is the exact problem this story exists to solve.
