# Analysis Report

## Summary
| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | Approach B — DB-Mediated Q&A |
| Confidence | High |
| Divergent assessments | 1 |

## Context Weights
| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 25% | Medium-scope feature must align with existing HTTP-based architecture; no new infra patterns should be introduced lightly |
| Future Flexibility | 10% | Single Q&A cycle is explicitly scoped; multi-turn is out of scope (seed.md) — flexibility matters less |
| Business Value | 25% | Core goal is eliminating SSH-based operator friction; speed to unblock agents is the primary business driver |
| Implementation Effort | 25% | Medium story with clear scope; team velocity matters — prefer approaches that reuse existing patterns |
| Risk Profile | 15% | Production dispatch system; state corruption or silent failures would block agents longer than the status quo |

**Rationale:** Equal weight on Soundness, Value, and Effort because this is an operator-facing feature on a production dispatch system with a clear problem statement. Risk slightly elevated because dispatch state transitions affect agent uptime.

## Evaluation Agents
| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness + Flexibility | 3 |
| Business | Value + Effort | 3 |
| Risk | Risk Profile + Register | 3 |

## Approaches Evaluated

### Approach A: SSH-Based File I/O (Seed Proposal)
Ops-console SSHs into agent VMs to read `QUESTION.md` and write `ANSWER.md`. New SSH client infrastructure added to ops-console. Routes follow seed.md design exactly.

### Approach B: DB-Mediated Q&A (Extend Dispatch Schema)
Agent sends `question_text` in the existing `POST /needs_info` body. Question and answer stored as columns on `dispatch_items`. `GET /question` reads from DB. `POST /answer` writes to DB + resumes. Agent reads answer from claim response or new endpoint. Zero SSH, zero file I/O from ops-console.

### Approach C: Hybrid — Question Text in DB, Answer Written via SSH
Agent sends `question_text` in `POST /needs_info` body (DB read for question — no SSH on read path). `POST /answer` writes `ANSWER.md` via SSH to agent VM + resumes. SSH only needed for the write path.

## Scoring Matrix
| Approach | Technical (25%) | Flexibility (10%) | Value (25%) | Effort (25%) | Risk (15%) | Weighted |
|----------|-----------|------------|-------|--------|------|----------|
| A: SSH File I/O | 2/5 | 3/5 | 4/5 | 2/5 | 2/5 | **2.60** |
| B: DB-Mediated | 5/5 | 4/5 | 5/5 | 5/5 | 5/5 | **4.90** |
| C: Hybrid | 3/5 | 3/5 | 4/5 | 3/5 | 3/5 | **3.25** |

### Scoring Detail

#### Technical Soundness
| Approach | Score | Rationale |
|----------|-------|-----------|
| A | 2 | Introduces SSH infrastructure that doesn't exist in the codebase. Every other agent↔ops-console interaction is HTTP. SSH key management, connection pooling, error handling, and firewall rules are all net-new concerns. Violates the established architectural pattern. |
| B | 5 | Extends the existing dispatch DB schema and HTTP API pattern. `question_text`/`answer_text` columns are natural extensions of `needs_info_path`. Reads/writes go through asyncpg — the same path every other dispatch operation uses. No new infrastructure. |
| C | 3 | Question read via DB is sound, but writing ANSWER.md via SSH still requires the same SSH infra as Approach A for the write path. Half the architectural debt for half the operations. |

#### Future Flexibility
| Approach | Score | Rationale |
|----------|-------|-----------|
| A | 3 | SSH access enables arbitrary file operations (future multi-file Q&A, log retrieval). But this flexibility comes at high maintenance cost and is out of scope. |
| B | 4 | DB storage enables future features: Q&A history, search, multi-turn threading (add a jsonb array), analytics on question patterns. API-first approach composes well. |
| C | 3 | Mixed transport complicates future extension — some data in DB, some on filesystem. Inconsistent retrieval paths. |

#### Business Value
| Approach | Score | Rationale |
|----------|-------|-----------|
| A | 4 | Fully solves the operator problem — question visible in modal, answer submitted from UI. SSH degraded fallback adds UX complexity but is a net positive for reliability. |
| B | 5 | Same operator UX improvement, but more reliable — DB reads don't fail due to SSH timeouts or VM network issues. No degraded state needed for the happy path; question text is always available if the POST /needs_info succeeded. |
| C | 4 | Question read is reliable (DB), but answer write can still fail (SSH). Operator may submit answer that doesn't reach the VM — confusing UX. |

#### Implementation Effort
| Approach | Score | Rationale |
|----------|-------|-----------|
| A | 2 | Requires: new SSH client module, key provisioning, connection management, firewall rules, error handling for SSH-specific failures, mock SSH in tests. Estimated 3-4x the effort of Approach B. |
| B | 5 | Requires: 1 migration (2 nullable text columns), extend existing `/needs_info` request model, 2 new routes that follow existing patterns exactly, extend claim response to include `answer_text`. All within existing asyncpg/FastAPI patterns. Minimal new test infrastructure. |
| C | 3 | DB side is easy (same as B for reads). SSH write side carries same effort as Approach A for write path. Net ~60% of Approach A effort. |

#### Risk Profile
| Approach | Score | Rationale |
|----------|-------|-----------|
| A | 2 | SSH introduces: network partition risk (VM unreachable), credential rotation risk, firewall/security surface, race conditions (file write vs agent read timing). The degraded fallback in the seed acknowledges SSH is unreliable. |
| B | 5 | All operations go through the same DB that already handles dispatch state. Transactional consistency with the status transition. No new failure modes beyond what dispatch already handles. |
| C | 3 | Read path is safe (DB). Write path carries all SSH risks from Approach A. Partial failure scenario: answer saved in DB but SSH write fails — state is inconsistent (dispatch shows "claimed" but no ANSWER.md on disk). |

