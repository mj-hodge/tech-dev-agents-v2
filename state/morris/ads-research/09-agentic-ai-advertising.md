# 09 — Agentic AI in Advertising Systems

> Research brief for Gorilla Commerce engineering team
> Last updated: 2026-04-23

---

## Executive Summary

Agentic AI is rapidly transforming digital advertising from rule-based automation to autonomous, goal-directed campaign management. In 2025-2026, every major ad platform — Amazon, Google, Meta, Spotify — shipped agentic features that can plan campaigns, generate creative, adjust bids, and allocate budgets with minimal human input. eMarketer projects autonomous AI will manage **78% of all programmatic ad spend (~$567 billion)** by end of 2026 (eMarketer, 2026).

However, the technology carries significant risk. Documented budget disasters range from $23K to $52K in individual incidents, with recovery timelines of 6-12 weeks. Multi-agent architectures — where specialized bidding, budget, creative, and audience agents collaborate — are emerging as the dominant pattern. Spotify's multi-agent ad planner reduced campaign creation from 15-30 minutes to 5-10 seconds (Spotify Engineering, 2026).

For Gorilla Commerce, the opportunity is to build a **bounded-autonomy architecture** that layers agentic capabilities on Amazon's Ads Agent and Creative Agent APIs while maintaining hard guardrails on spend, a human-on-the-loop governance model, and graduated trust calibration. This document provides the architectural patterns, failure modes, and implementation recommendations to do so.

---

## 1. Agentic AI Architectures for Ad Optimization

### 1.1 The OODA Loop Applied to Advertising

The OODA loop (Observe-Orient-Decide-Act) has become the reference framework for agentic AI systems (Sogeti Labs, 2025; NVIDIA, 2025). Applied to advertising:

| OODA Phase | Advertising Application | Key Signals |
|---|---|---|
| **Observe** | Ingest real-time performance data (CTR, ACOS, impressions, conversion rates) | Amazon Ads API metrics, DSP signals, AMC queries |
| **Orient** | Contextualize against goals, seasonality, competitor activity, inventory levels | Historical campaign data, product catalog, margin data |
| **Decide** | Select optimization action (bid adjustment, budget reallocation, pause/enable) | Policy engine + ML prediction models |
| **Act** | Execute the change via API and log the decision | Amazon Ads API mutations, audit trail |

However, Schneier & Raghavan (IEEE S&P, 2025) warn of the "OODA Loop Problem" — AI must compress reality into model-legible forms, creating blind spots. For ad systems, this means agents may miss context that experienced human advertisers would catch (brand crises, competitor pricing shifts, supply chain disruptions).

### 1.2 Reward Signals

Effective agentic ad systems require carefully designed reward signals:

- **Primary reward**: Target ACOS / ROAS at the campaign or portfolio level
- **Secondary rewards**: Impression share, new-to-brand percentage, organic rank lift
- **Constraint signals**: Maximum daily spend, minimum ROAS floor, brand safety score
- **Temporal discounting**: Short-term conversion value vs. long-term customer LTV

The danger of naive reward signals is well-documented: Google's Performance Max has been observed to optimize for spurious "conversions" (free downloads, bot clicks) when tracking is misconfigured, leading to Attribution Death Spirals (GROAS.ai, 2025).

### 1.3 Guardrail Architecture

Modern agentic systems employ layered guardrails (Authority Partners, 2026; Prompt Engineering, 2026):

| Layer | Mechanism | Example |
|---|---|---|
| **Hard limits** | Non-overridable constraints | Max daily spend cap, max bid ceiling |
| **Soft limits** | Alerts + human approval required | Spend >120% of daily target, ACOS >150% of target |
| **Policy engine** | Deterministic rules checked before every action | No bid increases during low-inventory periods |
| **Statistical anomaly detection** | Detect unusual patterns within 15 minutes | Sudden CTR spike (possible click fraud) |
| **Circuit breakers** | Automatic pause on threshold breach | Campaign paused if spend rate >3x normal |

---

## 2. Real-World Case Studies

### 2.1 Amazon Ads Agent & Creative Agent (as of 2025-11)

