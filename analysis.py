from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Optional


def tag_sessions_local(candles):
    def label_session(utc_iso_time: str) -> str:
        dt = datetime.fromisoformat(utc_iso_time.replace("Z", "+00:00"))
        hour = dt.hour
        if 0 <= hour < 7:
            return "Asia"
        if 7 <= hour < 12:
            return "London"
        if 12 <= hour < 17:
            return "NewYork"
        if 17 <= hour < 24:
            return "PostNY"
        return "Unknown"

    return [{**c, "session": label_session(c["time"])} for c in candles]


def compute_session_levels(candles):
    session_groups = defaultdict(list)
    for candle in candles:
        session_groups[candle["session"]].append(candle)

    session_levels = {}
    for session, group in session_groups.items():
        highs = [c["high"] for c in group]
        lows = [c["low"] for c in group]
        session_levels[session] = {
            "high": max(highs) if highs else None,
            "low": min(lows) if lows else None,
        }
    return session_levels


def _range(candle: dict) -> float:
    return max(candle["high"] - candle["low"], 0.0)


def _body(candle: dict) -> float:
    return abs(candle["close"] - candle["open"])


def _is_bullish(candle: dict) -> bool:
    return candle["close"] > candle["open"]


def _is_bearish(candle: dict) -> bool:
    return candle["close"] < candle["open"]


def _close_position(candle: dict) -> float:
    candle_range = _range(candle)
    if candle_range == 0:
        return 0.5
    return (candle["close"] - candle["low"]) / candle_range


def _median_range(candles: list[dict], window: int = 20) -> float:
    sample = candles[-window:] if len(candles) > window else candles
    ranges = [_range(c) for c in sample if _range(c) > 0]
    return median(ranges) if ranges else 0.0


def _median_body(candles: list[dict], window: int = 20) -> float:
    sample = candles[-window:] if len(candles) > window else candles
    bodies = [_body(c) for c in sample if _body(c) > 0]
    return median(bodies) if bodies else 0.0


def _find_swings(candles: list[dict], window: int = 2) -> list[dict]:
    swings = []
    if len(candles) < (window * 2) + 1:
        return swings

    for idx in range(window, len(candles) - window):
        candle = candles[idx]
        left = candles[idx - window:idx]
        right = candles[idx + 1:idx + window + 1]

        if all(candle["high"] > c["high"] for c in left + right):
            swings.append(
                {
                    "kind": "high",
                    "index": idx,
                    "price": candle["high"],
                    "time": candle["time"],
                }
            )

        if all(candle["low"] < c["low"] for c in left + right):
            swings.append(
                {
                    "kind": "low",
                    "index": idx,
                    "price": candle["low"],
                    "time": candle["time"],
                }
            )

    return swings


def _find_break_events(
    candles: list[dict],
    lookback: int,
    swing_window: int,
) -> list[dict]:
    scoped = candles[-lookback:] if len(candles) > lookback else candles
    offset = len(candles) - len(scoped)
    swings = _find_swings(scoped, window=swing_window)
    events = []

    for swing in swings:
        for break_idx in range(swing["index"] + 1, len(scoped)):
            breaker = scoped[break_idx]
            if swing["kind"] == "high" and breaker["close"] > swing["price"]:
                events.append(
                    {
                        "direction": "bullish",
                        "break_index": offset + break_idx,
                        "break_time": breaker["time"],
                        "reference_index": offset + swing["index"],
                        "reference_time": swing["time"],
                        "broken_level": swing["price"],
                    }
                )
                break
            if swing["kind"] == "low" and breaker["close"] < swing["price"]:
                events.append(
                    {
                        "direction": "bearish",
                        "break_index": offset + break_idx,
                        "break_time": breaker["time"],
                        "reference_index": offset + swing["index"],
                        "reference_time": swing["time"],
                        "broken_level": swing["price"],
                    }
                )
                break

    deduped = {}
    for event in events:
        key = (event["direction"], event["reference_index"])
        existing = deduped.get(key)
        if existing is None or event["break_index"] < existing["break_index"]:
            deduped[key] = event

    return sorted(deduped.values(), key=lambda item: item["break_index"])


def _latest_choch_event(candles: list[dict], lookback: int, swing_window: int) -> Optional[dict]:
    events = _find_break_events(candles, lookback=lookback, swing_window=swing_window)
    if len(events) < 2:
        return None

    for idx in range(len(events) - 1, 0, -1):
        current = events[idx]
        previous = events[idx - 1]
        if current["direction"] != previous["direction"]:
            return {
                "time": current["break_time"],
                "direction": current["direction"],
                "broken_level": current["broken_level"],
                "reference_time": current["reference_time"],
            }

    return None


