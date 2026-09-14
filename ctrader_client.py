# ctrader_client.py

import calendar
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAAmendOrderReq,
    ProtoOAAmendPositionSLTPReq,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOANewOrderReq,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)
from dotenv import load_dotenv
from twisted.internet import reactor

load_dotenv()

logger = logging.getLogger(__name__)

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

_symbol_state_lock = threading.Lock()
_symbols_ready = threading.Event()
_symbol_load_error: Optional[str] = None


class CTraderUnavailableError(RuntimeError):
    """Raised when cTrader authentication or catalogue loading is unavailable."""


def _describe_response(payload) -> str:
    details = [type(payload).__name__]
    error_code = getattr(payload, "errorCode", None)
    description = getattr(payload, "description", None)
    if error_code:
        details.append(f"errorCode={error_code}")
    if description:
        details.append(f"description={description}")
    return ", ".join(details)


def _extract_expected(res, expected_type, stage: str):
    payload = Protobuf.extract(res)
    if not isinstance(payload, expected_type):
        raise CTraderUnavailableError(
            f"{stage} returned {_describe_response(payload)}; "
            f"expected {expected_type.__name__}. Verify the cTrader access token, "
            f"account ID, and {HOST_TYPE} environment."
        )
    return payload


def _reset_symbol_state() -> None:
    global _symbol_load_error
    with _symbol_state_lock:
        symbol_map.clear()
        symbol_name_to_id.clear()
        symbol_digits_map.clear()
        _symbol_load_error = None
        _symbols_ready.clear()


def _set_symbol_error(message: str) -> None:
    global _symbol_load_error
    with _symbol_state_lock:
        symbol_map.clear()
        symbol_name_to_id.clear()
        symbol_digits_map.clear()
        _symbol_load_error = message
        _symbols_ready.set()
    logger.error("[cTrader] %s", message)


def get_symbol_status() -> dict:
    with _symbol_state_lock:
        return {
            "ready": _symbols_ready.is_set()
            and _symbol_load_error is None
            and bool(symbol_name_to_id),
            "symbols_loaded": len(symbol_name_to_id),
            "error": _symbol_load_error,
        }


def wait_for_symbols(timeout: float = 10) -> None:
    if not _symbols_ready.wait(timeout):
        raise CTraderUnavailableError(
            "cTrader symbol catalogue is still loading. Try again shortly."
        )

    with _symbol_state_lock:
        if _symbol_load_error:
            raise CTraderUnavailableError(_symbol_load_error)
        if not symbol_name_to_id:
            raise CTraderUnavailableError(
                "cTrader returned an empty symbol catalogue for this account."
            )


def _transport_error(stage: str):
    def errback(failure):
        get_message = getattr(failure, "getErrorMessage", None)
        reason = get_message() if callable(get_message) else str(failure)
        _set_symbol_error(f"{stage} failed: {reason}")
        return None

    return errback


def symbols_response_cb(res):
    try:
        symbols = _extract_expected(
            res,
            ProtoOASymbolsListRes,
            "Symbol catalogue request",
        )
        next_symbol_map = {}
        next_name_to_id = {}
        next_digits_map = {}

        for symbol in symbols.symbol:
            digits = getattr(symbol, "digits", getattr(symbol, "pipPosition", 5))
            next_symbol_map[symbol.symbolId] = symbol.symbolName
            next_name_to_id[symbol.symbolName.upper()] = symbol.symbolId
            next_digits_map[symbol.symbolId] = digits

        if not next_symbol_map:
            raise CTraderUnavailableError(
                "cTrader returned an empty symbol catalogue for this account."
            )
    except Exception as exc:
        _set_symbol_error(str(exc))
        return None

    global _symbol_load_error
    with _symbol_state_lock:
        symbol_map.clear()
        symbol_map.update(next_symbol_map)
        symbol_name_to_id.clear()
        symbol_name_to_id.update(next_name_to_id)
        symbol_digits_map.clear()
        symbol_digits_map.update(next_digits_map)
        _symbol_load_error = None
        _symbols_ready.set()

    logger.info("[cTrader] Loaded %s symbols.", len(next_symbol_map))
    return symbols


