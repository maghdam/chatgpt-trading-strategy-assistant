# app.py
import base64
import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from notion_client import Client as NotionClient
from pydantic import BaseModel, Field
from twisted.internet import reactor

from analysis import (
    compute_session_levels,
    detect_bos,
    detect_bullish_or_bearish_engulfing,
    detect_choch,
    detect_fvg,
    detect_ltf_entry,
    detect_order_block,
    detect_sweep,
    detect_trend_bias,
    score_confluence,
    tag_sessions_local,
)
from charts import generate_smc_chart
from ctrader_client import (
    ACCOUNT_ID,
    VOLUME_UNITS_PER_LOT,
    client,
    get_ohlc_data,
    get_open_positions,
    get_pending_orders,
    init_client,
    place_order,
    symbol_name_to_id,
    wait_for_deferred,
)

app = FastAPI()

load_dotenv()

NOTION_SECRET = os.getenv("NOTION_SECRET")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
notion = NotionClient(auth=NOTION_SECRET)


@app.on_event("startup")
async def start_ctrader():
    """Spin up the cTrader Open API client once per worker."""
    if not reactor.running:
        threading.Thread(target=init_client, daemon=True).start()


@app.on_event("shutdown")
async def stop_ctrader():
    if reactor.running:
        reactor.stop()


class Candle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class SessionCandle(Candle):
    session: Literal["Asia", "London", "NewYork", "PostNY", "Unknown"]


class CandleList(BaseModel):
    candles: List[Candle]


class FetchDataRequest(BaseModel):
    symbol: str
    timeframe: Optional[str] = "M5"
    num_bars: Optional[int] = 500
    return_chart: Optional[bool] = False


class JournalEntry(BaseModel):
    title: str
    symbol: str
    session: str
    htf_bias: str
    entry_type: str
    entry_price: float
    stop_loss: float
    target_price: float
    order_type: str
    note: str = ""
    checklist: str = ""
    news_events: str = ""
    chart_url: str = ""
    status: Optional[str] = "Pending"


class PlaceOrderRequest(BaseModel):
    symbol: str
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    direction: Literal["BUY", "SELL"]
    volume: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class LTFEntry(BaseModel):
    entry_type: Optional[str]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    notes: Optional[str] = ""


class AnalyzeRequest(BaseModel):
    symbol: str


class MTFZones(BaseModel):
    H4_Macro_OB: Optional[dict] = None
    H4_Minor_OB: Optional[dict] = None
    H1_Macro_OB: Optional[dict] = None
    H1_Minor_OB: Optional[dict] = None
    H4_BOS: Optional[dict] = None
    H1_BOS: Optional[dict] = None
    H4_FVG: Optional[dict] = None
    H1_FVG: Optional[dict] = None


class CHOCHModel(BaseModel):
    macro: Optional[dict] = None
    minor: Optional[dict] = None


class BOSModel(BaseModel):
    macro: Optional[dict] = None
    minor: Optional[dict] = None


class OBModel(BaseModel):
    macro: Optional[dict] = None
    minor: Optional[dict] = None


class Checklist(BaseModel):
    CHOCH: CHOCHModel = Field(default_factory=CHOCHModel)
    BOS: BOSModel = Field(default_factory=BOSModel)
    OB: OBModel = Field(default_factory=OBModel)
    FVG: Optional[dict] = None
    Sweep: Optional[dict] = None
    Candle: Optional[dict] = None


class ConfluenceModel(BaseModel):
    direction: str
    score: int
    threshold: int
    eligible: bool
    components: dict
    present_components: List[str]
    summary: str
    htf_bias_aligned: bool


class AnalyzeResponse(BaseModel):
    HTF_Bias: str
    MTF_Zones: MTFZones
    LTF_Entry: Optional[LTFEntry]
    Previous_Day_High: float
    Previous_Day_Low: float
    Session_Levels: dict
    Checklist: Checklist
    Confluence: ConfluenceModel
    News: str


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


def wait_until_symbols_loaded(timeout: int = 10) -> bool:
    for _ in range(timeout * 10):
        if symbol_name_to_id:
            return True
        time.sleep(0.1)
    return False


def volume_to_units(volume: float) -> int:
    if volume <= 0:
        raise HTTPException(status_code=400, detail="Volume must be greater than 0.")

    # The documented request contract uses lots (for example 1.0), but keep
    # compatibility for callers already sending native units as a large integer.
    if volume >= 1000 and float(volume).is_integer():
        return int(volume)

    return int(round(volume * VOLUME_UNITS_PER_LOT))


def validate_order_request(order: PlaceOrderRequest) -> None:
    if order.order_type in {"LIMIT", "STOP"} and order.entry_price is None:
        raise HTTPException(
            status_code=400,
            detail=f"{order.order_type} orders require entry_price.",
        )

    if order.order_type == "MARKET" and order.entry_price is not None:
        raise HTTPException(
            status_code=400,
            detail="MARKET orders must not include entry_price.",
        )


