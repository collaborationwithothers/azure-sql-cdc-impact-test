"""Pool prices from the Azure Retail Prices API, read at report time.

One query returns both meters for the product 'SQL Database Single/Elastic Pool
General Purpose - Compute Gen5': 'vCore' (compute) and 'Zone Redundancy vCore'
(the zone-redundancy add-on). Each pool size N has its own SKU, named
'N vCore' and 'N vCore Zone Redundancy', priced per hour for the whole size.
Monthly = hourly x 730 hours, an assumption stated in the report.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

API = "https://prices.azure.com/api/retail/prices"
REGION = "swedencentral"
PRODUCT = "SQL Database Single/Elastic Pool General Purpose - Compute Gen5"
HOURS_PER_MONTH = 730
# Fields kept when the prices used are written next to the report. IDs are dropped:
# they look like GUIDs and the repository rejects GUIDs in tracked files.
KEEP = ("currencyCode", "retailPrice", "unitPrice", "armRegionName", "effectiveStartDate", "meterName", "productName",
        "skuName", "serviceName", "unitOfMeasure", "type", "armSkuName")


def odata_filter(region: str = REGION) -> str:
    return f"armRegionName eq '{region}' and priceType eq 'Consumption' and productName eq '{PRODUCT}'"


def fetch(region: str = REGION, timeout: float = 30) -> dict:
    """All pages of the query. Returns {"queried_at", "query", "Items"}."""
    url = API + "?" + urllib.parse.urlencode({"$filter": odata_filter(region)})
    query = url
    items: list[dict] = []
    pages = 0
    while url and pages < 20:
        if not url.startswith(API):
            raise ValueError("unexpected NextPageLink host")
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed public API
            page = json.loads(r.read().decode())
        items += page.get("Items", [])
        url = page.get("NextPageLink")
        pages += 1
    return {"queried_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "query": query,
            "Items": [{k: i.get(k) for k in KEEP} for i in items]}


def load_file(path: str) -> dict:
    """A saved response: either the API's own page shape or the shape fetch() returns."""
    with open(path) as f:
        data = json.load(f)
    return {"queried_at": data.get("queried_at", "unknown (from file)"), "query": data.get("query", path),
            "Items": [{k: i.get(k) for k in KEEP} for i in data.get("Items", [])]}


@dataclass(frozen=True)
class PoolPrice:
    vcores: int
    compute_hourly: float
    zone_redundancy_hourly: float
    currency: str
    effective: str

    @property
    def hourly(self) -> float:
        return self.compute_hourly + self.zone_redundancy_hourly

    @property
    def monthly(self) -> float:
        return self.hourly * HOURS_PER_MONTH


def _latest(items: list[dict]) -> dict | None:
    """The newest effective price. Exact duplicates are common in the API and harmless."""
    if not items:
        return None
    items = sorted(items, key=lambda i: str(i.get("effectiveStartDate") or ""))
    newest = items[-1]
    same = [i for i in items if i.get("effectiveStartDate") == newest.get("effectiveStartDate")]
    if len({float(i.get("retailPrice")) for i in same}) > 1:
        raise ValueError(f"conflicting prices for {newest.get('skuName')}")
    return newest


def parse(data: dict, sizes: list[int]) -> tuple[dict[int, PoolPrice], list[str]]:
    """{vcores: PoolPrice} for each size with both meters, plus notes for sizes without."""
    items = [i for i in data.get("Items", []) if i.get("productName") == PRODUCT and i.get("unitOfMeasure") == "1 Hour"
             and (i.get("type") in (None, "Consumption"))]
    out: dict[int, PoolPrice] = {}
    notes: list[str] = []
    for n in sizes:
        compute = _latest([i for i in items if i.get("meterName") == "vCore" and i.get("skuName") == f"{n} vCore"])
        zr = _latest([i for i in items if i.get("meterName") == "Zone Redundancy vCore"
                      and i.get("skuName") == f"{n} vCore Zone Redundancy"])
        if compute is None or zr is None:
            notes.append(f"{n} vCores: no {'compute' if compute is None else 'zone redundancy'} price in the API response")
            continue
        if compute.get("currencyCode") != zr.get("currencyCode"):
            notes.append(f"{n} vCores: currencies differ between meters; not priced")
            continue
        c, z = float(compute["retailPrice"]), float(zr["retailPrice"])
        if math.isnan(c) or math.isnan(z):
            continue
        out[n] = PoolPrice(n, c, z, compute.get("currencyCode") or "", str(compute.get("effectiveStartDate") or ""))
    return out, notes