def _latest_bos_event(candles: list[dict], lookback: int, swing_window: int) -> Optional[dict]:
    events = _find_break_events(candles, lookback=lookback, swing_window=swing_window)
    if len(events) < 2:
        return None

    for idx in range(len(events) - 1, 0, -1):
        current = events[idx]
        previous = events[idx - 1]
        if current["direction"] == previous["direction"]:
            return {
                "time": current["break_time"],
                "direction": current["direction"],
                "broken_level": current["broken_level"],
                "reference_time": current["reference_time"],
            }

    return None


def _find_order_block_from_break(
    candles: list[dict],
    break_event: Optional[dict],
    search_window: int = 8,
) -> Optional[dict]:
    if not break_event:
        return None

    break_idx = break_event["break_index"]
    start = max(0, break_idx - search_window)
    impulse_slice = candles[start:break_idx]
    if not impulse_slice:
        return None

    if break_event["direction"] == "bullish":
        candidates = [
            (start + idx, candle)
            for idx, candle in enumerate(impulse_slice)
            if _is_bearish(candle)
        ]
        ob_type = "bullish"
    else:
        candidates = [
            (start + idx, candle)
            for idx, candle in enumerate(impulse_slice)
            if _is_bullish(candle)
        ]
        ob_type = "bearish"

    if not candidates:
        return None

    ob_index, ob_candle = candidates[-1]
    return {
        "type": ob_type,
        "low": ob_candle["low"],
        "high": ob_candle["high"],
        "time": ob_candle["time"],
        "broken_level": break_event["broken_level"],
        "impulse_time": candles[break_idx]["time"],
        "index": ob_index,
    }


def detect_order_block(candles: list, lookback: int = 200, macro_threshold: int = 100) -> Optional[dict]:
    if len(candles) < 8:
        return None

    macro_event = _latest_choch_event(candles, lookback=max(lookback, macro_threshold), swing_window=4)
    minor_event = _latest_choch_event(candles, lookback=lookback, swing_window=2)

    macro_ob = _find_order_block_from_break(candles, macro_event, search_window=12)
    minor_ob = _find_order_block_from_break(candles, minor_event, search_window=6)

    if macro_ob:
        macro_ob["label"] = "macro"
    if minor_ob:
        minor_ob["label"] = "minor"

    if macro_ob or minor_ob:
        return {"macro": macro_ob, "minor": minor_ob}
    return None


def detect_bos(candles: list, macro_threshold: int = 100) -> Optional[dict]:
    if len(candles) < 8:
        return None

    macro = _latest_bos_event(candles, lookback=max(macro_threshold, 120), swing_window=4)
    minor = _latest_bos_event(candles, lookback=min(len(candles), 80), swing_window=2)

    if macro:
        macro["label"] = "macro"
    if minor:
        minor["label"] = "minor"

    if macro or minor:
        return {"macro": macro, "minor": minor}
    return None


def detect_fvg(candles: list, lookback: int = 50):
    if len(candles) < 3:
        return None

    scoped = candles[-lookback:] if len(candles) > lookback else candles
    min_gap = _median_range(scoped, window=min(20, len(scoped))) * 0.15

    for idx in range(len(scoped) - 1, 1, -1):
        c0 = scoped[idx - 2]
        c1 = scoped[idx - 1]
        c2 = scoped[idx]

        up_gap = c2["low"] - c0["high"]
        if up_gap > min_gap and _body(c1) >= _median_body(scoped, 20):
            return {
                "type": "up_fvg",
                "low": c0["high"],
                "high": c2["low"],
                "base_time": c1["time"],
            }

        down_gap = c0["low"] - c2["high"]
        if down_gap > min_gap and _body(c1) >= _median_body(scoped, 20):
            return {
                "type": "down_fvg",
                "low": c2["high"],
                "high": c0["low"],
                "base_time": c1["time"],
            }

    return None


def detect_sweep(candles: list, pdh: float, pdl: float, session_levels: dict = None):
    sweeps = []
    recent = candles[-8:] if len(candles) >= 8 else candles

    for candle in recent:
        if candle["high"] > pdh and candle["close"] < pdh:
            sweeps.append("PDH sweep")
        if candle["low"] < pdl and candle["close"] > pdl:
            sweeps.append("PDL sweep")

        if session_levels:
            candle_session = candle.get("session")
            for session, levels in session_levels.items():
                if session == candle_session:
                    continue

                if levels.get("high") is not None and candle["high"] > levels["high"] and candle["close"] < levels["high"]:
                    sweeps.append(f"{session} High sweep")
                if levels.get("low") is not None and candle["low"] < levels["low"] and candle["close"] > levels["low"]:
                    sweeps.append(f"{session} Low sweep")

    unique_sweeps = sorted(set(sweeps))
    return {
        "sweeps": unique_sweeps,
        "latest": unique_sweeps[-1] if unique_sweeps else None,
    }


