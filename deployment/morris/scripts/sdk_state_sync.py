#!/usr/bin/env python3
import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

RUN_LABEL = "state_sync"
MODEL = "claude-haiku-4-5"
MODE = "claude_background"
TASKS = [
  {
    "repo": "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents",
    "max_turns": 15,
    "prompt": (
        "You are Morris running the hourly state sync. Two jobs:\n\n"
        "JOB 1 — Escalation scan.\n"
        "Read /home/hermes/state/morris/cron-status.md, fleet-status.md, and pr-tracker.md. "
        "If any FAIL entry, fleet alert, or PR needs human action, write a brief summary to "
        "/home/hermes/state/morris/escalation-needed.md. Otherwise overwrite that file with "
        "'No escalations needed' plus timestamp.\n\n"
        "JOB 2 — Failed-story recovery sweep.\n"
        "Invoke the /requeue-failed skill. Follow it strictly — classify each candidate before "
        "deciding, respect the eligibility rules (60-min window, claimed at least once, no PR yet, "
        "retry-count cap, environmental error class), and respect the hard caps (max 10 requeued "
        "per sweep, abort if 5+ share the same class). Write the report to "
        "/home/hermes/state/morris/requeue-status.md and append any escalations to "
        "escalation-needed.md.\n\n"
        "Do NOT modify memory, code, settings, or any files outside /home/hermes/state/morris/. "
        "Do NOT bulk-reset failed stories — the skill is selective on purpose."
    ),
  }
]

RUN_DIR = Path('/home/hermes/.hermes/scripts/runs')
RUN_DIR.mkdir(parents=True, exist_ok=True)

def run_shell(cmd, timeout=600):
    t0 = time.time()
    p = subprocess.run(['bash','-lc',cmd], capture_output=True, text=True, timeout=timeout)
    return {
        'cmd': cmd,
        'rc': p.returncode,
        'stdout': (p.stdout or '')[-4000:],
        'stderr': (p.stderr or '')[-4000:],
        'seconds': round(time.time()-t0, 2),
    }

def launch_claude(repo, prompt, max_turns=20):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    log = RUN_DIR / f"{RUN_LABEL}_{ts}_{abs(hash(repo+prompt))%100000}.log"
    cmd = f"cd {repo} && claude --permission-mode bypassPermissions -p {shlex.quote(prompt)} --model {MODEL} --max-turns {max_turns} < /dev/null"
    with open(log, 'w') as f:
        p = subprocess.Popen(['bash','-lc',cmd], stdout=f, stderr=subprocess.STDOUT)
    return {'repo': repo, 'pid': p.pid, 'log': str(log), 'max_turns': max_turns}

out = {
    'collected_at': datetime.now(timezone.utc).isoformat(),
    'run_label': RUN_LABEL,
    'mode': MODE,
    'launched': [],
    'commands': [],
}

if MODE == 'claude_background':
    for t in TASKS:
        out['launched'].append(launch_claude(t['repo'], t['prompt'], int(t.get('max_turns',20))))
elif MODE == 'shell':
    for t in TASKS:
        out['commands'].append(run_shell(t['cmd'], int(t.get('timeout',600))))
elif MODE == 'hybrid':
    for t in TASKS:
        if t.get('kind') == 'shell':
            out['commands'].append(run_shell(t['cmd'], int(t.get('timeout',600))))
        else:
            out['launched'].append(launch_claude(t['repo'], t['prompt'], int(t.get('max_turns',20))))

print(json.dumps(out, indent=2))
