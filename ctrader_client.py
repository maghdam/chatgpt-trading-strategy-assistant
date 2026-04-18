# ctrader_client.py

import calendar
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAmendOrderReq,
    ProtoOAAmendPositionSLTPReq,
    ProtoOAApplicationAuthReq,
    ProtoOAGetTrendbarsReq,
    ProtoOANewOrderReq,
    ProtoOAReconcileReq,
    ProtoOASymbolsListReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)
from dotenv import load_dotenv
from twisted.internet import reactor

load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))
HOST_TYPE = (os.getenv("CTRADER_HOST_TYPE") or "demo").lower()
VOLUME_UNITS_PER_LOT = 10_000_000

host = EndPoints.PROTOBUF_LIVE_HOST if HOST_TYPE == "live" else EndPoints.PROTOBUF_DEMO_HOST
client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)

symbol_map: dict[int, str] = {}
symbol_name_to_id: dict[str, int] = {}
symbol_digits_map: dict[int, int] = {}


def on_error(failure):
    print("[ERROR]", failure)


def symbols_response_cb(res):
    global symbol_map, symbol_name_to_id, symbol_digits_map
    symbol_map.clear()
    symbol_name_to_id.clear()
    symbol_digits_map.clear()

    symbols = Protobuf.extract(res)
    for symbol in symbols.symbol:
        digits = getattr(symbol, "digits", getattr(symbol, "pipPosition", 5))
        symbol_map[symbol.symbolId] = symbol.symbolName
        symbol_name_to_id[symbol.symbolName.upper()] = symbol.symbolId
        symbol_digits_map[symbol.symbolId] = digits

    print(f"[DEBUG] Loaded {len(symbol_map)} symbols.")


def account_auth_cb(_):
    req = ProtoOASymbolsListReq(
        ctidTraderAccountId=ACCOUNT_ID,
        includeArchivedSymbols=False,
    )
    client.send(req).addCallbacks(symbols_response_cb, on_error)


def app_auth_cb(_):
    req = ProtoOAAccountAuthReq(
        ctidTraderAccountId=ACCOUNT_ID,
        accessToken=ACCESS_TOKEN,
    )
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


def _trendbar_to_candle(trendbar):
    ts = datetime.fromtimestamp(trendbar.utcTimestampInMinutes * 60, timezone.utc)
    return {
        "time": ts.isoformat(),
        "open": (trendbar.low + trendbar.deltaOpen) / 100_000,
        "high": (trendbar.low + trendbar.deltaHigh) / 100_000,
        "low": trendbar.low / 100_000,
        "close": (trendbar.low + trendbar.deltaClose) / 100_000,
        "volume": trendbar.volume,
    }


def get_ohlc_data(symbol: str, tf: str = "D1", n: int = 10):
    sid = symbol_name_to_id.get(symbol.upper())
    if sid is None:
        raise ValueError(f"Unknown symbol '{symbol}'")

    now = datetime.utcnow()
    req = ProtoOAGetTrendbarsReq(
        symbolId=sid,
        ctidTraderAccountId=ACCOUNT_ID,
        period=getattr(ProtoOATrendbarPeriod, tf),
        fromTimestamp=int(calendar.timegm((now - timedelta(weeks=52)).utctimetuple())) * 1000,
        toTimestamp=int(calendar.timegm(now.utctimetuple())) * 1000,
    )

    ready = threading.Event()
    box = {}

    def callback(response):
        box["candles"] = [_trendbar_to_candle(tb) for tb in Protobuf.extract(response).trendbar]
        ready.set()

    def errback(failure):
        box["error"] = failure
        ready.set()
        return failure

    client.send(req).addCallbacks(callback, errback)
    if not ready.wait(10):
        raise TimeoutError(f"Timed out fetching {tf} candles for {symbol}.")
    if "error" in box:
        raise RuntimeError(str(box["error"]))

    candles = box.get("candles", [])[-n:]
    highs = [bar["high"] for bar in candles]
    lows = [bar["low"] for bar in candles]
    closes = [bar["close"] for bar in candles]

    context_levels = {}
    if len(candles) >= 2:
        context_levels = {
            "today_high": candles[-1]["high"],
            "today_low": candles[-1]["low"],
            "prev_day_high": candles[-2]["high"],
            "prev_day_low": candles[-2]["low"],
            "range_high_5": max(highs[-5:]),
            "range_low_5": min(lows[-5:]),
        }

    trend_strength = {}
    if tf in ("D1", "H4") and len(closes) >= 5:
        x = np.arange(len(closes))
        slope, _ = np.polyfit(x, closes, 1)
        correlation = np.corrcoef(x, closes)[0, 1]
        trend_strength = {
            "slope": float(slope),
            "correlation": float(correlation),
            "confidence": (
                "Ultra Strong Bullish"
                if slope > 0.5 and correlation > 0.9
                else "Strong Bearish"
                if slope < -0.5 and correlation > 0.9
                else "Sideways/Neutral"
            ),
        }

    return {
        "candles": candles,
        "context": context_levels,
        "trend": trend_strength,
    }


