# ChatGPT Trading Strategy Assistant with cTrader API

A ChatGPT-powered trading assistant backend for cTrader that can analyze markets, journal setups, and place trades through Custom GPT Actions.

The default implementation ships with a Smart Money Concepts (SMC) strategy, but the real point of the repo is broader:

- use ChatGPT as the trader-facing interface
- use FastAPI as the action backend
- use cTrader Open API as the market and execution layer
- swap the strategy logic and GPT instructions for your own system

This repo is best understood as a reusable pattern for building strategy-specific trading assistants, not just a single SMC bot.

---

## Why This Repo Exists

Most trading bot repos focus on one of these:

- backtesting only
- broker execution only
- prompt experiments with no real broker integration

This project connects all three layers that people actually want:

1. a ChatGPT Custom GPT with Actions
2. a live market and execution backend
3. strategy-specific analysis logic

That makes it useful both as a working SMC prototype and as a template for other trading workflows such as:

- breakout systems
- trend-following systems
- Fibonacci or indicator-based systems
- session-based scalping
- news-aware discretionary assistants
- portfolio monitoring or journaling assistants

---

## What It Does

- Fetches live OHLC data from cTrader Open API
- Runs structured multi-timeframe analysis through `/analyze`
- Returns machine-usable action payloads for ChatGPT
- Places market, limit, and stop orders via `/place-order`
- Lists open positions and pending orders
- Logs setups to Notion through `/journal-entry`
- Optionally renders price charts for the GPT or the user

Current default strategy signals include:

- CHOCH
- BOS
- order blocks
- fair value gaps
- session sweeps
- candle confirmations
- weighted confluence scoring

---

## SMC By Default, Reusable As A Pattern

This repo is intentionally built around Smart Money Concepts. The current:

- [analysis.py](analysis.py)
- [app.py](app.py)
- [gpt_instructions.md](gpt_instructions.md)
- [gpt-schema.yaml](gpt-schema.yaml)

are meant to work together as an SMC-specific stack.

If someone wants a different strategy, such as SMA crossover, RSI divergence, or breakout trading, the right approach is not to keep these exact SMC files and make them vague. The right approach is to create a new strategy-specific set of:

1. backend analysis logic
2. backend response contract
3. Custom GPT instructions
4. Custom GPT action schema

Then replace the SMC versions in the backend and in the ChatGPT Custom GPT configuration.

So the reusable asset is not "one universal instruction file." The reusable asset is:

- ChatGPT Action flow
- broker integration
- analysis endpoint contract
- journaling and execution plumbing

### Example Non-SMC Adaptations

- EMA pullback strategy:
  Create EMA-specific analysis logic, EMA-specific GPT instructions, and an EMA-specific action schema.
- RSI divergence strategy:
  Create divergence-specific backend detection, divergence-specific instructions, and a schema that exposes those fields.
- breakout strategy:
  Create breakout-specific market structure fields, breakout instructions, and a breakout action contract.
- swing trend strategy:
  Create a trend-strategy backend, trend-strategy instructions, and a matching schema.

Concrete example:

- [examples/sma-strategy-pack-outline.md](examples/sma-strategy-pack-outline.md)

---

## Forking This For Another Strategy

If you want to reuse the repo for a different strategy, keep the plumbing and replace the strategy layer.

What usually stays the same:

- cTrader connectivity in [ctrader_client.py](ctrader_client.py)
- FastAPI app structure in [app.py](app.py)
- order placement and journaling endpoints
- ChatGPT Actions workflow

What usually changes:

- strategy logic in [analysis.py](analysis.py)
- `/analyze` response shape in [app.py](app.py)
- Custom GPT instructions in [gpt_instructions.md](gpt_instructions.md)
- action schema in [gpt-schema.yaml](gpt-schema.yaml)

### Example: SMA Crossover Version

If you want an SMA crossover assistant, do not keep the SMC instructions and just rename things. Replace the strategy layer with SMA-specific outputs such as:

- fast SMA value
- slow SMA value
- crossover direction
- trend filter
- pullback state
- invalidation price
- take-profit logic
- confidence or confluence score

In practice, the steps are:

1. Rewrite [analysis.py](analysis.py) so it computes SMA signals instead of CHOCH, BOS, OB, and FVG.
2. Update [app.py](app.py) so `/analyze` returns SMA-specific fields.
3. Replace [gpt_instructions.md](gpt_instructions.md) with instructions telling ChatGPT how to interpret the SMA payload and when to place or avoid trades.
4. Replace [gpt-schema.yaml](gpt-schema.yaml) so the Custom GPT Action matches the new SMA response contract.
5. Paste the new instructions and schema into your Custom GPT configuration.

That keeps the architecture while making the strategy implementation honest and internally consistent.

---

## How The Custom GPT Uses It

The intended ChatGPT setup is:

1. You create a Custom GPT in ChatGPT.
2. You paste [gpt_instructions.md](gpt_instructions.md) into the GPT Instructions field.
3. You paste [gpt-schema.yaml](gpt-schema.yaml) into the Actions schema field.
4. You point the action base URL to your deployed backend.

At runtime, the GPT should:

1. call `/analyze`
2. read structured fields like `HTF_Bias`, `Checklist`, and `Confluence`
3. decide whether a setup qualifies
4. optionally place or journal the trade

