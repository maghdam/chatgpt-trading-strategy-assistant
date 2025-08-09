
# ctrader_client.py
# ---------------------------------------------------------------------------

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAAccountAuthReq,
    ProtoOASymbolsListReq,
    ProtoOAReconcileReq,
    ProtoOAGetTrendbarsReq,
    ProtoOANewOrderReq,
    ProtoOAAmendOrderReq,
    ProtoOAAmendPositionSLTPReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)
from twisted.internet import reactor
from datetime import datetime, timezone, timedelta
import calendar, time, threading, json
import os
from dotenv import load_dotenv
import numpy as np



# ── Ctrader-Openapi credentials & client ───────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))
HOST_TYPE = os.getenv("CTRADER_HOST_TYPE")


host = EndPoints.PROTOBUF_LIVE_HOST if HOST_TYPE.lower() == "live" else EndPoints.PROTOBUF_DEMO_HOST
client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)

# ── symbol maps ────────────────────────────────────────────────────────────
symbol_map        : dict[int, str] = {}   # {id: name}
symbol_name_to_id : dict[str, int] = {}   # {name.upper(): id}
symbol_digits_map : dict[int, int] = {}   # {id: digits}

# ── helpers ────────────────────────────────────────────────────────────────
def pips_to_relative(pips: int, digits: int) -> int:
    """Convert pips → 1/100 000 of price units (works for 2- to 5-digit symbols)."""
    return pips * 10 ** (6 - digits)

def on_error(failure):  # generic errback
    print("[ERROR]", failure)

# ── auth & symbol bootstrap ────────────────────────────────────────────────
def symbols_response_cb(res):
    global symbol_map, symbol_name_to_id, symbol_digits_map
    symbol_map.clear(); symbol_name_to_id.clear(); symbol_digits_map.clear()

    symbols = Protobuf.extract(res)
    for s in symbols.symbol:
        # → try `digits`, fall back to `pipPosition`, default = 5
        digits = getattr(s, "digits", getattr(s, "pipPosition", 5))

        symbol_map[s.symbolId]            = s.symbolName
        symbol_name_to_id[s.symbolName.upper()] = s.symbolId
        symbol_digits_map[s.symbolId]     = digits

    print(f"[DEBUG] Loaded {len(symbol_map)} symbols.")


# ── account‑level auth → ask for symbol list ─────────────────────────────
def account_auth_cb(_):
    req = ProtoOASymbolsListReq(
        ctidTraderAccountId=ACCOUNT_ID,
        includeArchivedSymbols=False,
    )
    # when the symbols arrive we’ll call symbols_response_cb
    client.send(req).addCallbacks(symbols_response_cb, on_error)


def app_auth_cb(_):
    req = ProtoOAAccountAuthReq(
        ctidTraderAccountId=ACCOUNT_ID,
        accessToken=ACCESS_TOKEN,
    )
    # account_auth_cb must exist in the same module
    client.send(req).addCallbacks(account_auth_cb, on_error)


def connected(_):
    req = ProtoOAApplicationAuthReq(clientId=CLIENT_ID, clientSecret=CLIENT_SECRET)
    client.send(req).addCallbacks(app_auth_cb, on_error)

def init_client():
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(lambda c, r: print("[INFO] Disconnected:", r))
    client.setMessageReceivedCallback(lambda c, m: None)
    client.startService()
    reactor.run(installSignalHandlers=False)


# ── OHLC fetch (used by /fetch-data) ───────────────────────────────────────
daily_bars, ready_event = [], threading.Event()

def _trendbars_cb(res):
    bars = Protobuf.extract(res).trendbar
    def _tb(tb):
        ts = datetime.fromtimestamp(tb.utcTimestampInMinutes * 60, timezone.utc)
        return dict(
            time   = ts.isoformat(),
            open   = (tb.low + tb.deltaOpen)   / 100_000,
            high   = (tb.low + tb.deltaHigh)   / 100_000,
            low    = tb.low                    / 100_000,
            close  = (tb.low + tb.deltaClose)  / 100_000,
            volume = tb.volume,
        )
    global daily_bars
    daily_bars = list(map(_tb, bars))[-50:]
    ready_event.set()