Amazon's unBoxed 2025 conference revealed two production agentic systems (Amazon Ads, 2025; Ad Advance, 2025):

- **Ads Agent** (Open Beta, US): Analyzes advertiser objectives and recommends audiences/keywords using Amazon's shopping, streaming, and browsing signals. Allows refinement before application. Particularly effective for new product launches. Broader marketplace rollout expected 2026.
- **Creative Agent** (Open Beta since 2025-11-11): Generates image, video, and audio creative using first-party retail signals (product detail pages, A+ content, customer reviews, Brand Store data). Compatible with Sponsored Display, DSP, and Streaming TV.
- **AMC Skills**: Converts natural-language questions into SQL queries within Amazon Marketing Cloud, reducing technical barriers.
- **Full-Funnel Campaigns**: AI-driven spend allocation across awareness, consideration, and conversion stages.

**Result**: 67% faster campaign launches; significant reduction in daily management time (Amazon Ads, 2025).

### 2.2 Spotify Multi-Agent Ad Planner (as of 2026-02)

Spotify Engineering published their multi-agent architecture (Spotify Engineering, 2026):

| Agent | Role |
|---|---|
| **RouterAgent** | Fast routing to prevent unnecessary LLM calls |
| **GoalResolverAgent** | Maps intent to campaign objectives (REACH, CLICKS, APP_INSTALLS) |
| **AudienceResolverAgent** | Extracts targeting criteria (interests, geography, age, gender) |
| **BudgetAgent** | Parses budget formats, converts to micro-units |
| **ScheduleAgent** | Handles date parsing including relative dates |
| **MediaPlannerAgent** | Generates ad set recommendations from historical data |

**Tech stack**: Google ADK 0.2.0 + Vertex AI (Gemini 2.5 Pro) + gRPC API layer.
**Result**: Campaign creation dropped from 15-30 minutes / 20+ form fields → 5-10 seconds / 1-3 messages.

### 2.3 NBCUniversal / FreeWheel Agent-Led Deal (as of 2026-Q1)

First AI-agent-led programmatic guaranteed deal: buyer and seller agents automated operations for NFL playoff campaign (INMA, 2026). Demonstrated agent-to-agent negotiation in live media buying.

### 2.4 Google Marketing Advisor (as of 2025-05)

Chrome-based AI agent managing campaigns across platforms. Offers personalized keyword suggestions, creative ideas, bid adjustments, and can implement changes on advertiser's behalf (Google, 2025).

### 2.5 Budget Disaster Case Studies

| Incident | Loss | Duration | Root Cause | Recovery |
|---|---|---|---|---|
| TechFlow Solutions (B2B SaaS) | $47,283 | 62 hours (weekend) | Performance Max attribution death spiral; 847 fake "conversions" | 7 weeks; $31,400 partial refund |
| Elite Fitness Equipment | $34,900 | 18 hours | Auto-enrolled in "enhanced audience expansion"; showed $3K products to budget shoppers | 3 weeks; 42% refund ($14,658) |
| Legal Marketing Pro (Agency) | $28,400 | 6 days across 12 clients | Target CPA misinterpreted engagement; bid on "free legal advice" queries | 8 weeks; Google refused refund |
| Artisan Home Décor | $52,700 | 5 days (Black Friday) | Smart Shopping bid on bargain searches instead of premium market | Paused after $1,200 revenue from $17K spend |
| CloudSync Analytics (Startup) | $23,800 (95% of budget) | 3 days | Performance Max ROAS targeting irrelevant audiences; 1,200+ fake conversions | 6 weeks; only 15% refund |

(Source: GROAS.ai, 2025)

---

## 3. Failure Modes and Safety Guardrails

### 3.1 Taxonomy of Failure Modes

Based on documented incidents (GROAS.ai, 2025; DesignRush, 2025; NewscastStudio, 2026):

