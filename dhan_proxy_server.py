#!/usr/bin/env python3
"""
Dhan OrderFlowMap Proxy Server
==============================
Connects to DhanHQ Live Market Feed / 20-Level Depth WebSocket
and re-publishes clean JSON (OpenAlgo-compatible) on ws://127.0.0.1:8765

Usage:
  export DHAN_CLIENT_ID=your_client_id
  export DHAN_ACCESS_TOKEN=your_access_token
  python dhan_proxy_server.py

Then open index.html → Live mode → Connect
"""

import asyncio
import json
import os
import struct
import time
from typing import Optional

import websockets
from websockets.server import serve

# ── Config ──────────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 8765

# Dhan endpoints
DHAN_FEED_URL = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"
DHAN_20DEPTH_URL = "wss://depth-api-feed.dhan.co/twentydepth?token={token}&clientId={client_id}&authType=2"

# Segment codes used by Dhan binary packets
SEG_MAP = {
    "NSE_EQ": 1,
    "NSE_FNO": 2,
    "NSE_CURRENCY": 3,
    "BSE_EQ": 4,
    "BSE_FNO": 5,
    "MCX_COMM": 8,
    "IDX_I": 0,
}

# Reverse for display
SEG_NAME = {v: k for k, v in SEG_MAP.items()}

# Global state
CLIENTS = set()
DHAN_WS: Optional[websockets.WebSocketClientProtocol] = None
CURRENT_INSTRUMENT = None  # (exchange_segment_str, security_id_str)
LAST_VOLUME = 0
LAST_LTP = 0.0


def get_credentials():
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if not client_id or not token:
        print("=" * 60)
        print("Set environment variables:")
        print("  export DHAN_CLIENT_ID=your_client_id")
        print("  export DHAN_ACCESS_TOKEN=your_access_token")
        print("=" * 60)
        raise SystemExit(1)
    return client_id, token


# ── Binary packet parsers (Dhan Live Market Feed v2) ─────────────────────────

def parse_header(data: bytes):
    """Common 8-byte header for most packets."""
    if len(data) < 8:
        return None
    msg_len = struct.unpack_from("<H", data, 0)[0]
    code = data[2]
    segment = data[3]
    security_id = struct.unpack_from("<i", data, 4)[0]
    return msg_len, code, segment, security_id


def parse_full_packet(data: bytes):
    """
    Full packet (code 8) – LTP + volume + OI + 5-level depth.
    Layout from Dhan docs.
    """
    if len(data) < 162:
        return None
    _, code, segment, sec_id = parse_header(data)
    offset = 8
    ltp = struct.unpack_from("<f", data, offset)[0]; offset += 4
    ltq = struct.unpack_from("<H", data, offset)[0]; offset += 2
    ltt = struct.unpack_from("<i", data, offset)[0]; offset += 4
    atp = struct.unpack_from("<f", data, offset)[0]; offset += 4
    volume = struct.unpack_from("<i", data, offset)[0]; offset += 4
    total_sell = struct.unpack_from("<i", data, offset)[0]; offset += 4
    total_buy = struct.unpack_from("<i", data, offset)[0]; offset += 4
    oi = struct.unpack_from("<i", data, offset)[0]; offset += 4
    # skip oi high/low, open, close, high, low … (we only need depth)
    offset = 8 + 4 + 2 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4  # rough
    # Actual depth starts after OHLC fields. Safer fixed layout:
    # From Dhan docs Full Packet ends with 5×20-byte depth levels.
    depth_start = len(data) - 100  # 5 levels × 20 bytes
    if depth_start < 50:
        depth_start = 62  # fallback
    bids = []
    asks = []
    for i in range(5):
        base = depth_start + i * 20
        if base + 20 > len(data):
            break
        bid_qty = struct.unpack_from("<i", data, base)[0]
        ask_qty = struct.unpack_from("<i", data, base + 4)[0]
        bid_orders = struct.unpack_from("<H", data, base + 8)[0]
        ask_orders = struct.unpack_from("<H", data, base + 10)[0]
        bid_price = struct.unpack_from("<f", data, base + 12)[0]
        ask_price = struct.unpack_from("<f", data, base + 16)[0]
        if bid_price > 0:
            bids.append({"price": round(bid_price, 2), "quantity": bid_qty, "orders": bid_orders})
        if ask_price > 0:
            asks.append({"price": round(ask_price, 2), "quantity": ask_qty, "orders": ask_orders})
    return {
        "ltp": round(ltp, 2),
        "volume": volume,
        "ltt": ltt * 1000 if ltt < 1e12 else ltt,  # ms
        "oi": oi,
        "depth": {"buy": bids, "sell": asks},
        "security_id": sec_id,
        "segment": segment,
    }


