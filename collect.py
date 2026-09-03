#!/usr/bin/env python3
"""교보문고·예스24에서 책의 순위·판매지수·매장 재고를 수집해 data/에 기록한다.

- books.json 에 등록된 책마다 한 번씩 조회한다.
- data/history.jsonl : 스냅샷 1건 = 1줄 (누적)
- data/latest.json   : 책별 최신 스냅샷
- data/stores.json   : 교보문고 매장 코드 → 이름·지역 (매 실행마다 갱신)
- data/reviews/<book>.json : 교보·예스24 리뷰 전체 (매 실행마다 덮어씀)

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

    pdata = fetch_json(base)["data"]
    product = pdata.get("info") or {}
    policy = pdata.get("policy") or {}
    out["online"] = product.get("realInvnQntt")
    out["likeCount"] = policy.get("likeCount")
    out["flags"] = {k: bool(policy.get(k)) for k in ("mdChoice", "soldout", "reStock", "todayBook", "limitSale", "coupon")}
    barcode = product.get("cmdtCode")

    try:
        st = fetch_json("https://product.kyobobook.co.kr/api/gw/shrd/review/Statistics"
                        f"?saleCmdtid={pid}&saleCmdtids={pid}")["data"] or {}
        out["reviewStats"] = {
            "total": st.get("whlRevwCont"), "buyer": st.get("buyRevwNumc"), "avg": st.get("revwRvgrAvg"),
            "dist": {str(i): st.get(f"revwRvgrNumc{i}") or 0 for i in range(1, 5)},
        }
    except Exception as e:  # noqa: BLE001
        out["reviewStatsError"] = str(e)

    if barcode:
        try:
            bt = fetch_json("https://product.kyobobook.co.kr/api/ai/picks-bought-together"
                            f"?barcode={barcode}&ejk_gb=KOR&size=10")
            out["boughtTogether"] = [
                {"title": r.get("book_nm"), "author": r.get("author_nm"), "id": r.get("sale_cmdtid"),
                 "isbn": r.get("barcode") or r.get("w_barcode"), "count": r.get("ordr_klover_cnt")}
                for r in (bt.get("persona") or [])[:10]
            ]
        except Exception as e:  # noqa: BLE001
            out["boughtTogetherError"] = str(e)
        try:
            kp = fetch_json(f"https://product.kyobobook.co.kr/api/ai/keyword-picks?key={barcode}&size=10")
            out["keywords"] = [r.get("keyword") for r in (kp.get("persona") or []) if r.get("keyword")]
        except Exception as e:  # noqa: BLE001
            out["keywordsError"] = str(e)
    try:
        ev = fetch_json(f"https://product.kyobobook.co.kr/api/gw/evt/external/events-by-cmdtid?saleCmdtid={pid}")
        out["events"] = [{"id": e.get("eventId"), "title": e.get("title"), "content": e.get("content")}
                         for e in (ev.get("data") or [])]
    except Exception as e:  # noqa: BLE001
        out["eventsError"] = str(e)

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

    m = re.search(r"종이책 한줄평\s*\((\d+)건\)", html)
    out["onelineCount"] = int(m.group(1)) if m else None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    dist = re.findall(r"평점 ([\d.]+)\s*~\s*([\d.]+)점\s*(\d+)%", text)
    out["ratingDist"] = [{"range": f"{a}~{b}", "pct": int(c)} for a, b, c in dist] or None
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


# ---------------------------------------------------------------- 알라딘
def collect_aladin(isbn):
    html = fetch(f"https://www.aladin.co.kr/shop/wproduct.aspx?ISBN={isbn}", accept="text/html")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    out = {}
    m = re.search(r"ItemId=(\d+)", html)
    out["itemId"] = m.group(1) if m else None
    m = re.search(r"Sales Point\s*:\s*([\d,]+)", text)
    out["salesPoint"] = int(m.group(1).replace(",", "")) if m else None
    m = re.search(r"리뷰\((\d+)\)", text)
    out["reviewCount"] = int(m.group(1)) if m else None
    m = re.search(r"100자평\((\d+)\)", text)
    out["commentCount"] = int(m.group(1)) if m else None
    m = re.search(r"([\d.]+)\s*점?\s*\(\s*(\d+)\s*명\s*\)|평점\s*([\d.]+)", text)
    ranks = []
    for label, num in re.findall(r"([가-힣A-Za-z0-9/ ]{1,30}?)\s*(?:주간\s*)?([\d,]+)위", text[:20000]):
        label = label.strip()
        if label and ("top" in label.lower() or "베스트" in label or "주간" in label or len(label) < 20):
            ranks.append({"label": label, "rank": int(num.replace(",", ""))})
    out["rank"] = ranks[:3]
    return out


# ---------------------------------------------------------------- 리뷰
MAX_REVIEW_PAGES = 30


def kyobo_reviews(pid):
    """교보문고 리뷰 전체(최신순). 평점은 4단계(revwRvgr 1~4)를 10점 만점으로 환산한다."""
    out, page = [], 1
    while page <= MAX_REVIEW_PAGES:
        d = fetch_json("https://product.kyobobook.co.kr/api/gw/shrd/review/list"
                       f"?page={page}&pageLimit=50&reviewSort=002&revwPatrCode=001&saleCmdtid={pid}")["data"]
        rows = d.get("reviewList") or []
        for r in rows:
            out.append({
                "id": f"k{r.get('revwNum')}",
                "date": (r.get("cretDttm") or "")[:10],
                "score": r.get("revwRvgr") * 2.5 if isinstance(r.get("revwRvgr"), (int, float)) else None,
                "tag": r.get("revwEmtnKywrName") or None,
                "author": r.get("mmbrId"),
                "text": (r.get("revwCntt") or "").strip(),
                "likes": r.get("reviewRecommendCount") or 0,
                "purchased": r.get("ordrId") is not None,
            })
        if len(out) >= (d.get("totalCount") or 0) or not rows:
            break
        page += 1
    return out


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def yes24_reviews(gid):
    """예스24 리뷰(GoodsReviewList) + 한줄평(AwordReviewList)."""
    ref = f"https://www.yes24.com/product/goods/{gid}"
    out = []
    page = 1
    while page <= MAX_REVIEW_PAGES:
        html = fetch(f"https://www.yes24.com/Product/communityModules/GoodsReviewList/{gid}"
                     f"?goodsSetYn=N&Sort=1&PageNumber={page}", referer=ref, accept="text/html")
        items = re.findall(r'<div class="reviewInfoGrp.*?(?=<div class="reviewInfoGrp|<!-- #+ 리뷰 하나 반복 끝|$)', html, re.S)
        if not items:
            break
        for it in items:
            rid = re.search(r"OpenReviewReport\((\d+)", it) or re.search(r"review-view/(\d+)", it)
            score = re.search(r"total_rating_(\d+)", it)
            body = re.search(r'<div class="reviewInfoBot origin">(.*?)<div class="reviewInfoLike', it, re.S)
            text = ""
            if body:
                p = re.search(r'<div class="review_cont">(.*?)</div>', body.group(1), re.S)
                text = _strip(p.group(1)) if p else ""
            if not text:
                p = re.search(r'<div class="review_cont">(.*?)<span class="review_more"', it, re.S)
                text = _strip(p.group(1)) if p else ""
            out.append({
                "id": f"y{rid.group(1)}" if rid else None,
                "type": "review",
                "date": (re.search(r'txt_date">([\d-]+)', it) or [None, None])[1],
                "score": int(score.group(1)) if score else None,
                "title": _strip((re.search(r'review_tit">(.*?)</span>\s*</span>', it, re.S) or [None, ""])[1].split("</span>")[-1]),
                "author": (re.search(r'class="lnk_id">([^<]*)<', it) or [None, None])[1],
                "text": text,
                "likes": int((re.search(r'ico_sympathy">공감</em><em class="yes_b txt">(\d+)', it) or [None, "0"])[1]),
                "purchased": 'iconC buy' in it,
            })
        # 페이지 번호 링크 중 현재보다 큰 것이 없으면 끝
        pages = [int(x) for x in re.findall(r"PageNumber=(\d+)", html)]
        if not pages or page >= max(pages):
            break
        page += 1

    page = 1
    while page <= MAX_REVIEW_PAGES:
        html = fetch(f"https://www.yes24.com/Product/communityModules/AwordReviewList/{gid}"
                     f"?goodsSetYn=N&Sort=1&PageNumber={page}", referer=ref, accept="text/html")
        items = re.findall(r'<div class="cmtInfoGrp.*?(?=<div class="cmtInfoGrp|$)', html, re.S)
        if not items:
            break
        for it in items:
            rid = re.search(r"AwordRecomment\((\d+)", it)
            score = re.search(r"rating rating_(\d+)", it)
            out.append({
                "id": f"ya{rid.group(1)}" if rid else None,
                "type": "oneline",
                "date": (re.search(r'txt_date">([\d-]+)', it) or [None, None])[1],
                "score": int(score.group(1)) * 2 if score else None,
                "author": (re.search(r'class="lnk_nick"[^>]*>([^<]*)<', it) or [None, None])[1],
                "text": _strip((re.search(r'<div class="cmt_cont">(.*?)</div>', it, re.S) or [None, ""])[1]),
                "likes": int((re.search(r'ico_sympathy">공감</em><em class="yes_b txt">(\d+)', it) or [None, "0"])[1]),
                "purchased": 'iconC buy' in it,
            })
        pages = [int(x) for x in re.findall(r"PageNumber=(\d+)", html)]
        if not pages or page >= max(pages):
            break
        page += 1
    # id 기준 중복 제거
    seen, uniq = set(), []
    for r in out:
        key = r["id"] or (r["date"], r["author"], r["text"][:40])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def collect_reviews(book, now):
    path = DATA / "reviews" / f"{book['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = {"updated": now.isoformat(), "kyobo": prev.get("kyobo", []), "yes24": prev.get("yes24", [])}
    errors = []
    if book.get("kyobo"):
        try:
            out["kyobo"] = kyobo_reviews(book["kyobo"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"kyobo: {type(e).__name__}: {e}")
    if book.get("yes24"):
        try:
            out["yes24"] = yes24_reviews(book["yes24"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"yes24: {type(e).__name__}: {e}")
    if errors:
        out["errors"] = errors
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out["kyobo"]), len(out["yes24"]), errors


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
        if book.get("isbn"):
            try:
                snap["aladin"] = collect_aladin(book["isbn"])
            except Exception as e:  # noqa: BLE001
                snap["aladinError"] = f"{type(e).__name__}: {e}"
                print(f"[aladin] {book['id']}: {e}", file=sys.stderr)
        nk, ny, rerr = collect_reviews(book, now)
        snap["reviews"] = {"kyobo": nk, "yes24": ny}
        for msg in rerr:
            print(f"[reviews] {book['id']}: {msg}", file=sys.stderr)
        latest[book["id"]] = snap
        lines.append(json.dumps(snap, ensure_ascii=False, separators=(",", ":")))
        k, y = snap.get("kyobo", {}), snap.get("yes24", {})
        print(f"{now:%Y-%m-%d %H:%M} {book['title']}: 교보 순위 {k.get('rank')} 온라인 {k.get('online')} "
              f"매장 {k.get('storeTotal')} 찜 {k.get('likeCount')} | 예스24 판매지수 {y.get('salesIndex')} 순위 {y.get('rank')} "
              f"| 알라딘 SP {(snap.get('aladin') or {}).get('salesPoint')} | 리뷰 {nk}+{ny}")

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