def get_open_positions():
    ready = threading.Event()
    box = {"positions": []}

    def callback(response):
        rec = Protobuf.extract(response)
        positions = []
        for position in rec.position:
            trade_data = position.tradeData
            timestamp_ms = (
                getattr(position, "utcLastUpdateTimestamp", None)
                or getattr(position, "lastUpdateTimestamp", None)
                or getattr(trade_data, "utcTimestamp", None)
            )
            timestamp_utc = None
            timestamp_local = None
            if timestamp_ms:
                dt_utc = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
                timestamp_utc = dt_utc.isoformat()
                timestamp_local = dt_utc.astimezone().isoformat()

            positions.append(
                {
                    "symbol_name": symbol_map.get(trade_data.symbolId, str(trade_data.symbolId)),
                    "position_id": position.positionId,
                    "direction": "buy" if trade_data.tradeSide == ProtoOATradeSide.BUY else "sell",
                    "entry_price": getattr(position, "price", 0),
                    "stop_loss": getattr(position, "stopLoss", None),
                    "take_profit": getattr(position, "takeProfit", None),
                    "volume_lots": trade_data.volume / VOLUME_UNITS_PER_LOT,
                    "volume_units": trade_data.volume,
                    "unrealized_profit": (
                        getattr(position, "netUnrealizedPnL", None)
                        or getattr(position, "grossUnrealizedPnL", None)
                    ),
                    "timestamp_utc": timestamp_utc,
                    "timestamp_local": timestamp_local,
                }
            )

        box["positions"] = positions
        ready.set()

    def errback(failure):
        box["error"] = failure
        ready.set()
        return failure

    req = ProtoOAReconcileReq(ctidTraderAccountId=ACCOUNT_ID)
    client.send(req).addCallbacks(callback, errback)
    if not ready.wait(5):
        raise TimeoutError("Timed out waiting for open positions.")
    if "error" in box:
        raise RuntimeError(str(box["error"]))
    return box["positions"]


def is_forex_symbol(symbol: str) -> bool:
    return symbol.upper() in {
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "AUDUSD",
        "NZDUSD",
        "USDCHF",
        "USDCAD",
        "EURJPY",
        "EURGBP",
        "GBPJPY",
    }