def parse_quote_packet(data: bytes):
    """Quote packet (code 4) – lighter."""
    if len(data) < 50:
        return None
    _, code, segment, sec_id = parse_header(data)
    offset = 8
    ltp = struct.unpack_from("<f", data, offset)[0]; offset += 4
    ltq = struct.unpack_from("<H", data, offset)[0]; offset += 2
    ltt = struct.unpack_from("<i", data, offset)[0]; offset += 4
    atp = struct.unpack_from("<f", data, offset)[0]; offset += 4
    volume = struct.unpack_from("<i", data, offset)[0]; offset += 4
    return {
        "ltp": round(ltp, 2),
        "volume": volume,
        "ltt": ltt * 1000 if ltt < 1e12 else ltt,
        "depth": {"buy": [], "sell": []},
        "security_id": sec_id,
        "segment": segment,
    }


def parse_20depth_packet(data: bytes):
    """
    20-level depth packets come as separate Bid (code 41) and Ask (code 51).
    Header is 12 bytes for depth feed.
    """
    if len(data) < 12:
        return None
    msg_len = struct.unpack_from("<H", data, 0)[0]
    code = data[2]
    segment = data[3]
    sec_id = struct.unpack_from("<i", data, 4)[0]
    # levels start at offset 12
    levels = []
    offset = 12
    while offset + 16 <= len(data):
        price = struct.unpack_from("<d", data, offset)[0]
        qty = struct.unpack_from("<I", data, offset + 8)[0]
        orders = struct.unpack_from("<I", data, offset + 12)[0]
        if price > 0:
            levels.append({"price": round(price, 2), "quantity": qty, "orders": orders})
        offset += 16
    side = "buy" if code == 41 else "sell"
    return {"side": side, "levels": levels, "security_id": sec_id, "segment": segment, "code": code}


# ── Broadcast helper ─────────────────────────────────────────────────────────

async def broadcast(msg: dict):
    if not CLIENTS:
        return
    payload = json.dumps(msg)
    dead = set()
    for ws in CLIENTS:
        try:
            await ws.send(payload)
        except Exception:
            dead.add(ws)
    CLIENTS.difference_update(dead)


# ── Dhan upstream connection ─────────────────────────────────────────────────

async def dhan_feed_loop(client_id: str, token: str):
    global DHAN_WS, LAST_VOLUME, LAST_LTP, CURRENT_INSTRUMENT
    url = DHAN_FEED_URL.format(token=token, client_id=client_id)
    while True:
        try:
            print(f"[Dhan] Connecting to Live Market Feed …")
            async with websockets.connect(url, ping_interval=20, ping_timeout=40) as ws:
                DHAN_WS = ws
                print("[Dhan] Connected")
                # If we already have an instrument, subscribe immediately
                if CURRENT_INSTRUMENT:
                    await subscribe_dhan(CURRENT_INSTRUMENT[0], CURRENT_INSTRUMENT[1])

                async for raw in ws:
                    if isinstance(raw, str):
                        continue
                    data = raw if isinstance(raw, bytes) else bytes(raw)
                    if len(data) < 8:
                        continue
                    code = data[2]
                    parsed = None
                    if code == 8:  # Full
                        parsed = parse_full_packet(data)
                    elif code == 4:  # Quote
                        parsed = parse_quote_packet(data)
                    elif code in (41, 51):  # 20-depth (if mixed)
                        continue  # handled by dedicated depth socket if used

                    if not parsed:
                        continue

                    # Trade reconstruction via volume delta
                    vol = parsed.get("volume", 0)
                    ltp = parsed.get("ltp", 0.0)
                    trade_side = None
                    trade_qty = 0
                    if LAST_VOLUME > 0 and vol > LAST_VOLUME:
                        trade_qty = vol - LAST_VOLUME
                        if ltp > LAST_LTP:
                            trade_side = "buy"
                        elif ltp < LAST_LTP:
                            trade_side = "sell"
                        else:
                            trade_side = "buy"  # default
                    LAST_VOLUME = vol
                    LAST_LTP = ltp

                    out = {
                        "type": "market_data",
                        "data": {
                            "ltp": ltp,
                            "volume": vol,
                            "ltt": parsed.get("ltt", int(time.time() * 1000)),
                            "depth": parsed.get("depth", {"buy": [], "sell": []}),
                            "oi": parsed.get("oi", 0),
                        },
                    }
                    if trade_qty > 0 and trade_side:
                        out["data"]["last_trade"] = {
                            "qty": trade_qty,
                            "side": trade_side,
                            "price": ltp,
                        }
                    await broadcast(out)
        except Exception as e:
            print(f"[Dhan] Feed error: {e}. Reconnecting in 5 s …")
            DHAN_WS = None
            await asyncio.sleep(5)