def get_ohlc_data(symbol: str, tf: str = "D1", n: int = 10):
    ready_event.clear()
    sid = symbol_name_to_id.get(symbol.upper())
    if sid is None:
        raise ValueError(f"Unknown symbol '{symbol}'")

    now = datetime.utcnow()
    req = ProtoOAGetTrendbarsReq(
        symbolId            = sid,
        ctidTraderAccountId = ACCOUNT_ID,
        period              = getattr(ProtoOATrendbarPeriod, tf),
        fromTimestamp       = int(calendar.timegm((now - timedelta(weeks=52)).utctimetuple())) * 1000,
        toTimestamp         = int(calendar.timegm(now.utctimetuple())) * 1000,
    )
    client.send(req).addCallbacks(_trendbars_cb, on_error)
    ready_event.wait(10)

    candles = daily_bars[-n:]
    highs = [bar["high"] for bar in candles]
    lows = [bar["low"] for bar in candles]
    closes = [bar["close"] for bar in candles]

    # Ensure we have enough for context
    context_levels = {}
    if len(candles) >= 2:
        context_levels = {
            "today_high": candles[-1]["high"],
            "today_low": candles[-1]["low"],
            "prev_day_high": candles[-2]["high"],
            "prev_day_low": candles[-2]["low"],
            "range_high_5": max(highs[-5:]),
            "range_low_5": min(lows[-5:])
        }

    # Optional HTF trend logic (D1/H4 only)
    trend_strength = {}
    if tf in ("D1", "H4") and len(closes) >= 5:
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, closes, 1)
        r = np.corrcoef(x, closes)[0, 1]
        trend_strength = {
            "slope": float(slope),
            "correlation": float(r),
            "confidence": (
                "Ultra Strong Bullish" if slope > 0.5 and r > 0.9 else
                "Strong Bearish" if slope < -0.5 and r > 0.9 else
                "Sideways/Neutral"
            )
        }

    return {
        "candles": candles,
        "context": context_levels,
        "trend": trend_strength
    }


# ── reconcile helpers ──────────────────────────────────────────────────────
open_positions, pos_ready = [], threading.Event()

def _reconcile_cb(res):
    global open_positions
    open_positions = []
    rec = Protobuf.extract(res)
    for p in rec.position:
        td = p.tradeData
        open_positions.append(
            dict(
                symbol_name = symbol_map.get(td.symbolId, str(td.symbolId)),
                position_id = p.positionId,
                direction   = "buy" if td.tradeSide == ProtoOATradeSide.BUY else "sell",
                entry_price = getattr(p, "price", 0),  # already a float like 1.17700
                volume_lots = td.volume / 10_000_000,  # 1 lot = 10 000 000
            )
        )
    pos_ready.set()

def get_open_positions():
    pos_ready.clear()
    req = ProtoOAReconcileReq(ctidTraderAccountId = ACCOUNT_ID)
    client.send(req).addCallbacks(_reconcile_cb, on_error)
    pos_ready.wait(5)
    return open_positions


def is_forex_symbol(symbol: str) -> bool:
    """Basic rule: treat majors as Forex; expand this set if needed."""
    return symbol.upper() in {
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD",
        "EURJPY", "EURGBP", "GBPJPY"
    }