| Failure Mode | Description | Frequency |
|---|---|---|
| **Attribution Death Spiral** | Conversion tracking errors cause AI to claim 200-400% above-normal conversions, reinforcing bad bids | Very common |
| **Audience Explosion** | Algorithm progressively broadens to irrelevant audiences to meet spend targets | Common |
| **Keyword Inflation Bubble** | Aggressive bidding on increasingly irrelevant search terms | Common |
| **Performance Max Black Hole** | Complete loss of targeting focus in black-box campaigns | Common |
| **Learning Phase Lock** | Permanent "learning" status with continuous maximum spending | Moderate |
| **Weekend/Holiday Drift** | Compounding errors during periods without human oversight | Common |
| **Inter-Agent Conflict** | Multiple agents optimizing competing objectives simultaneously | Emerging |
| **Reward Hacking** | Agent optimizes proxy metric that diverges from business value | Moderate |

These five modes account for **94% of documented AI ad spend disasters** (GROAS.ai, 2025).

### 3.2 Recommended Guardrail Stack

**Technical guardrails** (Authority Partners, 2026; Basis, 2025):
- Budget constraints with hard ceilings that AI cannot override
- Frequency caps preventing ad fatigue
- Brand safety filters blocking inappropriate placements
- Optimization thresholds requiring human approval above defined limits
- Anomaly detection identifying unusual spending within 15 minutes

**Organizational guardrails**:
- Decision logging and audit trails for every autonomous action
- Escalation protocols with defined response times
- Regular model performance reviews
- Kill-switch capability: human operators can override any AI decision immediately

**Regulatory guardrails** (Influencers Time, 2025):
- Transparency requirements for AI-driven ad placement
- Consumer protection compliance (FTC, EU AI Act)
- Privacy compliance (data minimization in targeting)
- Anti-discrimination requirements in audience selection

---

## 4. Multi-Agent Systems in Advertising

### 4.1 Architecture Patterns

Multi-agent advertising systems employ distributed intelligence where specialized agents collaborate through a coordination layer (Glean, 2026; HubSpot, 2025):

```
┌─────────────────────────────────────────────┐
│              Orchestrator Agent              │
│  (goal alignment, conflict resolution,      │
│   resource allocation, audit logging)       │
├──────┬──────┬──────┬──────┬────────────────┤
│Bidding│Budget│Creative│Audience│ Analytics  │
│Agent  │Agent │Agent   │Agent   │ Agent      │
├──────┴──────┴──────┴──────┴────────────────┤
│         Shared State / Message Bus          │
├─────────────────────────────────────────────┤
│      Amazon Ads API / DSP / AMC             │
└─────────────────────────────────────────────┘
```

### 4.2 Agent Roles for Amazon Advertising

| Agent | Responsibility | Amazon API Surface |
|---|---|---|
| **Bidding Agent** | Real-time bid optimization per keyword/target | SP/SB/SD Bid APIs |
| **Budget Agent** | Daily/campaign budget allocation and pacing | Campaign Budget APIs |
| **Creative Agent** | A/B testing, creative rotation, asset generation | Creative Agent API, Creative Asset Library |
| **Audience Agent** | Segment refinement, negative targeting, new audience discovery | AMC, Audience APIs |
| **Analytics Agent** | Trend detection, anomaly alerting, reporting | AMC SQL, Reporting APIs |
| **Inventory Agent** | Pause/reduce spend for low-stock ASINs | SP-API Inventory feeds |

### 4.3 Coordination Challenges

Key challenges in multi-agent ad systems (DEV Community, 2026; Glean, 2026):

- **Conflicting objectives**: Bidding agent wants to raise bids while budget agent wants to constrain spend
- **Resolution pattern**: Orchestrator agent mediates using business-level KPI hierarchy (e.g., ACOS target takes precedence over impression share)
- **State consistency**: All agents must see the same campaign state; eventual consistency can cause conflicting actions
- **Feedback loops**: Agent A's action changes the signal Agent B optimizes on, causing oscillation

### 4.4 Industry Standardization

The IAB Tech Lab released its first framework for agentic ad buying standards (AdExchanger, 2026), working toward:
- Agent-to-agent communication protocols
- Standardized capability discovery
- Audit trail requirements
- Safety certification for autonomous buying agents

---

## 5. Human-in-the-Loop Patterns for High-Stakes Ad Spend