## Divergent Assessments
- **Approach A — Business vs Technical:** Business sees high value (4/5) because it solves the operator problem completely as designed in the seed. Technical sees significant concerns (2/5) because SSH infrastructure contradicts the established HTTP-only architecture. **Resolution:** The business value is achievable without SSH (Approach B delivers the same UX at 5/5 value), so the technical concern dominates — there's no reason to accept the architectural debt.

## Top 3 Ranking

### 1. Approach B — DB-Mediated Q&A (Weighted: 4.90)
- **Why #1:** Highest score across every dimension. Extends the existing dispatch pattern rather than introducing new infrastructure. Eliminates the SSH failure mode entirely — question text is always available once the agent POSTs it.
- **Key strength:** Perfect alignment with existing architecture (Technical: 5/5). Zero new infrastructure, zero new failure modes.
- **Key risk:** Requires agent-side change to POST question_text in `/needs_info` body (minor — agent already calls this endpoint).
- **Trade-off:** Question text is limited to what the agent sends at POST time — can't re-read a modified QUESTION.md from disk. Acceptable because agents don't modify questions after posting.

### 2. Approach C — Hybrid (Weighted: 3.25)
- **Why #2:** Pragmatic middle ground — eliminates SSH for the read path (most common operation) while preserving filesystem write for answer delivery. But the SSH write path still carries infrastructure and reliability costs.
- **Key strength:** Question display is always reliable (DB-backed).
- **Key risk:** Answer write via SSH can fail silently — operator sees "submitted" but ANSWER.md never reaches the VM.
- **Trade-off:** Still requires SSH infrastructure for writes. Half the architectural debt of Approach A with diminishing returns.

### 3. Approach A — SSH-Based File I/O (Weighted: 2.60)
- **Why #3:** Faithfully implements the seed design, but the seed assumed SSH infrastructure existed. Codebase exploration reveals it does not — every agent↔ops-console interaction is HTTP. Building SSH infra for one feature is disproportionate.
- **Key strength:** Direct filesystem access; no agent-side changes needed.
- **Key risk:** SSH infrastructure is net-new, adding operational burden (key rotation, firewall rules, connection pooling) for a single feature.
- **Trade-off:** Maximum flexibility (arbitrary file access) at maximum cost (new infra + new failure modes).

## Risk Register Highlights (Top 3 approaches)
| ID | Approach | Risk | Severity | Mitigation |
|----|----------|------|----------|-----------|
| R-B-1 | B: DB-Mediated | Agent must be updated to POST question_text in /needs_info body | Low | Backward-compatible: make question_text optional, fall back to "Question posted — see needs_info_path" placeholder if absent. Agent change is a one-line addition to the existing POST call. |
| R-B-2 | B: DB-Mediated | Large question text (>64KB) bloats dispatch_items table | Low | Add TEXT column with application-level 64KB cap on read/write. PostgreSQL TEXT type handles this natively. |
| R-B-3 | B: DB-Mediated | Answer text stored in DB but agent expects ANSWER.md on filesystem | Medium | Agent-side change: on claim, if `answer_text` is present in claim response, write it to local filesystem as ANSWER.md. Alternatively, agent reads answer from a new `GET /dispatch/{story_id}/answer` endpoint. Either approach is a small agent change. |
| R-C-1 | C: Hybrid | SSH write fails after DB status transition — inconsistent state | High | Wrap SSH write + DB resume in a transaction-like pattern: write file first, resume only on success. But SSH is not transactional — partial failure still possible. |
| R-C-2 | C: Hybrid | SSH infrastructure needed for write path | Medium | Same infra cost as Approach A for the write side. Key management, firewall rules, connection handling all required. |
| R-A-1 | A: SSH File I/O | SSH infrastructure is net-new to ops-console | High | Would need: SSH client library (asyncssh/paramiko), key provisioning, connection pooling, firewall rules. Significant ops overhead for one feature. |
| R-A-2 | A: SSH File I/O | VM unreachable during question fetch → degraded UX | Medium | Seed already designs for degraded fallback, but degraded state is a poor operator experience and defeats the purpose of the feature. |

## Recommendation

**Select Approach B: DB-Mediated Q&A.**

This approach scores highest across all dimensions (4.90/5.00 weighted) with high confidence. The critical insight from codebase exploration is that ops-console has zero SSH infrastructure — every agent interaction is HTTP/DB-mediated. Introducing SSH for this one feature would be architecturally inconsistent and operationally expensive.

Approach B requires two small agent-side changes: (1) POST question_text alongside question_file_path in `/needs_info`, and (2) read answer_text from claim response or a new endpoint. Both are minor additions to existing agent code paths.

The seed.md's SSH-based design was reasonable in the abstract but doesn't match the actual codebase architecture. Approach B delivers identical operator UX (modal with question + answer textarea) with higher reliability (no SSH failure modes), lower effort (no new infrastructure), and full consistency with existing patterns.

**What's sacrificed:** Direct filesystem access to QUESTION.md. If the agent modifies the file after posting (it doesn't), or if we later need to read other files from the VM, we'd need SSH infrastructure anyway. This is explicitly out of scope and can be revisited in a future story if needed.

**Key implementation notes for Phase 6:**
1. Migration: add `question_text TEXT` and `answer_text TEXT` columns to `dispatch_items`
2. Extend `NeedsInfoRequest` model to accept optional `question_text`
3. New `GET /dispatch/{story_id}/question` reads from DB column
4. New `POST /dispatch/{story_id}/answer` writes to DB column + calls existing resume logic
5. Agent change: include question file contents in POST /needs_info body
6. Agent change: read answer_text from claim response, write to local ANSWER.md
