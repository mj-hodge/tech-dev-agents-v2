## Morris Review --- PR 307 (STORY-873: Stop duplicate failed event emission)

Verdict: REQUEST_CHANGES | CI: No checks reported | Branch stacked on 303 + 306

HIGH (merge blockers)

H1 -- Stacked PR missing dependency declaration
Branch has STORY-861/871/872 commits before STORY-873. Add to PR body: Stacked on 303 (STORY-871) and 306 (STORY-872). Must merge after both, or rebase onto main once they land.

H2 -- No CI signal
gh pr checks 307 returns no checks. Paste local pytest output (93/93 GREEN) in PR body or trigger CI.

MEDIUM

M1 -- Silent downstream for attention_queue classes
After fix, attention_queue emits zero events. Confirm nothing queries failure_reason like policy_routed:% in dispatch_v2_events.

Code correctness (STORY-873) -- PASS
Fix is correct: removes duplicate record_event(failed,...) in attention_queue branch. DB trigger handles state+lane from caller. 16 tests, AC1+AC2 covered. No security surface. SDLC: features/story-873-stop-duplicate-failed-event-emission/ present.

Required before re-review
1. Add stacked-PR dependency note (H1): must merge after 303 + 306
2. Provide CI evidence (H2): paste pytest -v output or trigger workflow

-- Morris (automated review) 2026-05-05