def place_order(
    *,
    client,
    account_id,
    symbol_id,
    order_type,
    side,
    volume,
    price=None,
    stop_loss=None,
    take_profit=None,
    client_msg_id=None,
):
    req = ProtoOANewOrderReq(
        ctidTraderAccountId=account_id,
        symbolId=symbol_id,
        orderType=ProtoOAOrderType.Value(order_type.upper()),
        tradeSide=ProtoOATradeSide.Value(side.upper()),
        volume=int(volume),
    )

    if order_type.upper() == "LIMIT":
        if price is None:
            raise ValueError("Limit order requires price.")
        req.limitPrice = float(price)
    elif order_type.upper() == "STOP":
        if price is None:
            raise ValueError("Stop order requires price.")
        req.stopPrice = float(price)

    if order_type.upper() in ("LIMIT", "STOP"):
        if stop_loss is not None:
            req.stopLoss = float(stop_loss)
        if take_profit is not None:
            req.takeProfit = float(take_profit)

    print(
        f"[DEBUG] Sending order: {order_type=} {side=} "
        f"price={price} SL={stop_loss} TP={take_profit}"
    )
    deferred = client.send(req, client_msg_id=client_msg_id, timeout=12)

    if order_type.upper() == "MARKET":
        def delayed_sltp(_):
            time.sleep(8)
            for position in get_open_positions():
                if (
                    position["symbol_name"].upper() == symbol_map[symbol_id].upper()
                    and position["direction"].upper() == side.upper()
                ):
                    return modify_position_sltp(
                        client=client,
                        account_id=account_id,
                        position_id=position["position_id"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
            return {"status": "position_not_found"}

        deferred.addCallback(delayed_sltp)

    return deferred


def modify_position_sltp(client, account_id, position_id, stop_loss=None, take_profit=None):
    req = ProtoOAAmendPositionSLTPReq(
        ctidTraderAccountId=account_id,
        positionId=position_id,
    )
    if stop_loss is not None:
        req.stopLoss = stop_loss
    if take_profit is not None:
        req.takeProfit = take_profit
    return client.send(req)


def modify_pending_order_sltp(client, account_id, order_id, version, stop_loss=None, take_profit=None):
    req = ProtoOAAmendOrderReq(
        ctidTraderAccountId=account_id,
        orderId=order_id,
        version=version,
    )
    if stop_loss is not None:
        req.stopLoss = stop_loss
    if take_profit is not None:
        req.takeProfit = take_profit
    return client.send(req)


def wait_for_deferred(d, timeout=10):
    evt = threading.Event()
    box = {}

    def callback(result):
        box["result"] = result
        evt.set()
        return result

    def errback(failure):
        box["failure"] = failure
        evt.set()
        return failure

    d.addCallbacks(callback, errback)
    if not evt.wait(timeout):
        return {"status": "failed", "error": f"Timed out after {timeout} seconds."}

    if "failure" in box:
        return {"status": "failed", "error": str(box["failure"])}

    return box.get("result")


def get_pending_orders():
    ready = threading.Event()
    box = {"orders": []}

    def callback(response):
        res = Protobuf.extract(response)
        orders = []

        for order in res.order:
            order_type = "LIMIT" if order.orderType == ProtoOAOrderType.LIMIT else "STOP"
            direction = "buy" if order.tradeData.tradeSide == ProtoOATradeSide.BUY else "sell"

            entry_price = None
            if hasattr(order, "limitPrice"):
                entry_price = float(order.limitPrice)
            elif hasattr(order, "stopPrice"):
                entry_price = float(order.stopPrice)

            timestamp_ms = getattr(order, "orderTimestamp", None) or getattr(order, "lastUpdateTimestamp", 0)
            creation_time = None
            if timestamp_ms:
                creation_time = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()

            orders.append(
                {
                    "order_id": order.orderId,
                    "symbol_id": order.tradeData.symbolId,
                    "symbol_name": symbol_map.get(order.tradeData.symbolId, str(order.tradeData.symbolId)),
                    "direction": direction,
                    "order_type": order_type,
                    "entry_price": entry_price,
                    "stop_loss": getattr(order, "stopLoss", None),
                    "take_profit": getattr(order, "takeProfit", None),
                    "volume": order.tradeData.volume,
                    "creation_time": creation_time,
                }
            )

        box["orders"] = orders
        ready.set()

    def errback(failure):
        box["error"] = failure
        ready.set()
        return failure

    req = ProtoOAReconcileReq(ctidTraderAccountId=ACCOUNT_ID)
    client.send(req).addCallbacks(callback, errback)
    if not ready.wait(12):
        raise TimeoutError("Timed out waiting for pending orders.")
    if "error" in box:
        raise RuntimeError(str(box["error"]))
    return {"orders": box["orders"]}