def account_auth_cb(res):
    try:
        response = _extract_expected(
            res,
            ProtoOAAccountAuthRes,
            "Account authentication",
        )
        if response.ctidTraderAccountId != ACCOUNT_ID:
            raise CTraderUnavailableError(
                "cTrader authenticated a different account than CTRADER_ACCOUNT_ID."
            )
    except Exception as exc:
        _set_symbol_error(str(exc))
        return None

    req = ProtoOASymbolsListReq(
        ctidTraderAccountId=ACCOUNT_ID,
        includeArchivedSymbols=False,
    )
    deferred = client.send(req)
    deferred.addCallback(symbols_response_cb)
    deferred.addErrback(_transport_error("Symbol catalogue request"))
    return deferred


def app_auth_cb(res):
    try:
        _extract_expected(
            res,
            ProtoOAApplicationAuthRes,
            "Application authentication",
        )
    except Exception as exc:
        _set_symbol_error(str(exc))
        return None

    req = ProtoOAAccountAuthReq(
        ctidTraderAccountId=ACCOUNT_ID,
        accessToken=ACCESS_TOKEN,
    )
    deferred = client.send(req)
    deferred.addCallback(account_auth_cb)
    deferred.addErrback(_transport_error("Account authentication"))
    return deferred


def connected(_):
    _reset_symbol_state()
    req = ProtoOAApplicationAuthReq(clientId=CLIENT_ID, clientSecret=CLIENT_SECRET)
    deferred = client.send(req)
    deferred.addCallback(app_auth_cb)
    deferred.addErrback(_transport_error("Application authentication"))
    return deferred


def disconnected(_, reason):
    _set_symbol_error(f"cTrader disconnected: {reason}")


def init_client():
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
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
    wait_for_symbols()
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
        payload = _extract_expected(response, ProtoOAGetTrendbarsRes, "Trendbar request")
        box["candles"] = [_trendbar_to_candle(tb) for tb in payload.trendbar]
        ready.set()

    def errback(failure):
        box["error"] = failure
        ready.set()
        return None

    deferred = client.send(req)
    deferred.addCallback(callback)
    deferred.addErrback(errback)
    if not ready.wait(10):
        raise TimeoutError(f"Timed out fetching {tf} candles for {symbol}.")
    if "error" in box:
        raise CTraderUnavailableError(str(box["error"]))

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
    wait_for_symbols()
    ready = threading.Event()
    box = {"positions": []}

    def callback(response):
        rec = _extract_expected(response, ProtoOAReconcileRes, "Reconcile request")
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
        return None

    req = ProtoOAReconcileReq(ctidTraderAccountId=ACCOUNT_ID)
    deferred = client.send(req)
    deferred.addCallback(callback)
    deferred.addErrback(errback)
    if not ready.wait(5):
        raise TimeoutError("Timed out waiting for open positions.")
    if "error" in box:
        raise CTraderUnavailableError(str(box["error"]))
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
        return None

    d.addCallback(callback)
    d.addErrback(errback)
    if not evt.wait(timeout):
        return {"status": "failed", "error": f"Timed out after {timeout} seconds."}

    if "failure" in box:
        return {"status": "failed", "error": str(box["failure"])}

    return box.get("result")


def get_pending_orders():
    wait_for_symbols()
    ready = threading.Event()
    box = {"orders": []}

    def callback(response):
        res = _extract_expected(response, ProtoOAReconcileRes, "Reconcile request")
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
        return None

    req = ProtoOAReconcileReq(ctidTraderAccountId=ACCOUNT_ID)
    deferred = client.send(req)
    deferred.addCallback(callback)
    deferred.addErrback(errback)
    if not ready.wait(12):
        raise TimeoutError("Timed out waiting for pending orders.")
    if "error" in box:
        raise CTraderUnavailableError(str(box["error"]))
    return {"orders": box["orders"]}