def detect_bullish_or_bearish_engulfing(candles: list) -> Optional[str]:
    if len(candles) < 2:
        return None

    prev = candles[-2]
    curr = candles[-1]

    if _is_bearish(prev) and _is_bullish(curr):
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            return "Bullish Engulfing"

    if _is_bullish(prev) and _is_bearish(curr):
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            return "Bearish Engulfing"

    return None


def detect_trend_bias(candles: list) -> str:
    if len(candles) < 5:
        return "neutral"

    macro_choch = _latest_choch_event(candles, lookback=min(len(candles), 250), swing_window=4)
    minor_choch = _latest_choch_event(candles, lookback=min(len(candles), 120), swing_window=2)
    recent_swings = _find_swings(candles[-80:] if len(candles) > 80 else candles, window=2)

    highs = [s for s in recent_swings if s["kind"] == "high"]
    lows = [s for s in recent_swings if s["kind"] == "low"]

    bullish_points = 0
    bearish_points = 0

    if len(highs) >= 2:
        if highs[-1]["price"] > highs[-2]["price"]:
            bullish_points += 1
        else:
            bearish_points += 1

    if len(lows) >= 2:
        if lows[-1]["price"] > lows[-2]["price"]:
            bullish_points += 1
        else:
            bearish_points += 1

    close_now = candles[-1]["close"]
    close_then = candles[-5]["close"]
    if close_now > close_then:
        bullish_points += 1
    elif close_now < close_then:
        bearish_points += 1

    if macro_choch:
        if macro_choch["direction"] == "bullish":
            bullish_points += 2
        else:
            bearish_points += 2

    if minor_choch:
        if minor_choch["direction"] == "bullish":
            bullish_points += 1
        else:
            bearish_points += 1

    if bullish_points > bearish_points:
        return "bullish"
    if bearish_points > bullish_points:
        return "bearish"
    return "neutral"


def _bullish_entry_signal(m5: list[dict]) -> bool:
    if len(m5) < 3:
        return False

    last = m5[-1]
    prev = m5[-2]
    med_body = _median_body(m5, 12)
    return (
        _is_bullish(last)
        and _close_position(last) >= 0.7
        and _body(last) >= med_body
        and last["close"] > prev["high"]
    )


def _bearish_entry_signal(m5: list[dict]) -> bool:
    if len(m5) < 3:
        return False

    last = m5[-1]
    prev = m5[-2]
    med_body = _median_body(m5, 12)
    return (
        _is_bearish(last)
        and _close_position(last) <= 0.3
        and _body(last) >= med_body
        and last["close"] < prev["low"]
    )


def detect_ltf_entry(m15: list, m5: list, pdh: float, pdl: float, session_levels: dict) -> dict:
    if len(m15) < 5 or len(m5) < 5:
        return {
            "entry_type": "none",
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "notes": "Insufficient candles for LTF entry detection",
        }

    trend_bias = detect_trend_bias(m15)
    sweeps = detect_sweep(m15, pdh, pdl, session_levels).get("sweeps", [])
    last = m5[-1]
    recent_low = min(c["low"] for c in m5[-5:])
    recent_high = max(c["high"] for c in m5[-5:])
    risk_buffer = _median_range(m5, 12) * 0.15

    bullish_liquidity = any("PDL" in item or "Low sweep" in item for item in sweeps)
    bearish_liquidity = any("PDH" in item or "High sweep" in item for item in sweeps)

    if trend_bias != "bearish" and bullish_liquidity and _bullish_entry_signal(m5):
        stop_loss = recent_low - risk_buffer
        risk = max(last["close"] - stop_loss, _median_range(m5, 12) * 0.5)
        return {
            "entry_type": "bullish",
            "entry_price": last["close"],
            "stop_loss": stop_loss,
            "take_profit": last["close"] + (risk * 2.0),
            "notes": "Bullish displacement after liquidity sweep and higher-timeframe support",
        }

    if trend_bias != "bullish" and bearish_liquidity and _bearish_entry_signal(m5):
        stop_loss = recent_high + risk_buffer
        risk = max(stop_loss - last["close"], _median_range(m5, 12) * 0.5)
        return {
            "entry_type": "bearish",
            "entry_price": last["close"],
            "stop_loss": stop_loss,
            "take_profit": last["close"] - (risk * 2.0),
            "notes": "Bearish displacement after liquidity sweep and higher-timeframe resistance",
        }

    return {
        "entry_type": "none",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "notes": "No valid LTF entry confluence",
    }