### 5.1 HITL vs. HOTL vs. Full Autonomy

| Pattern | Description | When to Use | Scaling |
|---|---|---|---|
| **Human-in-the-Loop (HITL)** | Human approves every action before execution | High-stakes decisions, early trust phase | Doesn't scale beyond ~50 decisions/day |
| **Human-on-the-Loop (HOTL)** | AI executes autonomously; human monitors dashboards and can intervene | Medium-stakes, established trust | Scales to thousands of daily actions |
| **Full Autonomy** | AI executes with only post-hoc review | Low-stakes, well-understood domains | Unlimited scale |

(Source: Smartly.io, 2025; Agentic Foundry, 2026)

### 5.2 Risk-Confidence Policy Matrix

Not every decision carries the same stakes. Build a two-axis matrix (Parseur, 2026):

| | **Low Business Risk** | **Medium Business Risk** | **High Business Risk** |
|---|---|---|---|
| **High AI Confidence** | Full autonomy | HOTL with alerts | HITL approval required |
| **Medium AI Confidence** | HOTL with logging | HITL approval required | HITL + senior review |
| **Low AI Confidence** | HITL approval | HITL + senior review | Human-only decision |

**Examples for Amazon advertising**:
- **Low risk, high confidence**: Adjusting a bid by ±5% on a stable keyword → Full autonomy
- **Medium risk, medium confidence**: Reallocating $500 between campaigns → HOTL with alert
- **High risk, any confidence**: Launching a new campaign type, spending >$5K/day, Black Friday strategy → HITL required

### 5.3 Escalation Architecture

```
Level 0: Agent executes autonomously (logged)
Level 1: Agent proposes, auto-approves after 30-min timeout
Level 2: Agent proposes, requires explicit human approval
Level 3: Agent flags issue, human must design solution
Level 4: System paused, incident review triggered
```

---

## 6. Trust Calibration — Building Confidence in Autonomous Ad Agents

### 6.1 The Trust Maturity Model

Building trust in autonomous ad agents is a graduated process (Google Cloud, 2025; Beetroot, 2025):

| Phase | Duration | Agent Autonomy | Human Role | Success Criteria |
|---|---|---|---|---|
| **Shadow Mode** | 2-4 weeks | Agent recommends, human executes | Full control | Agent recommendations match or beat human decisions ≥80% of the time |
| **Supervised Autonomy** | 4-8 weeks | Agent executes low-risk actions; proposes high-risk | Approve/reject high-risk | No budget overruns; ACOS within ±10% of target |
| **Bounded Autonomy** | 2-3 months | Agent executes within defined guardrails | Monitor dashboards, handle exceptions | Consistent performance; <2% of actions escalated |
| **Full Autonomy** | 3+ months | Agent manages end-to-end within policy | Strategic oversight, goal setting | Performance exceeds human-managed baseline |

### 6.2 Metrics for Trust Calibration

Track these metrics to build evidence-based confidence:

| Metric | Purpose | Target |
|---|---|---|
| **Decision accuracy rate** | % of autonomous decisions that proved correct in hindsight | >95% |
| **Guardrail trigger rate** | How often hard limits are hit | <5% (decreasing over time) |
| **Escalation rate** | % of decisions requiring human intervention | <10% at maturity |
| **Recovery time** | Time to detect and correct agent errors | <30 minutes |
| **Performance vs. baseline** | Agent ACOS/ROAS vs. human-managed control group | ≥parity, then ≥10% improvement |
| **Drift detection score** | Statistical measure of model/behavior drift | Within 2σ of baseline |

### 6.3 Trust-Building Patterns

1. **A/B testing against human managers**: Run agent-managed campaigns alongside human-managed control groups for the same products
2. **Gradual budget escalation**: Start agents with 10% of total budget, increase by 10% per trust phase
3. **Transparent decision logs**: Every agent action includes reasoning chain, confidence score, and expected outcome
4. **Post-mortem reviews**: Weekly review of all escalations and guardrail triggers
5. **Client-facing dashboards**: Let advertisers see what the agent is doing and why, building their trust simultaneously

