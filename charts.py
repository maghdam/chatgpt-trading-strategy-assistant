from datetime import datetime
from typing import Any, Optional

import plotly.graph_objects as go


def _select_order_block(order_block: Any) -> Optional[dict]:
    if not isinstance(order_block, dict):
        return None

    if "low" in order_block and "high" in order_block:
        return order_block

    for key in ("minor", "macro"):
        candidate = order_block.get(key)
        if isinstance(candidate, dict) and "low" in candidate and "high" in candidate:
            return candidate

    return None


def _find_candle_by_time(candles: list, timestamp: Optional[str]) -> Optional[dict]:
    if not timestamp:
        return None

    for candle in candles:
        if candle["time"] == timestamp:
            return candle

    return None


def generate_smc_chart(candles: list, title: str = "SMC Chart", highlights=None) -> bytes:
    """
    Generate a Plotly candlestick chart with optional SMC highlights.
    """
    if not candles:
        raise ValueError("Cannot render a chart without candles.")

    df = {
        "time": [datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in candles],
        "open": [c["open"] for c in candles],
        "high": [c["high"] for c in candles],
        "low": [c["low"] for c in candles],
        "close": [c["close"] for c in candles],
    }

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["time"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="Price",
            )
        ]
    )

    if highlights:
        order_block = _select_order_block(highlights.get("order_block"))
        if order_block:
            fig.add_shape(
                type="rect",
                x0=df["time"][0],
                x1=df["time"][-1],
                y0=order_block["low"],
                y1=order_block["high"],
                fillcolor="rgba(0,255,0,0.2)",
                line_width=0,
            )

        fvg = highlights.get("fvg")
        if isinstance(fvg, dict) and "low" in fvg and "high" in fvg:
            fig.add_shape(
                type="rect",
                x0=df["time"][0],
                x1=df["time"][-1],
                y0=fvg["low"],
                y1=fvg["high"],
                fillcolor="rgba(255,165,0,0.3)",
                line_width=0,
            )

        choch = highlights.get("choch")
        if isinstance(choch, dict):
            choch_point = _find_candle_by_time(
                candles,
                (choch.get("minor") or choch.get("macro") or {}).get("time"),
            )
            if choch_point:
                choch_time = datetime.fromisoformat(choch_point["time"].replace("Z", "+00:00"))
                fig.add_vline(x=choch_time, line_dash="dot", line_color="red")

        if highlights.get("entry") is not None:
            fig.add_hline(y=highlights["entry"], line_color="blue")

        if highlights.get("stop_loss") is not None:
            fig.add_hline(y=highlights["stop_loss"], line_color="black")

        if highlights.get("take_profit") is not None:
            fig.add_hline(y=highlights["take_profit"], line_color="green")

    fig.update_layout(title=title, xaxis_rangeslider_visible=False)
    return fig.to_image(format="png")
