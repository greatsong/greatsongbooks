#!/usr/bin/env python3
"""교보문고·예스24에서 책의 순위·판매지수·매장 재고를 수집해 data/에 기록한다.

- books.json 에 등록된 책마다 한 번씩 조회한다.
- data/history.jsonl : 스냅샷 1건 = 1줄 (누적)
- data/latest.json   : 책별 최신 스냅샷
- data/stores.json   : 교보문고 매장 코드 → 이름·지역 (매 실행마다 갱신)

표준 라이브러리만 사용한다.
"""
import gzip
import io
import json
import re
import sys
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

KYOBO_AREA = {"001": "서울", "002": "경기/인천", "003": "지방"}


def fetch(url, referer=None, accept="application/json"):
    headers = {
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
        enc = res.headers.get("Content-Encoding", "")
    if enc == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    elif enc == "deflate":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def fetch_json(url):
    return json.loads(fetch(url))


# ---------------------------------------------------------------- 교보문고
def collect_kyobo(pid):
    base = f"https://product.kyobobook.co.kr/api/gw/pdt/v2/product/{pid}"
    out = {}

    top = fetch_json(f"{base.replace('/product/', '/product/component/')}/top")["data"]["top"]
    info = top.get("info") or {}
    order = top.get("order") or {}
    out["rank"] = [
        {"label": r.get("label"), "rank": r.get("rank")}
        for r in (info.get("weeklyBest") or [])
    ]
    review = info.get("review") or {}
    out["reviewCount"] = review.get("count")
    out["reviewScore"] = review.get("score")
    out["price"] = (order.get("price") or {}).get("discountPrice")
    out["status"] = (order.get("status") or {}).get("value")

    product = fetch_json(base)["data"]["info"]
    out["online"] = product.get("realInvnQntt")

    inv = fetch_json(f"{base}/location-inventory")["data"]
    stores = {}
    meta = {}
    for group in inv:
        area = group.get("strAreaGrpCode")
        for s in group.get("list") or []:
            code = s["strRdpCode"]
            stores[code] = s.get("realInvnQntt", 0)
            meta[code] = {
                "name": s.get("strName"),
                "area": KYOBO_AREA.get(area, area),
                "areaCode": area,
                "addr": (s.get("strAdrs") or "").strip(),
            }
    out["stores"] = stores
    out["storeTotal"] = sum(v for v in stores.values() if isinstance(v, int))
    return out, meta


# ---------------------------------------------------------------- 예스24
RANK_RE = re.compile(r"([가-힣A-Za-z0-9/() ]+?)\s*([\d,]+)위")


def parse_ranks(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    ranks = []
    for label, num in RANK_RE.findall(text):
        label = re.sub(r"^베스트\s*", "", label.strip())
        if not label:
            continue
        ranks.append({"label": label, "rank": int(num.replace(",", ""))})
    return ranks, text.strip()


def collect_yes24(gid):
    url = f"https://www.yes24.com/product/goods/{gid}"
    html = fetch(url, accept="text/html,application/xhtml+xml")
    out = {}

    m = re.search(r"판매지수\s*([\d,]+)", html)
    out["salesIndex"] = int(m.group(1).replace(",", "")) if m else None

    m = re.search(r'gd_reviewCount">(.*?)</span>', html, re.S)
    if m:
        n = re.search(r"([\d,]+)\s*건", re.sub(r"<[^>]+>", " ", m.group(1)))
        out["reviewCount"] = int(n.group(1).replace(",", "")) if n else 0

    m = re.search(r'gd_rating">.*?<em class="yes_b">([\d.]+)</em>', html, re.S)
    out["reviewScore"] = float(m.group(1)) if m else None

    m = re.search(r'BestSellerRank_Book/(\d+)/\?categoryNumber=(\d+)', html)
    ranks, raw = [], ""
    if m:
        mod = fetch(
            f"https://www.yes24.com/Product/addModules/BestSellerRank_Book/{m.group(1)}/"
            f"?categoryNumber={m.group(2)}&FreePrice=N",
            referer=url, accept="text/html")
        if "gd_best" in mod:
            ranks, raw = parse_ranks(mod)
    out["rank"] = ranks
    out["rankText"] = raw
    return out


# ---------------------------------------------------------------- main
def main():
    DATA.mkdir(exist_ok=True)
    books = json.loads((ROOT / "books.json").read_text(encoding="utf-8"))
    now = datetime.now(KST).replace(microsecond=0)

    latest_path = DATA / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    stores_path = DATA / "stores.json"
    stores_meta = json.loads(stores_path.read_text(encoding="utf-8")) if stores_path.exists() else {}

    lines = []
    failures = 0
    for book in books:
        snap = {"t": now.isoformat(), "book": book["id"]}
        if book.get("kyobo"):
            try:
                snap["kyobo"], meta = collect_kyobo(book["kyobo"])
                stores_meta.update(meta)
            except Exception as e:  # noqa: BLE001
                snap["kyoboError"] = f"{type(e).__name__}: {e}"
                failures += 1
                print(f"[kyobo] {book['id']}: {e}", file=sys.stderr)
        if book.get("yes24"):
            try:
                snap["yes24"] = collect_yes24(book["yes24"])
            except Exception as e:  # noqa: BLE001
                snap["yes24Error"] = f"{type(e).__name__}: {e}"
                failures += 1
                print(f"[yes24] {book['id']}: {e}", file=sys.stderr)
        latest[book["id"]] = snap
        lines.append(json.dumps(snap, ensure_ascii=False, separators=(",", ":")))
        k, y = snap.get("kyobo", {}), snap.get("yes24", {})
        print(f"{now:%Y-%m-%d %H:%M} {book['title']}: 교보 순위 {k.get('rank')} 온라인 {k.get('online')} "
              f"매장 {k.get('storeTotal')} | 예스24 판매지수 {y.get('salesIndex')} 순위 {y.get('rank')}")

    with (DATA / "history.jsonl").open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
    stores_path.write_text(json.dumps(stores_meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # 모든 소스가 실패했으면 워크플로가 실패로 보이도록 한다.
    if failures and failures >= 2 * len(books):
        sys.exit(1)


if __name__ == "__main__":
    main()