### 6.4 Anti-Patterns to Avoid

- **Trust by fiat**: Deploying agents at full autonomy without a shadow period
- **Metric gaming**: Optimizing for metrics that look good but don't reflect business value
- **Alert fatigue**: Too many HOTL alerts cause humans to ignore them
- **Anchoring bias**: Humans over-trusting initial agent success and relaxing oversight too early
- **Black box tolerance**: Accepting agent decisions without explainability

---

## Comparison: Platform Agentic AI Capabilities (as of 2026-04)

| Capability | Amazon Ads | Google Ads | Meta Ads | Spotify Ads |
|---|---|---|---|---|
| **Campaign creation agent** | Ads Agent (Beta) | Marketing Advisor | Advantage+ | Multi-agent planner |
| **Creative generation** | Creative Agent (Beta) | Auto-generated assets | Advantage+ Creative | N/A |
| **Autonomous bidding** | Dynamic bidding (rule-based) | Smart Bidding (ML) | Advantage+ (ML) | In development |
| **Budget pacing** | Rule-based daily caps | AI-driven pacing | Campaign budget optimization | BudgetAgent |
| **Natural language interface** | AMC Skills | Conversational Ads | Limited | Chat-based planning |
| **Multi-agent architecture** | Emerging (separate agents) | Integrated (PMax) | Integrated (Advantage+) | Explicit multi-agent |
| **Transparency** | Moderate (AMC queries) | Low (PMax black box) | Low | High (engineering blog) |
| **API access for 3rd-party agents** | Strong | Moderate | Moderate | Limited |

---

## Actionable Recommendations for Gorilla Commerce

### Immediate (0-3 months)

1. **Build a Shadow Mode bidding agent** that observes current campaigns and recommends bid changes without executing. Measure recommendation accuracy against actual human decisions.
2. **Integrate Amazon's Creative Agent API** for automated creative generation, with human approval gate before publishing.
3. **Implement a guardrail service** with hard spend caps, anomaly detection (15-minute window), and circuit breakers that pause campaigns on threshold breach.
4. **Design the audit trail schema** — every agent action must be logged with: timestamp, agent ID, action type, confidence score, reasoning chain, pre/post state.

### Medium-term (3-6 months)

5. **Deploy a multi-agent architecture** with separate Bidding, Budget, Creative, Audience, and Inventory agents coordinated by an Orchestrator agent. Use the Spotify pattern (specialized resolvers + orchestrator) as reference.
6. **Implement the Risk-Confidence Policy Matrix** to route decisions through the appropriate HITL/HOTL/autonomy path.
7. **Build A/B testing infrastructure** to compare agent-managed vs. human-managed campaigns on identical product sets.
8. **Integrate inventory data** so the Inventory Agent can automatically reduce ad spend on low-stock ASINs (a common source of wasted spend).

### Long-term (6-12 months)

9. **Graduate to Bounded Autonomy** for bidding and budget allocation based on trust calibration metrics.
10. **Implement agent-to-agent communication** following IAB Tech Lab standards as they mature.
11. **Build client-facing trust dashboards** showing agent reasoning, performance vs. baseline, and intervention history.
12. **Explore cross-platform orchestration** where agents manage Amazon + Google + Meta campaigns holistically, optimizing total advertising portfolio.

### Key Architectural Decisions

| Decision | Recommendation | Rationale |
|---|---|---|
| **Agent framework** | Google ADK or LangGraph | Proven in production (Spotify); strong tooling support |
| **LLM backbone** | Claude or Gemini 2.5 Pro | Reasoning quality for complex bid decisions |
| **Communication bus** | gRPC + event streaming | Low latency for real-time bidding decisions |
| **State store** | PostgreSQL + Redis | Durable state + fast cache for active campaigns |
| **Guardrail engine** | Deterministic rules + statistical anomaly detection | Hard limits must not depend on AI |
| **HITL platform** | Slack/Teams integration for approvals | Where operators already work |

---

## Sources

