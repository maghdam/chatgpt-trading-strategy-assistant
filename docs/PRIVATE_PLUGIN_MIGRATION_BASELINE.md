# Private trading-research plugin — migration baseline (Plugin 1, staged)

This repository is the **existing SMC/cTrader Custom GPT assistant**, separate from
`maghdam/GenAI-MultiAgent-TradingSystem`. The TradeAgent project's
[issue #133](https://github.com/maghdam/GenAI-MultiAgent-TradingSystem/issues/133)
retains the two-repository, private-first plan; neither repo is being merged into the other.

## Repo-only inventory (not a backup of the live Custom GPT)

| File | Role | Important limitation |
| --- | --- | --- |
| `gpt_instructions.md` | Repository version of SMC GPT instructions | **Not verified** against currently published GPT instructions |
| `gpt-schema.yaml` | Original, broad Custom GPT Actions contract | Includes broker order placement, Notion journaling and broker-account reads |
| `app.py` | FastAPI SMC/market-data and order/journal service | Existing `/place-order` and `/placeOrder` handlers do not show a request-authentication dependency in the reviewed code |
| `gpt-schema-research-only.json` | **New, staging-only** read contract for `/analyze` and `/fetch-data` | Placeholder `example.invalid`; not an installed plugin or connected Action |
| `tests/test_research_only_schema.py` | Verifies exact read-only allowlist and disabled network target | Does not test running service or prove server-side authorization |

The original schema and instructions are left unchanged so existing GPT
configuration can be compared without losing historical information.

## Next operator-side evidence before any migration

1. In the **actual existing Custom GPT** configuration, export/copy the live
   instructions, attached knowledge file names and contents, all Action schemas,
   configured domains, authentication types and action permissions to a **private**
   backup. **Never commit tokens, cookies, connected-account data or private knowledge
   attachments to public GitHub**.
2. Compare the real GPT with the two repository files and record discrepancies
   privately. Repo files must not be assumed to match what is deployed.
3. Review the running Render service's authentication and deployment status.
   Source inspection found broker-order POST routes without an apparent HTTP
   authentication guard. Treat any internet-exposed service connected to a
   broker account as a **potential security risk** until independently verified
   and protected. Do not exercise order routes to test this.
4. Keep research-only operations separate from order/close/cancel, broker-account
   identity, Notion writes, and future TradeAgent protected execution.
5. Only after server-side authentication, authorization, rate limiting,
   logging/redaction, and order-route lockout are tested should an authenticated
   HTTPS MCP server and a **private** ChatGPT plugin be connected.

The staging JSON file has an intentionally non-routable `example.invalid`
server address. It can be reviewed and contract-tested offline. **Do not paste
it into a live GPT or replace that address with Render/localhost as a shortcut.**
Hiding `/place-order` from a client-side schema is **not a security control**:
the backend must independently enforce an explicit deny-by-default policy.

## Why not merge TradeAgent MCP PR #127?

PR #127 is an old **localhost-only cTrader candle** proof of concept in the
separate TradeAgent repository. It is still draft/unmerged and is **not** the
approved initial private GPT migration. Its MCP SDK invocation, runtime
compatibility, and security model require independent tests before reuse;
never tunnel an unauthenticated local service. The initial migration remains
anchored to this assistant repository, then later optional TradeAgent research
integration.

## Offline verification

```powershell
python -m unittest discover -s tests -p "test_research_only_schema.py" -v
python -m unittest discover -s tests -p "test_*.py" -v
```

This PR prepares the safe **repository-side baseline** only. It does not
migrate a GPT, connect a plugin, add MCP endpoints, change server behavior,
place a trade, or assert that the currently deployed backend is secure.
