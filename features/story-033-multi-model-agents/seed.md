# STORY-033: Multi-Model Dev Agents — Seed

> **Phase:** 1 (Concept & Seed)
> **Scope:** Medium
> **Date:** 2026-04-13
> **Author:** Opus (Phase 1 agent)

---

## 1. Problem Statement

The Hermes agent fleet currently runs exclusively on Claude Code via `claude_sdk_tool.py` (Anthropic's Claude Agent SDK). This creates:

- **Vendor lock-in** — If Anthropic has outages, rate limits, or price changes, all agents stop.
- **No cost benchmarking** — We cannot compare quality-per-dollar across providers.
- **Missing capability coverage** — Some tasks (e.g., large-context refactoring with 1M token windows) may be better served by other models.
- **No fallback** — A single provider failure means zero agent throughput.

## 2. Goal

Enable Hermes to spawn dev agents using **multiple model providers** (OpenAI Codex, Google Gemini, and optionally open-source tools like Aider) so we can compare quality, cost, and capability — and eventually route tasks to the best model for the job.

---

## 3. Research Findings

### 3.1 OpenAI Codex CLI

| Attribute | Details |
|-----------|---------|
| **Product** | [Codex CLI](https://github.com/openai/codex) — open-source, Rust-based terminal coding agent |
| **Status** | GA. Active development. Not the deprecated 2023 Codex model — this is a new product. |
| **Headless Mode** | `codex exec "task"` — non-interactive, streams events to stderr, final result to stdout. Supports `--json` for JSONL event stream, `--ephemeral` for stateless runs, `-o <path>` for file output. |
| **Models** | `gpt-5.3-codex` (optimized for coding), `gpt-5.4` (general), `gpt-5.1-codex-mini` (cheap/fast), `o3`, `o4-mini` |
| **Auth** | `CODEX_API_KEY` env var (recommended for CI/headless). Also supports ChatGPT sign-in for interactive use. |
| **Tool Use** | Built-in: `ApplyPatchTool` (file editing via diffs), `ShellTool` (bash), `ComputerTool`. Extensible via MCP servers. |
| **Permission/Approval** | Three sandbox modes: `read-only` (default), `workspace-write` (`--full-auto`), `danger-full-access`. Approval policy: `never`, `on-request`, `untrusted`. Maps well to our `can_use_tool` callback. |
| **Session Resume** | Yes — `codex exec resume --last "next instruction"` or `codex exec resume <SESSION_ID>`. Thread IDs maintained via `structuredContent.threadId`. |
| **Streaming** | JSONL event stream via `--json` flag. Events: `thread.started`, `turn.completed`, `item.completed`, `error`. |
| **Cost Reporting** | Not built into CLI output. Must track via API usage dashboard or parse JSONL events. |
| **Agents SDK** | [openai-agents-python](https://github.com/openai/openai-agents-python) — Python SDK. Codex runs as MCP server (`codex mcp-server`), exposing `codex()` and `codex-reply()` tools. Supports multi-agent orchestration. |
| **Pricing** | `gpt-5.3-codex`: not published per-token separately. `codex-mini-latest`: $1.50 input / $6.00 output per MTok. `gpt-5`: $0.63 input / $5.00 output per MTok. `o3`: $10.00 input / $40.00 output per MTok (reasoning tokens extra). `o4-mini`: $1.10 input / $4.40 output per MTok. ChatGPT Pro ($200/mo) includes 300-1500 messages/5hr. |

**Sources:**
- [OpenAI Codex CLI GitHub](https://github.com/openai/codex)
- [Codex Non-Interactive Mode](https://developers.openai.com/codex/noninteractive)
- [Codex + Agents SDK Guide](https://developers.openai.com/codex/guides/agents-sdk)
- [Codex Pricing](https://developers.openai.com/codex/pricing)
- [Codex Models](https://developers.openai.com/codex/models)

### 3.2 Google Gemini CLI

| Attribute | Details |
|-----------|---------|
| **Product** | [Gemini CLI](https://github.com/google-gemini/gemini-cli) — open-source (Apache 2.0), Node.js-based terminal agent |
| **Status** | GA. v0.34.0+ (March 2026). Active development. |
| **Headless Mode** | `-p` / `--prompt` flag or non-TTY detection. Also `--non-interactive` to prevent prompts, `--yolo` for auto-approve, `--output-format json` for structured output. SDK available via `@google/gemini-cli-sdk` (Node.js). |
| **Models** | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash`. 1M token context window on Pro. |
| **Auth** | `GEMINI_API_KEY` env var (simplest for headless). Also: `GOOGLE_API_KEY`, ADC via service account (`GOOGLE_APPLICATION_CREDENTIALS`), Google OAuth. Vertex AI supported with `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`. |
| **Tool Use** | Built-in: file read/write/edit/glob, `run_shell_command` (with PTY), `web_fetch`, `google_web_search`. Extensible via MCP. Per-agent `ToolRegistry` for isolation. |
| **Permission/Approval** | `MessageBus` emits `TOOL_CONFIRMATION_REQUEST` events. Developers subscribe and implement custom approval logic. Plan Mode (default in v0.34.0+) proposes changes before executing. |
| **Session Resume** | `--resume` / `-r` flag. Named checkpoints via `/resume save <name>` and `/resume resume <name>`. `/restore` reverts file changes to a checkpoint's Git snapshot. Auto-saving on by default since v0.20.0+. |
| **Streaming** | `GeminiClient` streams turn-by-turn via `Turn.run()`, yielding content chunks and tool call events. |
| **Cost Reporting** | `GeminiChat` tracks token counts. No built-in cost-in-dollars reporting. |
| **SDK** | CLI SDK: `@google/gemini-cli-sdk` (Node.js). **ADK (Agent Development Kit):** [adk.dev](https://adk.dev/) — Python, TypeScript, Go, Java (Python SDK downloaded 7M+ times). Model-agnostic, supports multi-agent orchestration, MCP tools, session management. This resolves the "Node.js only" concern — **ADK Python is the preferred integration path**. |
| **Pricing** | `gemini-2.5-pro`: $1.25 input / $10.00 output per MTok (under 200K context). $2.50/$15.00 above 200K. `gemini-2.5-flash`: $0.30 / $2.50. `gemini-3-flash-preview`: $0.50 / $3.00. `gemini-3.1-pro-preview`: $2.00 / $12.00. 50% batch discount. Cache reads at ~10% of base price. **Free tier:** 1,000 req/day with Gemini 2.5 Pro via personal Google account. |

**Sources:**
- [Gemini CLI GitHub](https://github.com/google-gemini/gemini-cli)
- [Gemini CLI Documentation](https://geminicli.com/docs/)
- [Gemini CLI Headless Mode](https://geminicli.com/docs/cli/headless/)
- [Gemini CLI Session Management](https://geminicli.com/docs/cli/session-management/)
- [ADK (Agent Development Kit)](https://adk.dev/) — [GitHub](https://github.com/google/adk-python)
- [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [Gemini CLI Authentication](https://geminicli.com/docs/get-started/authentication/)

### 3.3 Other Contenders

| Tool | Type | Headless? | Key Facts | Verdict |
|------|------|-----------|-----------|---------|
| **Cursor CLI** | Commercial IDE + CLI | Yes — `-p`/`--print` flag, `--output-format json/stream-json/text`. Auth via `CURSOR_API_KEY`. | GA Feb 2026. Known issues: MCP trust requires interactive UI first (breaks headless). Proprietary, $20-40/mo subscription required. | **Not recommended** — MCP trust bug blocks headless automation; commercial license incompatible with our VM provisioning model. |
| **Aider** | Open-source CLI (Python) | Yes — non-interactive via pipes, `--yes` flag for auto-confirm. | 39K GitHub stars, 4.1M installs. Model-agnostic (works with Claude, GPT, Gemini, DeepSeek, Grok, local Ollama). Subagents, HTTP hooks (Feb 2026), auto-commits. Apache 2.0 license. | **Strong contender** as a model-agnostic fallback. Wraps any LLM API so we could use it with cheaper models. Not a first-class SDK though — it's a CLI tool, not an embeddable agent framework. |
| **Continue.dev** | Open-source CLI (Node.js) | Yes — `cn -p` for headless, `CONTINUE_API_KEY` for auth. | Pivoted from VS Code extension to CI/CD-focused CLI agent. Primarily for PR review and code quality, not general agent coding tasks. | **Not applicable** — focused on review/linting, not full SDLC agent work. |
| **GitHub Copilot CLI** | Commercial (GitHub subscription) | Yes — GA Feb 2026. Interactive + headless + autopilot modes. Multi-model (Claude, GPT, Gemini). Background cloud agents via `&` prefix. | Requires GitHub Copilot subscription. Multi-model support built in. Specialized sub-agents (Explore, etc.). | **Interesting but wrong abstraction** — it's a developer tool, not an embeddable agent SDK. Cannot control model routing, tool approval, or session management at the level we need. |

**Sources:**
- [Cursor CLI Headless Docs](https://cursor.com/docs/cli/headless)
- [Aider](https://aider.chat/)
- [Continue.dev CLI](https://docs.continue.dev/guides/cli)
- [GitHub Copilot CLI GA](https://github.blog/changelog/2026-02-25-github-copilot-cli-is-now-generally-available/)

### 3.4 Cost Comparison Matrix

| Provider / Model | Input (per MTok) | Output (per MTok) | Context Window | Best For |
|-----------------|------------------|--------------------|----------------|----------|
| **Claude Sonnet 4.6** | $3.00 | $15.00 | 1M | Current default — strong coding, reliable |
| **Claude Haiku 4.5** | $1.00 | $5.00 | 200K | Fast delegation tasks |
| **GPT-5** | $0.63 | $5.00 | 200K | General purpose, excellent value |
| **GPT-5.3-Codex** | ~$1.50* | ~$6.00* | 200K | Coding-optimized |
| **Codex Mini** | $1.50 | $6.00 | 200K | Cheap/fast coding tasks |
| **o4-mini** | $1.10 | $4.40 | 200K | Reasoning model, cheap |
| **Gemini 2.5 Pro** | $1.25 | $10.00 | 1M | Large context, competitive pricing |
| **Gemini 2.5 Flash** | $0.30 | $2.50 | 1M | Ultra-cheap, fast tasks |
| **Aider + DeepSeek V3** | ~$0.27 | ~$1.10 | 128K | Cheapest viable option |

*GPT-5.3-Codex per-token pricing estimated from codex-mini-latest rates; exact pricing via Codex product may differ from raw API.

**Key insight:** GPT-5 at $0.63/$5.00 is **~5x cheaper on input** and **~3x cheaper on output** than Claude Sonnet 4.6. Gemini 2.5 Flash at $0.30/$2.50 is **~10x cheaper on input** and **~6x cheaper on output** than Sonnet. Even Gemini 2.5 Pro matches GPT-5 pricing. For Phase 2-5 research work (which doesn't require peak coding quality), cheaper models could save significant cost.

---

## 4. Architecture Compatibility Analysis

### 4.1 Current Architecture

```
Hermes Gateway → dispatches prompt → claude_sdk_tool.py → Claude Agent SDK → streams output
                                         ↓
                                    can_use_tool callback (approval)
                                    session resume via --resume
                                    work queue integration
                                    structured logging to stdout/Grafana
```

### 4.2 Integration Feasibility per Provider

| Capability | Claude (current) | OpenAI Codex CLI | Gemini CLI | Aider |
|------------|-----------------|------------------|------------|-------|
| **Headless invocation** | `claude_agent_sdk.query()` Python async | `codex exec "task"` subprocess OR `openai-agents-python` SDK | `gemini -p "task"` subprocess OR ADK Python SDK (`google-adk`) | `aider --yes --message "task"` subprocess |
| **Replace claude_sdk_tool.py?** | N/A (current) | Yes — `codex exec` with `--json` matches our streaming pattern | Yes — ADK Python SDK is native to our stack; CLI subprocess also viable | Yes — simple subprocess wrapper |
| **Permission/approval callbacks** | `can_use_tool` async callback | Sandbox modes + approval policy (coarser than our callback) | `TOOL_CONFIRMATION_REQUEST` on MessageBus (Node.js SDK) | `--yes` auto-approves all (no granular control) |
| **Session resume** | `opts.resume = session_id` | `codex exec resume <SESSION_ID>` | `GeminiCliSession` checkpointing (SDK) | No built-in session resume |
| **Streaming output** | Async iterator over messages | `--json` JSONL to stdout | Turn-by-turn chunks (SDK) or `-p` text output | Stdout text stream |
| **Token/cost reporting** | `msg.total_cost_usd` on result event | Not in CLI output; must use API dashboard | Token counts via `GeminiChat`; no USD reporting | Token counts logged, no cost |
| **Language** | Python (native) | Rust CLI + Python Agents SDK | Node.js CLI + **Python ADK SDK** | Python (native) |

### 4.3 Recommended Architecture

**Option A: Unified `agent_sdk_tool.py` with `--provider` flag** (Recommended)

```
agent_sdk_tool.py --provider claude  -p "task" -w /path  → Claude Agent SDK (current behavior)
agent_sdk_tool.py --provider codex   -p "task" -w /path  → codex exec subprocess
agent_sdk_tool.py --provider gemini  -p "task" -w /path  → gemini -p subprocess
agent_sdk_tool.py --provider aider   -p "task" -w /path  → aider --yes subprocess
```

Why unified:
- Single entry point for Hermes gateway — just add `--provider` to dispatch
- Shared logging, work queue integration, story tracking
- Shared approval logic (adapted per provider's capabilities)
- Cost tracking normalized across providers
- Easy to add new providers later

**Internal structure:**

```python
# agent_sdk_tool.py (simplified)
class AgentProvider(ABC):
    async def run(self, prompt, workdir, max_turns, resume_id) -> AsyncIterator[AgentEvent]
    async def can_use_tool(self, tool_name, tool_input) -> bool

class ClaudeProvider(AgentProvider):    # wraps claude_agent_sdk
class CodexProvider(AgentProvider):     # wraps codex exec --json
class GeminiProvider(AgentProvider):    # wraps gemini -p (subprocess)
class AiderProvider(AgentProvider):     # wraps aider --yes --message
```

**Option B: Separate tools per provider** (Not recommended)

- `claude_sdk_tool.py`, `codex_sdk_tool.py`, `gemini_sdk_tool.py`
- Duplicates logging, work queue, approval logic
- Gateway needs to know about each tool separately
- Harder to maintain

---

## 5. Recommended Approach

### Phase 1: Add OpenAI Codex CLI (First)

**Why Codex first:**
1. **Best headless story** — `codex exec` with `--json` is purpose-built for automation, maps directly to our streaming pattern
2. **Python Agents SDK** — matches our stack, can go deeper than subprocess if needed
3. **Session resume** — built-in, matching our current capability
4. **Approval model** — sandbox modes + approval policy are good enough for our use case
5. **Competitive pricing** — GPT-5 at $0.63/$5.00 is ~5x cheaper on input than Sonnet 4.6 at $3.00/$15.00
6. **Strong coding quality** — GPT-5.3-Codex is purpose-built for code

### Phase 2: Add Gemini CLI (Second)

**Why Gemini second:**
1. **Cost advantage** — Gemini 2.5 Flash at $0.30/$2.50 per MTok is transformatively cheap for research phases
2. **1M context window** — matches Claude, useful for large codebase analysis
3. **ADK Python SDK available** — [adk.dev](https://adk.dev/) provides a Python-native agent framework with multi-agent orchestration, MCP tools, and session management. No longer limited to Node.js CLI wrapper.
4. **Good enough for research phases** — Phases 2-5 don't need peak coding quality
5. **Free tier** — 1,000 requests/day with Gemini 2.5 Pro for testing/development at zero cost

### Phase 3 (Optional): Add Aider as Model-Agnostic Fallback

- Use for cheapest-possible tasks via DeepSeek or local models
- Good for simple file edits, formatting, documentation
- No session resume limits its usefulness for complex SDLC work

---

## 6. Required Code Changes

### 6.1 Refactor `claude_sdk_tool.py` → `agent_sdk_tool.py`

| Change | Description | Effort |
|--------|-------------|--------|
| Extract `AgentProvider` ABC | Define common interface: `run()`, `can_use_tool()`, event types | Small |
| Create `ClaudeProvider` | Move existing claude_agent_sdk logic into provider class | Small (refactor only) |
| Create `CodexProvider` | Subprocess wrapper for `codex exec --json`, parse JSONL events | Medium |
| Create `GeminiProvider` | Subprocess wrapper for `gemini -p`, parse text/JSON output | Medium |
| Add `--provider` flag | CLI argument to select provider | Small |
| Normalize event model | `AgentEvent` dataclass: `type`, `text`, `tool_name`, `tool_input`, `cost_usd`, `tokens` | Small |
| Update logging | Prefix with provider name: `[Claude Code]`, `[Codex]`, `[Gemini]` | Small |
| Update work queue integration | Provider-agnostic story tracking | Small |

### 6.2 Gateway / Dispatch Changes

| Change | Description | Effort |
|--------|-------------|--------|
| Config: model routing rules | `config.yaml` section mapping story scope/phase to preferred provider | Small |
| Dispatch: pass `--provider` | Add provider selection to dispatch command | Small |
| Fallback logic | If primary provider fails/times out, retry with fallback provider | Medium |

### 6.3 VM Provisioning Changes

| Change | Description | Effort |
|--------|-------------|--------|
| Install Codex CLI | Add `codex` binary to VM image (Rust, single binary) | Small |
| Install Gemini CLI | Add `gemini` via npm to VM image | Small |
| Provision API keys | `CODEX_API_KEY` and `GEMINI_API_KEY` as VM secrets | Small |
| Test connectivity | Smoke test each provider on VM startup | Small |

---

## 7. Scope Assessment

**Overall: Medium**

Rationale:
- Core change is a well-scoped refactor of one file (`claude_sdk_tool.py`) into a provider pattern
- Each new provider is a subprocess wrapper (~100-150 lines each)
- No database changes, no new APIs, no UI changes
- Risk is moderate: each provider has different event formats, error modes, and capability gaps
- Testing needs provider-specific mocking

**Phase path:** 1 → 4 → 6 → 7 → 8 → 8b → 11 → Done

**Estimated effort:**
- Design (Phase 6): 1 session — feature spec for provider abstraction
- Tests (Phase 7): 1 session — mock-based tests for each provider
- Implementation (Phase 8): 2-3 sessions — refactor + Codex provider + Gemini provider
- Total: ~4-5 sessions

---

## 8. Open Questions for Mark

1. **Provider priority:** Confirm Codex first, Gemini second — or do we want Gemini first for cost savings?

2. **Cost routing:** Should we auto-route cheap phases (2-5 research) to Gemini Flash and reserve Claude for coding phases (7-8)? Or let the user choose per-story?

3. **API key provisioning:** Do we want separate OpenAI and Google Cloud accounts, or use existing organizational accounts? Any budget constraints?

4. **Quality gate:** Should we run a benchmark (e.g., same task on Claude vs Codex vs Gemini) before committing to multi-model? Or trust the public benchmarks?

5. **Aider inclusion:** Is the open-source/local model option worth pursuing now, or defer to a future story?

6. **VM image:** Should each agent VM have all providers installed (fat image), or provision model-specific VMs (lean but more images to maintain)?

7. **Fallback behavior:** When a provider fails mid-session, should we retry the entire task with a different provider, or surface the error for human decision?

8. **Session resume across providers:** If a Claude session fails, can we resume with Codex? (Answer is almost certainly no — sessions are provider-specific. But worth confirming the expectation.)

---

## 9. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Provider API changes break wrapper | Agent downtime for that provider | Abstract behind provider interface; pin CLI versions in VM image |
| Quality regression with cheaper models | Bad code, failed tests | Run quality benchmarks before enabling for production phases (7-8) |
| Different tool/permission models | Security gaps or over-restrictive behavior | Map each provider's permission model to our approval policy; default to most restrictive |
| ADK Python SDK maturity | API may change as ADK evolves | Pin ADK version in requirements; fall back to CLI subprocess if SDK breaks |
| Cost tracking inconsistency | Can't compare provider costs accurately | Normalize all costs to USD-per-MTok in our logging; parse from each provider's output format |

---

## 10. Dependencies

- **STORY-003 (Claude Code Runner):** Existing `claude_runner.py` module — will be superseded by the provider abstraction but tests remain relevant.
- **STORY-025 (Queue Visibility):** Work queue integration must remain provider-agnostic (already is — just calls `set_active`/`complete`).
- **STORY-027 (Dispatch Queue Auto-Pickup):** Dispatch logic needs `--provider` flag support.
- **VM provisioning scripts:** Must be updated to install additional CLI tools.

---

## 11. Non-Goals (Explicit Exclusions)

- **Model fine-tuning** — We use models as-is from each provider.
- **Multi-model within a single session** — Each session uses one provider. No mid-session switching.
- **Local model hosting** — Aider + Ollama is interesting but out of scope for this story.
- **Replacing Claude as default** — Claude remains primary; others are alternatives for comparison and fallback.

---

## 12. Success Criteria

1. `agent_sdk_tool.py --provider codex -p "create hello.py" -w /tmp/test` successfully runs a Codex session and streams output
2. `agent_sdk_tool.py --provider gemini -p "create hello.py" -w /tmp/test` successfully runs a Gemini session
3. Cost per session is logged in normalized format for all providers
4. Existing Claude workflows (`--provider claude` or default) are unchanged
5. Hermes dispatch can route to a specific provider via configuration