The latest backend contract now includes:

- `MTF_Zones` with OB, BOS, and FVG data
- `Checklist` with CHOCH, BOS, OB, FVG, Sweep, and Candle
- `Confluence` with weighted score and qualification status

---

## Project Structure

```text
chatgpt-trading-strategy-assistant/
|-- app.py
|-- analysis.py
|-- charts.py
|-- ctrader_client.py
|-- gpt_instructions.md
|-- gpt-schema.yaml
|-- requirements.txt
|-- Dockerfile
|-- docker-compose.yml
|-- .env.example
|-- images/
|-- examples/
|   `-- analyze-response.sample.json
|   `-- sma-strategy-pack-outline.md
|-- scripts/
|   `-- smoke_test.py
`-- tests/
    `-- test_analysis.py
```

### Core Files

- [app.py](app.py): FastAPI app and action endpoints
- [analysis.py](analysis.py): strategy analysis logic
- [ctrader_client.py](ctrader_client.py): broker connectivity and order execution
- [gpt_instructions.md](gpt_instructions.md): Custom GPT prompt behavior
- [gpt-schema.yaml](gpt-schema.yaml): Actions schema for ChatGPT

---

## `/analyze` Output Contract

The `/analyze` endpoint is the main decision engine. It is designed to return structured technical analysis that the GPT can cite directly instead of re-deriving logic from raw candles.

Current top-level fields:

- `HTF_Bias`
- `MTF_Zones`
- `LTF_Entry`
- `Previous_Day_High`
- `Previous_Day_Low`
- `Session_Levels`
- `Checklist`
- `Confluence`
- `News`

Sample response:

- [examples/analyze-response.sample.json](examples/analyze-response.sample.json)

This matters for forks because if you change the strategy, you should keep the response contract coherent enough that the GPT can reason over it without hidden assumptions.

---

## Setup

### Requirements

- Python 3.10+
- Docker and Docker Compose, if you want containerized runs
- cTrader Open API credentials
- ChatGPT Plus or another ChatGPT plan that supports Custom GPT Actions
- Notion integration credentials, if you want journaling

### Environment

Copy `.env.example` to `.env` and fill in your values:

```env
NOTION_SECRET=your_notion_integration_token
NOTION_DB_ID=your_notion_database_id

CTRADER_CLIENT_ID=your_ctrader_client_id
CTRADER_CLIENT_SECRET=your_ctrader_client_secret
CTRADER_HOST_TYPE=demo
CTRADER_ACCESS_TOKEN=your_ctrader_access_token
CTRADER_ACCOUNT_ID=your_ctrader_account_id

NGROK_TOKEN=your_ngrok_auth_token
```

### Local Run

```bash
python -m pip install -r requirements.txt
python app.py
```

Or with Docker:

```bash
docker-compose up --build
```

---

## Custom GPT Setup

Inside ChatGPT:

1. Go to `Explore GPTs` -> `Create`
2. Open the `Configure` tab
3. Paste [gpt_instructions.md](gpt_instructions.md) into Instructions
4. Add a new Action
5. Paste [gpt-schema.yaml](gpt-schema.yaml)
6. Set the API base URL to your deployed backend URL

If you fork this repo and replace the strategy:

- update the instructions first
- then update the schema if endpoint inputs or outputs changed
- then validate the GPT behavior against a real `/analyze` response

---

## Smoke Test

Use the included smoke test to validate a running backend before wiring it into ChatGPT:

```bash
python scripts/smoke_test.py --base-url http://127.0.0.1:8000 --symbol EURUSD
```

It checks:

- `/health`
- `/fetch-data`
- `/analyze`

The script uses only Python standard library modules, so it does not require extra dependencies.

---

## Example Prompts

- Analyze EURUSD using SMC
- What is the HTF bias on NAS100?
- Has New York swept the London high?
- Reevaluate my GBPUSD long
- Place a buy limit on EURUSD
- Log this NAS100 trade
- Return all open positions
- Return all pending orders

---

## Screenshots

### Custom GPT Frontend

![GPT Assistant](images/gpt-frontend.png)

### GPT Instructions

![GPT Instructions](images/gpt-configuration-instruction.png)

### GPT Action Schema

![GPT Action Schema](images/gpt-configuration-action-schema.png)

### Trade Analysis

![Trade Analysis](images/trade-analysis.png)

### Order Execution

![Order Execution](images/order-execution.png)

### Notion Journal

![Notion Journal](images/notion-journal.png)

---

## API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/analyze` | Full multi-timeframe strategy analysis |
| `/fetch-data` | Fetch raw OHLC data |
| `/tag-sessions` | Label candles by session |
| `/session-levels` | Compute session highs and lows |
| `/place-order` | Submit orders through cTrader |
| `/open-positions` | View active positions |
| `/pending-orders` | View pending orders |
| `/journal-entry` | Save trade notes to Notion |

---

## Important Notes

- This repo is educational and experimental software.
- Do not treat ChatGPT output as a guaranteed trading edge.
- Do not deploy against live funds until you have verified strategy logic, execution semantics, and failure handling.
- Always test on demo first.

---

## License

This project is licensed under the [MIT License](LICENSE).