async def subscribe_dhan(exchange_segment: str, security_id: str):
    """Subscribe to Full mode (code 21) for the instrument."""
    global CURRENT_INSTRUMENT, LAST_VOLUME, LAST_LTP
    CURRENT_INSTRUMENT = (exchange_segment, security_id)
    LAST_VOLUME = 0
    LAST_LTP = 0.0
    if not DHAN_WS:
        print("[Dhan] Not connected yet – will subscribe on connect")
        return
    # RequestCode 21 = Full packet
    payload = {
        "RequestCode": 21,
        "InstrumentCount": 1,
        "InstrumentList": [
            {
                "ExchangeSegment": exchange_segment,
                "SecurityId": str(security_id),
            }
        ],
    }
    await DHAN_WS.send(json.dumps(payload))
    print(f"[Dhan] Subscribed {exchange_segment}:{security_id} (Full mode)")


# ── Client-facing WebSocket (OpenAlgo-compatible) ────────────────────────────

async def client_handler(ws):
    CLIENTS.add(ws)
    print(f"[Client] Connected ({len(CLIENTS)} total)")
    try:
        async for message in ws:
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue
            action = msg.get("action")
            if action == "authenticate":
                # Accept any key – real auth is via env vars on server
                await ws.send(json.dumps({"message": "Authentication successful"}))
            elif action == "subscribe":
                symbol = msg.get("symbol", "")
                exchange = msg.get("exchange", "NSE")
                # Map common names → Dhan segment + security_id
                # User can pass security_id directly via "security_id" field
                sec_id = msg.get("security_id") or msg.get("token")
                seg = msg.get("exchange_segment")
                if not sec_id or not seg:
                    # Try simple heuristics / presets
                    preset = resolve_preset(symbol, exchange)
                    if preset:
                        seg, sec_id = preset
                    else:
                        await ws.send(json.dumps({
                            "type": "error",
                            "message": "Provide security_id + exchange_segment or use a known preset (NIFTY, CRUDEOIL, …)"
                        }))
                        continue
                await subscribe_dhan(seg, str(sec_id))
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "symbol": symbol,
                    "exchange_segment": seg,
                    "security_id": sec_id,
                }))
            elif action == "unsubscribe":
                # Dhan does not have explicit unsub per instrument easily;
                # just clear local state
                pass
    except websockets.ConnectionClosed:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[Client] Disconnected ({len(CLIENTS)} total)")


def resolve_preset(symbol: str, exchange: str):
    """
    Convenience presets. Security IDs change every expiry –
    user should look them up from https://images.dhan.co/api-data/api-scrip-master-detailed.csv
    These are placeholders; the UI lets you override.
    """
    s = symbol.upper().replace(" ", "")
    # NIFTY index (spot) – security_id 13, segment IDX_I
    if s in ("NIFTY", "NIFTY50", "NIFTY 50"):
        return ("IDX_I", "13")
    # BANKNIFTY
    if s in ("BANKNIFTY", "NIFTYBANK"):
        return ("IDX_I", "25")
    # Crude Oil – MCX, security_id changes monthly. Leave None so user supplies.
    return None


async def main():
    client_id, token = get_credentials()
    print(f"Dhan OrderFlowMap Proxy  →  ws://{HOST}:{PORT}")
    print("Open index.html → Live mode → Connect")
    # Start Dhan upstream
    asyncio.create_task(dhan_feed_loop(client_id, token))
    async with serve(client_handler, HOST, PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