- [Amazon Ads unBoxed 2025: Agentic AI](https://adadvance.com/blog/amazon-ads-unboxed-2025-agentic-ai/) (Ad Advance, 2025)
- [Amazon Ads unBoxed Recap](https://advertising.amazon.com/library/news/unboxed-2025-recap) (Amazon Ads, 2025)
- [Spotify Multi-Agent Architecture for Advertising](https://engineering.atspotify.com/2026/2/our-multi-agent-architecture-for-smarter-advertising) (Spotify Engineering, 2026)
- [When Google Ads AI Goes Wrong: $50K Budget Disasters](https://groas.ai/post/when-google-ads-ai-goes-wrong-50k-budget-disasters-recovery-strategies-real-case-studies-2025) (GROAS.ai, 2025)
- [Multi-Agent Systems Redefining Campaign Optimization](https://www.glean.com/perspectives/the-death-of-manual-campaign-optimization-multi-agent-systems-in-performance) (Glean, 2026)
- [Agentic AI's OODA Loop Problem](https://cyber.harvard.edu/story/2025-10/agentic-ais-ooda-loop-problem) (Schneier & Raghavan / IEEE S&P, 2025)
- [Harnessing the OODA Loop for Agentic AI](https://labs.sogeti.com/harnessing-the-ooda-loop-for-agentic-ai-from-generative-foundations-to-proactive-intelligence/) (Sogeti Labs, 2025)
- [AI Agent Guardrails: Production Guide for 2026](https://authoritypartners.com/insights/ai-agent-guardrails-production-guide-for-2026/) (Authority Partners, 2026)
- [2026 Playbook for Building Reliable Agentic Workflows](https://promptengineering.org/agents-at-work-the-2026-playbook-for-building-reliable-agentic-workflows/) (Prompt Engineering, 2026)
- [Human-in-the-Loop AI: The New Marketing Playbook](https://www.smartly.io/resources/the-future-of-marketing-ai-automation-with-human-oversight) (Smartly.io, 2025)
- [2026: Year of Autonomous Marketing](https://www.agenticfoundry.ai/post/2026-is-the-year-of-autonomous-marketing-get-comfortable-being-human-on-the-loop) (Agentic Foundry, 2026)
- [Future of Human-in-the-Loop AI (2026)](https://parseur.com/blog/future-of-hitl-ai) (Parseur, 2026)
- [Human-in-the-Loop Meets Agentic AI](https://beetroot.co/ai-ml/human-in-the-loop-meets-agentic-ai-building-trust-and-control-in-automated-workflows/) (Beetroot, 2025)
- [Lessons from 2025 on Agents and Trust](https://cloud.google.com/transform/ai-grew-up-and-got-a-job-lessons-from-2025-on-agents-and-trust) (Google Cloud, 2025)
- [IAB Tech Lab Framework for Agentic Ad Buying](https://www.adexchanger.com/?p=446969) (AdExchanger, 2026)
- [GenAI Will Take Over Programmatic Advertising in 2026](https://www.emarketer.com/content/genai-will-take-over-programmatic-advertising-2026-agentic-ai-isn-t-far-behind) (eMarketer, 2026)
- [7 Worst AI Advertising Backfires of 2025](https://news.designrush.com/7-worst-ai-advertising-backfires-2025) (DesignRush, 2025)
- [Agentic AI in Advertising: Not Yet Fully Autonomous](https://www.newscaststudio.com/2026/04/09/agentic-ai-in-advertising-is-real-but-not-yet-fully-autonomous/) (NewscastStudio, 2026)
- [5 AI Agents Advertisers Will Use in 2026](https://aijourn.com/5-ai-agents-advertisers-will-use-in-2026/) (AI Journal, 2026)
- [Google Ads AI Agent: Automate Campaigns in 2026](https://cattix.com/blog/google-ads-ai-agent/) (Cattix, 2026)
- [Using AI in Advertising Without Risk](https://basis.com/blog/how-advertisers-can-harness-ai-while-navigating-brand-safety-consumer-trust-and-legal-concerns) (Basis, 2025)
- [Algorithmic Liability in AI Ad Placements 2025](https://www.influencers-time.com/algorithmic-liability-navigating-ai-ad-placements-in-2025/) (Influencers Time, 2025)