def build_chart_highlights(
    candles: list,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> dict:
    return {
        "order_block": detect_order_block(candles),
        "fvg": detect_fvg(candles),
        "choch": detect_choch(candles),
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


@app.post("/tag-sessions")
async def tag_sessions(data: CandleList):
    try:
        return [
            SessionCandle(**c.dict(), session=label_session(c.time))
            for c in data.candles
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session-levels")
async def session_levels(data: CandleList):
    try:
        session_groups = defaultdict(list)
        for candle in data.candles:
            session_groups[label_session(candle.time)].append(candle)

        levels = {}
        for session, candles in session_groups.items():
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            levels[session] = {
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
            }

        return levels
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {
        "symbols_loaded": len(symbol_name_to_id),
        "connected": getattr(client, "connected", False),
    }


@app.post("/journal-entry")
async def journal_entry(entry: JournalEntry):
    try:
        status = "Open" if entry.order_type.upper() == "MARKET" else "Pending"

        properties = {
            "Title": {"title": [{"text": {"content": entry.title}}]},
            "Date": {"date": {"start": datetime.utcnow().isoformat()}},
            "Symbol": {"rich_text": [{"text": {"content": entry.symbol}}]},
            "Session": {"rich_text": [{"text": {"content": entry.session}}]},
            "HTF Bias": {"rich_text": [{"text": {"content": entry.htf_bias}}]},
            "Entry Type": {"rich_text": [{"text": {"content": entry.entry_type}}]},
            "Entry Price": {"number": entry.entry_price},
            "Stop Loss": {"number": entry.stop_loss},
            "Target Price": {"number": entry.target_price},
            "Order Type": {"rich_text": [{"text": {"content": entry.order_type}}]},
            "Status": {"rich_text": [{"text": {"content": status}}]},
            "Note": {"rich_text": [{"text": {"content": entry.note}}]},
            "Checklist": {"rich_text": [{"text": {"content": entry.checklist}}]},
            "News & Events": {"rich_text": [{"text": {"content": entry.news_events}}]},
        }

        if entry.chart_url:
            properties["Files & media"] = {
                "files": [
                    {
                        "name": "Chart",
                        "external": {"url": entry.chart_url},
                    }
                ]
            }

        notion.pages.create(parent={"database_id": NOTION_DB_ID}, properties=properties)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fetch-data")
async def fetch_data(req: FetchDataRequest):
    try:
        symbol_key = req.symbol.upper()
        if symbol_key not in symbol_name_to_id:
            raise HTTPException(status_code=404, detail=f"Symbol '{req.symbol}' not found")

        if req.num_bars == 500:
            tf = req.timeframe.upper()
            req.num_bars = {
                "M1": 1500,
                "M5": 500,
                "M15": 500,
                "M30": 500,
                "H1": 500,
                "H4": 500,
                "D1": 300,
                "W1": 100,
            }.get(tf, 500)

        result = get_ohlc_data(req.symbol, req.timeframe, req.num_bars)
        response = {
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "ohlc": result["candles"],
            "context": result.get("context", {}),
            "trend": result.get("trend", {}),
        }

        if req.return_chart:
            image_bytes = generate_smc_chart(
                result["candles"],
                title=f"{req.symbol.upper()} SMC Chart - {req.timeframe.upper()}",
                highlights=build_chart_highlights(result["candles"]),
            )
            response["chart_image_base64"] = base64.b64encode(image_bytes).decode("ascii")

        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/open-positions")
async def open_positions():
    try:
        positions = get_open_positions()
        return {"positions": positions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/place-order")
def place_order_main(order: PlaceOrderRequest):
    try:
        validate_order_request(order)

        if not wait_until_symbols_loaded():
            raise HTTPException(status_code=503, detail="Symbols not loaded yet. Try again shortly.")

        symbol_key = order.symbol.upper()
        if symbol_key not in symbol_name_to_id:
            raise HTTPException(status_code=404, detail=f"Symbol '{order.symbol}' not found.")

        symbol_id = symbol_name_to_id[symbol_key]
        print(f"[ORDER DEBUG] Sending order: {order=}, {symbol_id=}")

        deferred = place_order(
            client=client,
            account_id=ACCOUNT_ID,
            symbol_id=symbol_id,
            order_type=order.order_type,
            side=order.direction,
            volume=volume_to_units(order.volume),
            price=order.entry_price if order.order_type != "MARKET" else None,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )

        result = wait_for_deferred(deferred, timeout=12)
        if isinstance(result, str):
            result = {"message": result}
        elif not isinstance(result, dict):
            result = {"result": str(result)}

        return {"status": "success", "order_id": int(time.time()), "details": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed placing order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/placeOrder")
def place_order_alias(order: PlaceOrderRequest):
    return place_order_main(order)


@app.get("/pending-orders")
async def pending_orders():
    try:
        return get_pending_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    try:
        symbol = req.symbol
        timeframes = ["D1", "H4", "H1", "M15", "M5"]
        data = {}
        bar_depth = {
            "D1": 750,
            "H4": 1200,
            "H1": 1200,
            "M15": 500,
            "M5": 300,
        }

        for tf in timeframes:
            result = get_ohlc_data(symbol, tf, n=bar_depth.get(tf, 100))
            if not isinstance(result, dict) or "candles" not in result:
                raise HTTPException(status_code=500, detail=f"Failed to fetch candles for {tf}")
            data[tf] = result

        candles = {tf: data[tf]["candles"] for tf in timeframes}
        tagged_m15 = tag_sessions_local(candles["M15"])
        pdh = candles["D1"][-2]["high"]
        pdl = candles["D1"][-2]["low"]
        session_levels = compute_session_levels(tagged_m15)
        htf_bias = detect_trend_bias(candles["D1"])

        h4_ob_data = detect_order_block(candles["H4"], lookback=200, macro_threshold=100)
        h1_ob_data = detect_order_block(candles["H1"], lookback=200, macro_threshold=100)
        h4_bos_data = detect_bos(candles["H4"], macro_threshold=100)
        h1_bos_data = detect_bos(candles["H1"], macro_threshold=100)
        mtf_zones = {
            "H4_Macro_OB": h4_ob_data.get("macro") if h4_ob_data else None,
            "H4_Minor_OB": h4_ob_data.get("minor") if h4_ob_data else None,
            "H1_Macro_OB": h1_ob_data.get("macro") if h1_ob_data else None,
            "H1_Minor_OB": h1_ob_data.get("minor") if h1_ob_data else None,
            "H4_BOS": h4_bos_data,
            "H1_BOS": h1_bos_data,
            "H4_FVG": detect_fvg(candles["H4"]),
            "H1_FVG": detect_fvg(candles["H1"]),
        }

        ltf_entry = detect_ltf_entry(tagged_m15, candles["M5"], pdh, pdl, session_levels)
        raw_candle = detect_bullish_or_bearish_engulfing(candles["M5"])
        candle_dict = {"type": raw_candle} if isinstance(raw_candle, str) else raw_candle

        m15_ob_data = detect_order_block(candles["M15"], lookback=200, macro_threshold=100)
        m15_bos_data = detect_bos(candles["M15"], macro_threshold=100)
        m5_choch_data = detect_choch(candles["M5"], macro_threshold=100)
        checklist = {
            "CHOCH": {
                "macro": m5_choch_data.get("macro") if m5_choch_data else None,
                "minor": m5_choch_data.get("minor") if m5_choch_data else None,
            },
            "BOS": {
                "macro": m15_bos_data.get("macro") if m15_bos_data else None,
                "minor": m15_bos_data.get("minor") if m15_bos_data else None,
            },
            "OB": {
                "macro": m15_ob_data.get("macro") if m15_ob_data else None,
                "minor": m15_ob_data.get("minor") if m15_ob_data else None,
            },
            "FVG": detect_fvg(candles["M15"]),
            "Sweep": detect_sweep(tagged_m15, pdh, pdl, session_levels),
            "Candle": candle_dict,
        }
        confluence = score_confluence(htf_bias, checklist, ltf_entry)

        news = ""

        try:
            print("HTF Bias:", htf_bias)
            print("MTF Zones:", mtf_zones)
            print("LTF Entry Raw:", repr(ltf_entry))
            print("Checklist Raw:", repr(checklist))
            print("Confluence:", repr(confluence))

            response = AnalyzeResponse(
                HTF_Bias=htf_bias,
                MTF_Zones=MTFZones(**mtf_zones),
                LTF_Entry=LTFEntry(**ltf_entry),
                Previous_Day_High=pdh,
                Previous_Day_Low=pdl,
                Session_Levels=session_levels,
                Checklist=Checklist(**checklist),
                Confluence=ConfluenceModel(**confluence),
                News=news,
            )
            print("Final response created.")
            return response
        except Exception as e:
            print("Exception while constructing AnalyzeResponse:", e)
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chart")
async def chart(
    symbol: str,
    timeframe: str = "M15",
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
):
    try:
        candles_data = get_ohlc_data(symbol, timeframe, n=100)
        candles = candles_data["candles"]
        image_bytes = generate_smc_chart(
            candles,
            title=f"{symbol} SMC Chart - {timeframe}",
            highlights=build_chart_highlights(candles, entry, stop_loss, take_profit),
        )
        return Response(content=image_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000)
