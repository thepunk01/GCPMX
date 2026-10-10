from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


PROBE_TOKEN = os.getenv("PROBE_TOKEN", "").strip()
PROBE_LOCATION = os.getenv("PROBE_LOCATION", "overseas").strip().lower()
MAX_CONCURRENT = max(1, min(int(os.getenv("PROBE_MAX_CONCURRENT", "50")), 200))
CHECK_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)

app = FastAPI(title="GCPMX GFW Probe", version="1.0.0")
_bandwidth_previous: tuple[float, float, float] | None = None


class CheckRequest(BaseModel):
    target_ip: str
    port: int = Field(443, ge=1, le=65535)
    timeout_ms: int = Field(5000, ge=500, le=15000)


def checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="target_ip 必须是有效 IP 地址")
    # 探针只允许检测公网 IP，避免被当作内网端口扫描或开放代理。
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise HTTPException(status_code=400, detail="target_ip 必须是公网 IP")
    return str(address)


def check_token(value: str | None) -> None:
    if not PROBE_TOKEN:
        raise HTTPException(status_code=503, detail="探针尚未配置 PROBE_TOKEN")
    if not value or not secrets.compare_digest(value, PROBE_TOKEN):
        raise HTTPException(status_code=401, detail="探针令牌无效")


def linux_bandwidth_totals() -> tuple[float, float]:
    received = sent = 0.0
    with open('/proc/net/dev', encoding='utf-8') as handle:
        for line in handle:
            if ':' not in line:
                continue
            _, values = line.split(':', 1)
            fields = values.split()
            if len(fields) >= 9:
                received += float(fields[0])
                sent += float(fields[8])
    return received, sent


@app.get("/health")
async def health():
    return {"ok": True, "location": PROBE_LOCATION, "service": "gcpmx-probe"}


@app.get("/v1/bandwidth")
async def bandwidth(x_probe_token: str | None = Header(default=None)):
    global _bandwidth_previous
    check_token(x_probe_token)
    received, sent = linux_bandwidth_totals()
    current = time.monotonic()
    in_bps = out_bps = None
    if _bandwidth_previous is not None:
        old_received, old_sent, old_time = _bandwidth_previous
        elapsed = max(current - old_time, 0.001)
        in_bps = max(0.0, (received - old_received) / elapsed)
        out_bps = max(0.0, (sent - old_sent) / elapsed)
    _bandwidth_previous = (received, sent, current)
    return {"state": "live" if in_bps is not None else "warming", "in_bps": in_bps,
            "out_bps": out_bps, "sampled_at": checked_at(), "location": PROBE_LOCATION}


@app.post("/v1/check")
async def check(request: CheckRequest, x_probe_token: str | None = Header(default=None)):
    check_token(x_probe_token)
    target = public_ip(request.target_ip)
    started = time.perf_counter()
    reachable = False
    error = ""
    async with CHECK_SEMAPHORE:
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target, request.port),
                timeout=request.timeout_ms / 1000,
            )
            reachable = True
        except asyncio.TimeoutError:
            error = "timeout"
        except OSError as exc:
            error = f"{exc.__class__.__name__}: {exc}"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
    return {
        "location": PROBE_LOCATION,
        "target_ip": target,
        "port": request.port,
        "reachable": reachable,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
        "checked_at": checked_at(),
    }