# ── core: place_order ──────────────────────────────────────────────────────
def place_order(
    *, client, account_id, symbol_id,
    order_type, side, volume,
    price=None, stop_loss=None, take_profit=None,
    client_msg_id=None,
):
    # ✅ Convert lots to cTrader native units (1 lot = 100,000 for Forex)
    if volume < 1000:  # Assume input is in lots
        volume_units = int(volume * 100_000)  # 0.01 lots → 1,000 units
    else:  # Assume already in units
        volume_units = int(volume)

    req = ProtoOANewOrderReq(
        ctidTraderAccountId=account_id,
        symbolId=symbol_id,
        orderType=ProtoOAOrderType.Value(order_type.upper()),
        tradeSide=ProtoOATradeSide.Value(side.upper()),
        volume=volume_units,
    )

    # -------- Absolute prices for all order types ------------------
    if order_type.upper() == "LIMIT":
        if price is None:
            raise ValueError("Limit order requires price.")
        req.limitPrice = float(price)
    elif order_type.upper() == "STOP":
        if price is None:
            raise ValueError("Stop order requires price.")
        req.stopPrice = float(price)

    # LIMIT / STOP use absolute SL/TP
    if order_type.upper() in ("LIMIT", "STOP"):
        if stop_loss is not None:
            req.stopLoss = float(stop_loss)
        if take_profit is not None:
            req.takeProfit = float(take_profit)

    # MARKET uses absolute SL/TP as well
    elif order_type.upper() == "MARKET":
        if stop_loss is not None:
            req.stopLoss = float(stop_loss)
        if take_profit is not None:
            req.takeProfit = float(take_profit)
        print(f"[DEBUG] MARKET absolute prices: SL={stop_loss}, TP={take_profit}")

    print(
        f"[DEBUG] Sending order: {order_type=} {side=} "
        f"volume_units={volume_units} price={price} SL={stop_loss} TP={take_profit}"
    )
    d = client.send(req, client_msg_id=client_msg_id, timeout=12)

    # MARKET: delay SL/TP amendment after fill
    if order_type.upper() == "MARKET":
        def _delayed_sltp(_):
            time.sleep(8)
            open_pos = get_open_positions()
            for p in open_pos:
                if (
                    p["symbol_name"].upper() == symbol_map[symbol_id].upper()
                    and p["direction"].upper() == side.upper()
                ):
                    return modify_position_sltp(
                        client=client,
                        account_id=account_id,
                        position_id=p["position_id"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
            return {"status": "position_not_found"}
        d.addCallback(_delayed_sltp)

    return d




# ── amend helpers (rarely needed with new logic) ───────────────────────────
def modify_position_sltp(client, account_id, position_id, stop_loss=None, take_profit=None):
    req = ProtoOAAmendPositionSLTPReq(ctidTraderAccountId = account_id, positionId = position_id)
    if stop_loss   is not None: req.stopLoss   = stop_loss
    if take_profit is not None: req.takeProfit = take_profit
    return client.send(req)

def modify_pending_order_sltp(client, account_id, order_id, version, stop_loss=None, take_profit=None):
    req = ProtoOAAmendOrderReq(
        ctidTraderAccountId = account_id,
        orderId             = order_id,
        version             = version,
    )
    if stop_loss   is not None: req.stopLoss   = stop_loss
    if take_profit is not None: req.takeProfit = take_profit
    return client.send(req)

# ── blocking helper used by FastAPI layer ─────────────────────────────────
def wait_for_deferred(d, timeout=10):
    evt, box = threading.Event(), {}
    d.addCallbacks(lambda r: (box.setdefault("r", r), evt.set()), lambda f: (box.setdefault("f", f), evt.set()))
    evt.wait(timeout)
    return box.get("r") or {"status": "failed", "error": str(box.get("f"))}



def get_pending_orders():
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderType, ProtoOATradeSide

    result_ready = threading.Event()
    pending_orders = []

    def callback(response):
        res = Protobuf.extract(response)

        for o in res.order:
            order_type = "LIMIT" if o.orderType == ProtoOAOrderType.LIMIT else "STOP"
            direction = "buy" if o.tradeData.tradeSide == ProtoOATradeSide.BUY else "sell"

            entry_price = None
            if hasattr(o, "limitPrice"):
                entry_price = o.limitPrice / 100000
            elif hasattr(o, "stopPrice"):
                entry_price = o.stopPrice / 100000

            symbol_id = o.tradeData.symbolId
            timestamp_ms = getattr(o, "orderTimestamp", None) or getattr(o, "lastUpdateTimestamp", 0)
            pending_orders.append({
                "order_id": o.orderId,
                "symbol_id": symbol_id,
                "symbol_name": symbol_map.get(symbol_id, str(symbol_id)),
                "direction": direction,
                "order_type": order_type,
                "entry_price": entry_price,
                "stop_loss": getattr(o, "stopLoss", None),
                "take_profit": getattr(o, "takeProfit", None),
                "volume": o.tradeData.volume,

                "creation_time": datetime.utcfromtimestamp(timestamp_ms / 1000).isoformat()

            })

        result_ready.set()

    req = ProtoOAReconcileReq(ctidTraderAccountId=ACCOUNT_ID)
    d = client.send(req)
    d.addCallbacks(callback, on_error)
    result_ready.wait(timeout=12)

    return {"orders": pending_orders}

