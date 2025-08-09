# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel          # ←  put this line back
from typing import Optional, Literal
from notion_client import Client as NotionClient
from ctrader_client import (
    init_client,                       # still need this
    get_open_positions,
    get_ohlc_data,
    place_order,
    wait_for_deferred,
    symbol_name_to_id,
    client,
)
from twisted.internet import reactor
import threading
from ctrader_client import ACCOUNT_ID
import time
from typing import List, Literal
from datetime import datetime
from typing import List
from fastapi import  APIRouter
from ctrader_client import is_forex_symbol
import os
from dotenv import load_dotenv
from collections import defaultdict
from analysis import (
    detect_order_block,
    detect_fvg,
    detect_sweep,
    detect_bullish_or_bearish_engulfing,
    detect_trend_bias,
    detect_ltf_entry,
    detect_choch
)
from charts import generate_smc_chart
from analysis import detect_choch
from pydantic import BaseModel
from analysis import tag_sessions_local, compute_session_levels  # Add this
from fastapi.responses import Response



app = FastAPI()

# 🔌 ─────────────────────────────────────────────────────────────
@app.on_event("startup")
async def start_ctrader():
    """Spin up the cTrader Open API client once per worker."""
    if not reactor.running:                 # cheap guard
        threading.Thread(target=init_client, daemon=True).start()
# 🔌 ─────────────────────────────────────────────────────────────


# 🧠 Notion config
load_dotenv()  # ⬅️ This loads variables from .env file

NOTION_SECRET = os.getenv("NOTION_SECRET")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
notion = NotionClient(auth=NOTION_SECRET)


class Candle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int

class SessionCandle(Candle):
    session: Literal["Asia", "London", "NewYork", "PostNY", "Unknown"]


def label_session(utc_iso_time: str) -> str:
    dt = datetime.fromisoformat(utc_iso_time.replace("Z", "+00:00"))
    hour = dt.hour
    if 0 <= hour < 7:
        return "Asia"
    elif 7 <= hour < 12:
        return "London"
    elif 12 <= hour < 17:
        return "NewYork"
    elif 17 <= hour < 24:
        return "PostNY"
    return "Unknown"







class CandleList(BaseModel):
    candles: List[Candle]

class SessionCandle(Candle):
    session: str

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
        # Step 1: Assign sessions
        session_groups = defaultdict(list)
        for c in data.candles:
            session = label_session(c.time)
            session_groups[session].append(c)

        # Step 2: Compute session highs/lows
        session_levels = {}
        for session, candles in session_groups.items():
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            session_levels[session] = {
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None
            }

        return session_levels

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 📦 Data Request Schema
class FetchDataRequest(BaseModel):
    symbol: str
    timeframe: Optional[str] = "M5"
    num_bars: Optional[int] = 500
    return_chart: Optional[bool] = False

# 📓 Notion Journal Schema
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

# 📤 Trade Execution Schema
class PlaceOrderRequest(BaseModel):
    symbol: str
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    direction: Literal["BUY", "SELL"]
    volume: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

# ✅ Wait until symbols loaded
def wait_until_symbols_loaded(timeout=10):
    for _ in range(timeout * 10):
        if symbol_name_to_id:
            return True
        time.sleep(0.1)
    return False

@app.get("/health")
def health():
    return {
        "symbols_loaded": len(symbol_name_to_id),
        "connected": client.connected
    }

# 📟 Notion Entry Endpoint
@app.post("/journal-entry")
async def journal_entry(entry: JournalEntry):
    try:
        properties = {
            "Title": {"title": [{"text": {"content": entry.title}}]},
            "Symbol": {"rich_text": [{"text": {"content": entry.symbol}}]},
            "Session": {"rich_text": [{"text": {"content": entry.session}}]},
            "HTF Bias": {"rich_text": [{"text": {"content": entry.htf_bias}}]},
            "Entry Type": {"rich_text": [{"text": {"content": entry.entry_type}}]},
            "Entry Price": {"number": entry.entry_price},
            "Stop Loss": {"number": entry.stop_loss},
            "Target Price": {"number": entry.target_price},
            "Order Type": {"rich_text": [{"text": {"content": entry.order_type}}]},
            "Note": {"rich_text": [{"text": {"content": entry.note}}]},
            "Checklist": {"rich_text": [{"text": {"content": entry.checklist}}]},
            "News & Events": {"rich_text": [{"text": {"content": entry.news_events}}]},
        }

        if entry.chart_url:
            properties["Files & media"] = {"url": entry.chart_url}

        notion.pages.create(parent={"database_id": NOTION_DB_ID}, properties=properties)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 📈 OHLC Data
