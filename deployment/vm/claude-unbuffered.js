const fs = require('fs');
const path = require('path');
const { spawn, execSync } = require('child_process');

const CLAUDE_REAL = '/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js';
const logDir = '/tmp/claude-sdlc-logs';
fs.mkdirSync(logDir, { recursive: true });
const ts = new Date().toISOString().replace(/[:.]/g, '-');
const logFile = path.join(logDir, `session-${ts}.log`);
const logFd = fs.openSync(logFile, 'a');

const args = process.argv.slice(2);
fs.writeSync(logFd, `[${new Date().toISOString()}] claude ${args.join(' ')}\n`);

// Check if token needs refresh before starting
try {
  const home = process.env.HOME || '/home/hermes';
  const credsPath = path.join(home, '.claude', '.credentials.json');
  if (fs.existsSync(credsPath)) {
    const creds = JSON.parse(fs.readFileSync(credsPath, 'utf8'));
    const oauth = creds.claudeAiOauth || {};
    const expiresAt = oauth.expiresAt || 0;
    const now = Date.now();
    if (expiresAt > 0 && expiresAt < now + 300000) { // expired or expires in <5 min
      fs.writeSync(logFd, `[refresh] Token expired/expiring, triggering refresh...\n`);
      // Run a quick --version call which triggers the SDK's internal refresh
      try {
        execSync(`node ${CLAUDE_REAL} --version`, { timeout: 15000, stdio: 'ignore' });
        fs.writeSync(logFd, `[refresh] Token refreshed successfully\n`);
      } catch (e) {
        fs.writeSync(logFd, `[refresh] Warning: refresh attempt failed: ${e.message}\n`);
      }
    }
  }
} catch (e) {
  // Non-fatal
}

const isPrint = args.includes('-p') || args.includes('--print');

if (isPrint) {
  const newArgs = [...args];
  const ofIdx = newArgs.indexOf('--output-format');
  if (ofIdx !== -1) newArgs.splice(ofIdx, 2);
  newArgs.push('--output-format', 'stream-json', '--verbose');

  const child = spawn('node', [CLAUDE_REAL, ...newArgs], {
    stdio: ['inherit', 'pipe', 'pipe'],
    env: process.env,
  });

  let lastLogTime = Date.now();
  let toolCount = 0;

  const heartbeat = setInterval(() => {
    if (Date.now() - lastLogTime > 25000) {
      const msg = `[working] ${toolCount} tool calls since last update...\n`;
      process.stdout.write(msg);
      fs.writeSync(logFd, msg);
    }
  }, 30000);

  child.stdout.on('data', (chunk) => {
    for (const line of chunk.toString().split('\n')) {
      if (!line.trim()) continue;
      try {
        const evt = JSON.parse(line);
        if (evt.type === 'assistant' && evt.message && evt.message.content) {
          const content = evt.message.content;
          if (Array.isArray(content)) {
            for (const block of content) {
              if (block.type === 'text' && block.text && block.text.trim()) {
                const msg = block.text.trim() + '\n';
                process.stdout.write(msg);
                fs.writeSync(logFd, msg);
                lastLogTime = Date.now();
                toolCount = 0;
              } else if (block.type === 'tool_use') {
                toolCount++;
              }
            }
          } else if (typeof content === 'string' && content.trim()) {
            process.stdout.write(content + '\n');
            fs.writeSync(logFd, content + '\n');
            lastLogTime = Date.now();
            toolCount = 0;
          }
        } else if (evt.type === 'result') {
          const msg = `[done] cost=$${evt.cost_usd || '?'} duration=${Math.round((evt.duration_ms || 0)/1000)}s turns=${evt.num_turns || '?'}\n`;
          process.stdout.write(msg);
          fs.writeSync(logFd, msg);
          lastLogTime = Date.now();
        }
      } catch (e) {}
    }
  });

  child.stderr.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (text) fs.writeSync(logFd, `[stderr] ${text}\n`);
  });

  child.on('exit', (code) => {
    clearInterval(heartbeat);
    fs.writeSync(logFd, `[exit] code=${code}\n`);
    fs.closeSync(logFd);
    process.exit(code || 0);
  });
} else {
  // Non-print mode: run directly (supports interactive, token refresh, etc.)
  require(CLAUDE_REAL);
}
