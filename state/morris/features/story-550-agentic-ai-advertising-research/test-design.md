# STORY-550 — Test Design: Agentic AI Advertising Research

## Objective
Verify that the research document `ads-research/09-agentic-ai-advertising.md` exists, is well-structured, and covers all 6 required topic areas.

## Test Cases

### TC-1: File Exists
- **Check**: `ads-research/09-agentic-ai-advertising.md` exists in the repository
- **Command**: `test -f ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-2: Executive Summary Present
- **Check**: File contains an "Executive Summary" section
- **Command**: `grep -q "Executive Summary" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-3: Section 1 — Agentic AI Architectures for Ad Optimization
- **Check**: File covers OODA loops, reward signals, and guardrails
- **Command**: `grep -q "OODA" ads-research/09-agentic-ai-advertising.md && grep -q "Reward Signal" ads-research/09-agentic-ai-advertising.md && grep -q "Guardrail" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-4: Section 2 — Real-World Case Studies
- **Check**: File contains case studies of AI agents managing ad campaigns
- **Command**: `grep -q "Case Stud" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-5: Section 3 — Failure Modes and Safety Guardrails
- **Check**: File covers failure modes for autonomous ad spend
- **Command**: `grep -q "Failure Mode" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-6: Section 4 — Multi-Agent Systems in Advertising
- **Check**: File covers bidding agents, budget agents, creative agents
- **Command**: `grep -q "Multi-Agent" ads-research/09-agentic-ai-advertising.md && grep -q "Bidding Agent" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-7: Section 5 — Human-in-the-Loop Patterns
- **Check**: File covers HITL patterns for high-stakes ad spend
- **Command**: `grep -q "Human-in-the-Loop" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-8: Section 6 — Trust Calibration
- **Check**: File covers building confidence in autonomous ad agents
- **Command**: `grep -q "Trust Calibration" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

### TC-9: Dated Citations
- **Check**: Claims are dated with (Source, YYYY) or (as of YYYY-MM-DD) patterns
- **Command**: `grep -cP '\(\w+.*,\s*20\d{2}\)|\(as of 20\d{2}' ads-research/09-agentic-ai-advertising.md | awk '{if ($1 >= 10) print "PASS"; else print "FAIL"}'`

### TC-10: Comparison Tables
- **Check**: File contains markdown tables for comparison
- **Command**: `grep -c '|.*|.*|' ads-research/09-agentic-ai-advertising.md | awk '{if ($1 >= 10) print "PASS"; else print "FAIL"}'`

### TC-11: Actionable Recommendations
- **Check**: File contains recommendations section for Gorilla Commerce
- **Command**: `grep -q "Recommendations for Gorilla Commerce" ads-research/09-agentic-ai-advertising.md && echo PASS || echo FAIL`

## Execution

Run all tests:
```bash
cd /home/hermes/state/morris
for tc in \
  "test -f ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Executive Summary' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'OODA' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Case Stud' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Failure Mode' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Multi-Agent' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Human-in-the-Loop' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Trust Calibration' ads-research/09-agentic-ai-advertising.md" \
  "grep -q 'Recommendations for Gorilla Commerce' ads-research/09-agentic-ai-advertising.md"; do
  eval "$tc" && echo "PASS: $tc" || echo "FAIL: $tc"
done
```