@app.post("/fetch-data")
async def fetch_data(req: FetchDataRequest):
    try:
        symbol_key = req.symbol.upper()
        if symbol_key not in symbol_name_to_id:
            raise HTTPException(status_code=404, detail=f"Symbol '{req.symbol}' not found")

        if req.num_bars == 500:
            tf = req.timeframe.upper()
            req.num_bars = {
                "M1": 1500, "M5": 500, "M15": 500,
                "M30": 500, "H1": 500, "H4": 500,
                "D1": 300, "W1": 100
            }.get(tf, 500)

        result = get_ohlc_data(req.symbol, req.timeframe, req.num_bars)
        return {
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "ohlc": result["candles"],
            "context": result.get("context", {}),
            "trend": result.get("trend", {})
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 📊 Open Positions
@app.get("/open-positions")
async def open_positions():
    try:
        positions = get_open_positions()
        return {"positions": positions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 🎯 Execute Trade Order
@app.post("/place-order")
def execute_trade(order: PlaceOrderRequest):
    try:
        if not wait_until_symbols_loaded():
            raise HTTPException(status_code=503, detail="Symbols not loaded yet. Try again shortly.")

        symbol_key = order.symbol.upper()
        if symbol_key not in symbol_name_to_id:
            raise HTTPException(status_code=404, detail=f"Symbol '{order.symbol}' not found.")

        symbol_id = symbol_name_to_id[symbol_key]

        print(f"[ORDER DEBUG] Sending order: {order=}, {symbol_id=}")

        # ✅ Pass the volume exactly as sent (float) to preserve decimals
        volume_raw = float(order.volume)

        # Submit order
        deferred = place_order(
            client=client,
            account_id=ACCOUNT_ID,
            symbol_id=symbol_id,
            order_type=order.order_type,
            side=order.direction,
            volume=volume_raw,  # ✅ Keep as float, let place_order handle conversion
            price=order.entry_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit
        )

        result = wait_for_deferred(deferred, timeout=12)

        if isinstance(result, str):
            result = {"message": result}
        elif not isinstance(result, dict):
            result = {"result": str(result)}

        return {
            "status": "success",
            "submitted": True,
            "details": result
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed placing order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("shutdown")
async def stop_ctrader():
    if reactor.running:
        reactor.stop()


# 🔄 Pending Orders
@app.get("/pending-orders")
async def pending_orders():
    try:
        from ctrader_client import get_pending_orders
        return get_pending_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LTFEntry(BaseModel):
    entry_type: Optional[str]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    notes: Optional[str] = ""



class AnalyzeRequest(BaseModel):
    symbol: str

class MTFZones(BaseModel):
    H4_OB: Optional[dict]
    H1_OB: Optional[dict]
    H4_FVG: Optional[dict]
    H1_FVG: Optional[dict]

class Checklist(BaseModel):
    CHOCH: Optional[dict]
    OB: Optional[dict]
    FVG: Optional[dict]
    Sweep: Optional[dict]
    Candle: Optional[dict]

class AnalyzeResponse(BaseModel):
    HTF_Bias: str
    MTF_Zones: MTFZones
    LTF_Entry: Optional[LTFEntry]
    Previous_Day_High: float
    Previous_Day_Low: float
    Session_Levels: dict
    Checklist: Checklist
    News: str


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    try:
        symbol = req.symbol
        timeframes = ["D1", "H4", "H1", "M15", "M5"]
        data = {}

        # Fetch and store the full result (not just candles)
        for tf in timeframes:
            result = get_ohlc_data(symbol, tf, n=100)
            if not isinstance(result, dict) or "candles" not in result:
                raise HTTPException(status_code=500, detail=f"Failed to fetch candles for {tf}")
            data[tf] = result

        # Extract candles from each timeframe
        candles = {tf: data[tf]["candles"] for tf in timeframes}

        # Use local versions
        tagged_m15 = tag_sessions_local(candles["M15"])
        pdh = candles["D1"][-2]["high"]
        pdl = candles["D1"][-2]["low"]
        session_levels = compute_session_levels(tagged_m15)

        htf_bias = detect_trend_bias(candles["D1"])
        mtf_zones = {
            "H4_OB": detect_order_block(candles["H4"]),
            "H1_OB": detect_order_block(candles["H1"]),
            "H4_FVG": detect_fvg(candles["H4"]),
            "H1_FVG": detect_fvg(candles["H1"]),
        }

        ltf_entry = detect_ltf_entry(tagged_m15, candles["M5"], pdh, pdl, session_levels)

        checklist = {
            "CHOCH": detect_choch(candles["M5"]),
            "OB": detect_order_block(candles["M15"]),
            "FVG": detect_fvg(candles["M15"]),
            "Sweep": detect_sweep(tagged_m15, pdh, pdl, session_levels),
            "Candle": detect_bullish_or_bearish_engulfing(candles["M5"]),
        }

        news = ""  # Placeholder

        try:
            print("✅ HTF Bias:", htf_bias)
            print("✅ MTF Zones:", mtf_zones)
            print("✅ LTF Entry Raw:", repr(ltf_entry))
            print("✅ Checklist Raw:", repr(checklist))
            
            response = AnalyzeResponse(
                HTF_Bias=htf_bias,
                MTF_Zones=MTFZones(**mtf_zones),
                LTF_Entry=LTFEntry(**ltf_entry),
                Previous_Day_High=pdh,
                Previous_Day_Low=pdl,
                Session_Levels=session_levels,
                Checklist=Checklist(**checklist),
                News=news,
            )
            print("✅ Final response created.")
            return response
        except Exception as e:
            print("🔥 Exception while constructing AnalyzeResponse:", e)
            raise HTTPException(status_code=500, detail=str(e))


        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/chart")
async def chart(
    symbol: str,
    timeframe: str = "M15",
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None
):
    try:
        candles_data = get_ohlc_data(symbol, timeframe, n=100)
        candles = candles_data["candles"]

        # Detect structural SMC elements
        ob = detect_order_block(candles)
        fvg = detect_fvg(candles)
        choch = detect_choch(candles)

        highlights = {
            "order_block": ob,
            "fvg": fvg,
            "choch": choch,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit
        }

        image_bytes = generate_smc_chart(
            candles,
            title=f"{symbol} SMC Chart - {timeframe}",
            highlights=highlights
        )

        return Response(content=image_bytes, media_type="image/png")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)