def detect_choch(candles: list, macro_threshold: int = 100) -> Optional[dict]:
    if len(candles) < 8:
        return None

    macro = _latest_choch_event(candles, lookback=max(macro_threshold, 120), swing_window=4)
    minor = _latest_choch_event(candles, lookback=min(len(candles), 80), swing_window=2)

    if macro:
        macro["label"] = "macro"
    if minor:
        minor["label"] = "minor"

    if macro or minor:
        return {"macro": macro, "minor": minor}
    return None


def _direction_matches_structure(item: Optional[dict], direction: str) -> bool:
    if not item or direction not in {"bullish", "bearish"}:
        return False

    item_direction = item.get("direction") or item.get("type")
    if item_direction in {"up_fvg", "down_fvg"}:
        item_direction = "bullish" if item_direction == "up_fvg" else "bearish"

    return item_direction == direction


def _sweep_matches_direction(sweep: Optional[dict], direction: str) -> bool:
    if not sweep or direction not in {"bullish", "bearish"}:
        return False

    names = sweep.get("sweeps", [])
    if direction == "bullish":
        return any("PDL" in name or "Low sweep" in name for name in names)
    return any("PDH" in name or "High sweep" in name for name in names)


def _candle_matches_direction(candle: Optional[dict], direction: str) -> bool:
    if not candle or direction not in {"bullish", "bearish"}:
        return False

    candle_type = candle.get("type")
    if direction == "bullish":
        return candle_type in {"Bullish Engulfing", "Bullish Rejection"}
    return candle_type in {"Bearish Engulfing", "Bearish Rejection"}


def score_confluence(htf_bias: str, checklist: dict, ltf_entry: Optional[dict]) -> dict:
    direction = "neutral"
    if ltf_entry and ltf_entry.get("entry_type") in {"bullish", "bearish"}:
        direction = ltf_entry["entry_type"]
    elif htf_bias in {"bullish", "bearish"}:
        direction = htf_bias

    weights = {
        "macro_choch": 15,
        "minor_choch": 10,
        "macro_ob": 12,
        "minor_ob": 8,
        "fvg": 15,
        "sweep": 20,
        "candle_confirm": 20,
    }

    components = {
        "macro_choch": {
            "present": _direction_matches_structure((checklist.get("CHOCH") or {}).get("macro"), direction),
            "weight": weights["macro_choch"],
        },
        "minor_choch": {
            "present": _direction_matches_structure((checklist.get("CHOCH") or {}).get("minor"), direction),
            "weight": weights["minor_choch"],
        },
        "macro_ob": {
            "present": _direction_matches_structure((checklist.get("OB") or {}).get("macro"), direction),
            "weight": weights["macro_ob"],
        },
        "minor_ob": {
            "present": _direction_matches_structure((checklist.get("OB") or {}).get("minor"), direction),
            "weight": weights["minor_ob"],
        },
        "fvg": {
            "present": _direction_matches_structure(checklist.get("FVG"), direction),
            "weight": weights["fvg"],
        },
        "sweep": {
            "present": _sweep_matches_direction(checklist.get("Sweep"), direction),
            "weight": weights["sweep"],
        },
        "candle_confirm": {
            "present": _candle_matches_direction(checklist.get("Candle"), direction),
            "weight": weights["candle_confirm"],
        },
    }

    score = sum(
        component["weight"]
        for component in components.values()
        if component["present"]
    )

    threshold = 70
    eligible = (
        direction in {"bullish", "bearish"}
        and score >= threshold
        and bool(ltf_entry)
        and ltf_entry.get("entry_type") == direction
    )

    present_components = [
        key
        for key, component in components.items()
        if component["present"]
    ]

    if direction == "neutral":
        summary = "No directional confluence yet."
    elif eligible:
        summary = f"{direction.title()} setup meets confluence threshold."
    else:
        summary = f"{direction.title()} setup is below confluence threshold."

    return {
        "direction": direction,
        "score": score,
        "threshold": threshold,
        "eligible": eligible,
        "components": components,
        "present_components": present_components,
        "summary": summary,
        "htf_bias_aligned": direction == htf_bias if direction in {"bullish", "bearish"} else False,
    }
