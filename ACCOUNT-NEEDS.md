# What this build needs from an account (and what only Parish can do)

| Step | Who | Status (2026-11-18) |
|---|---|---|
| ElevenLabs account (Creator plan, yearly, already held) | Parish | Done |
| Separate API key `neo-portfolio-elevenlabs-agent-test` (7 scopes, 3,000-credit cap, 30-day expiry, auto-disable if leaked), stored in Keychain item `elevenlabs-agent-test-key` | Neo | Done 17 Nov 20:24. backtalk's own key untouched |
| Live agent, 4 tools, workspace secret, knowledge base, dictionary, post-call webhook | Neo (setup script) | Done 17 Nov. **Paused** 18 Nov (daily limit 0, concurrency 0, read back); signed URLs only |
| Tool, webhook and admin secrets | Neo | Keychain items `agent-tool-secret`, `elevenlabs-webhook-secret`, `voiceagent-admin-token` |
| OK to spend ~1,200 more credits on two 55-second test calls (booking; lead + urgent handover) | **Parish only** (credit rule) | DECLINED 18 Nov: no further paid calls; booking and handover stay tested by simulation |

No card, payment or new account is needed.

## Running more live calls (only if Parish later says yes; not planned)

1. Start the server and a tunnel: `research/elevenlabs-build/live/run_server.sh`, `cloudflared tunnel --url http://localhost:8781`.
2. `DATA_DIR=.../live/var python scripts/setup_agent.py --keychain elevenlabs-agent-test-key update --server-url <new tunnel URL>` (also lifts the pause). The post-call webhook URL is fixed at creation: delete and re-create the workspace webhook for the new URL, or keep the old one and read the conversation back with `GET /v1/convai/conversations/{id}` instead.
3. `python scripts/live_call.py --keychain elevenlabs-agent-test-key --clips .../live/clips/book3 --out .../live/runs/booking3 --max-secs 55`, then the same with `clips/lead4`.
4. `setup_agent.py pause`, stop the tunnel and server, copy the evidence into `examples/live-run/`, update the README table.

## When the build is finished

`setup_agent.py delete` removes the agent, tools, knowledge base, webhook and secret; then delete the test key in the ElevenLabs dashboard (it also expires on its own after 30 days, ~17 Dec 2026).
