# SMA Strategy Pack Outline

This file is not meant to be used directly by the current SMC assistant.

It exists to show how someone would fork this repo for a different strategy and replace the SMC-specific pieces with an SMA-specific stack.

---

## What Must Change

For an SMA strategy, these files should be considered strategy-specific and replaced together:

- `analysis.py`
- `app.py` endpoint response shape for `/analyze`
- `gpt_instructions.md`
- `gpt-schema.yaml`

The rest of the repo can often stay mostly the same:

- `ctrader_client.py`
- `/place-order`
- `/open-positions`
- `/pending-orders`
- `/journal-entry`
- deployment and Docker files

---

## Example SMA Concepts To Return

An SMA-based `/analyze` response might include fields like:

- `Trend_Bias`
- `Fast_SMA`
- `Slow_SMA`
- `Crossover`
- `Pullback_State`
- `Entry_Setup`
- `Risk_Model`
- `Confidence`

Example direction logic:

- bullish when fast SMA is above slow SMA
- bearish when fast SMA is below slow SMA
- neutral when the averages are flat or tightly compressed

Example entry logic:

- bullish continuation only after pullback into the fast SMA and bullish candle confirmation
- bearish continuation only after pullback into the fast SMA and bearish rejection

---

## Example `/analyze` Shape

```json
{
  "Trend_Bias": "bullish",
  "Fast_SMA": 1.0942,
  "Slow_SMA": 1.0918,
  "Crossover": {
    "state": "bullish_cross_active",
    "cross_time": "2026-04-18T08:00:00+00:00"
  },
  "Pullback_State": {
    "status": "retesting_fast_sma",
    "distance_from_fast_sma": 0.0004
  },
  "Entry_Setup": {
    "entry_type": "bullish_continuation",
    "entry_price": 1.0948,
    "stop_loss": 1.0931,
    "take_profit": 1.0982
  },
  "Confidence": {
    "score": 74,
    "eligible": true,
    "summary": "Bullish SMA continuation setup is valid."
  }
}
```

---

## Example GPT Instruction Shift

The SMC instruction file would need to be replaced with an SMA-specific instruction set.

That new instruction file should tell ChatGPT things like:

- always call `/analyze` first
- use SMA trend direction from the backend instead of inferring it manually
- only recommend trades when the backend `Confidence` object is eligible
- explain the setup in SMA terms, not SMC terms
- never mention CHOCH, BOS, OB, or FVG if the backend no longer returns them

---

## Example Schema Shift

The Action schema must match the new SMA response.

If the backend stops returning:

- `Checklist`
- `Confluence`
- `MTF_Zones`

then those fields should be removed from the schema and replaced with the SMA-specific fields.

Otherwise the Custom GPT will reason from a stale contract.

---

## Replacement Checklist

1. Replace SMC analysis logic in `analysis.py`.
2. Replace `/analyze` response shaping in `app.py`.
3. Replace `gpt_instructions.md` with SMA-specific instructions.
4. Replace `gpt-schema.yaml` with the SMA response contract.
5. Re-run local smoke checks.
6. Update the Custom GPT in ChatGPT with the new instructions and schema.
7. Test the GPT on one symbol before using it broadly.

---

## Important Rule

Do not mix strategy vocabularies.

If the backend is SMA-based, the GPT instructions and action schema must also be SMA-based. The safest pattern is one strategy, one backend contract, one instruction file, and one matching schema.
