#!/usr/bin/env python3
"""예스24 저자 페이지의 '전체작품'을 읽어 books.json을 채운다.

사용법: python3 discover.py <예스24 authorNo> [저자명]
  예)  python3 discover.py 259610 송석리

- 예스24 상품 페이지에서 ISBN13·제목·출판사·출간일을 읽고
- 교보문고 검색에서 같은 ISBN의 상품 ID(S000…)를 찾는다.
- 이미 books.json에 있는 책은 그대로 두고, 새 책만 뒤에 추가한다.
"""
import json
import re
import sys
import time
from pathlib import Path

from collect import fetch

ROOT = Path(__file__).resolve().parent


def author_books(author_no):
    html = fetch(f"https://www.yes24.com/product/author/{author_no}", accept="text/html")
    i = html.find('id="authAllBookSec"')
    sec = html[i:] if i >= 0 else html
    j = re.search(r'id="auth(?!AllBook)\w+Sec"', sec)
    if j:
        sec = sec[:j.start()]
    seen, out = set(), []
    for gid, title in re.findall(r'href="/product/goods/(\d+)"[^>]*>([^<]{2,120})</a>', sec):
        title = re.sub(r"\s*새창이동$", "", title.strip())
        if gid in seen or not title:
            continue
        seen.add(gid)
        out.append((gid, title))
    return out


def yes24_info(gid):
    html = fetch(f"https://www.yes24.com/product/goods/{gid}", accept="text/html")
    g = lambda pat: (re.search(pat, html, re.S) or [None, None])[1]  # noqa: E731
    title = g(r'<h2 class="gd_name">([^<]*)')
    isbn = g(r'ISBN13</th>\s*<td[^>]*>\s*([\d]{13})')
    pub = g(r'gd_pub">.*?>([^<]*)<')
    date = g(r'gd_date">([^<]*)')
    block = re.search(r'"author":\s*(\{.*?\}|\[.*?\])', html, re.S)
    author = ", ".join(re.findall(r'"name":\s*"([^"]+)"', block.group(1))) if block else ""
    author_ids = re.findall(r'/product/author/(\d+)', html)
    if date:
        m = re.match(r"(\d{4})년 (\d{2})월 (\d{2})일", date)
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else date
    return {"title": (title or "").strip(), "isbn": isbn, "publisher": (pub or "").strip(),
            "pubDate": date, "author": author, "authorIds": author_ids}


def kyobo_id(isbn):
    if not isbn:
        return None
    html = fetch(f"https://search.kyobobook.co.kr/search?keyword={isbn}&gbCode=TOT&target=total",
                 accept="text/html")
    ids = re.findall(r"detail/(S\d{12})", html)
    return ids[0] if ids else None


def slug(title, isbn):
    s = re.sub(r"[^a-z0-9가-힣]+", "-", title.lower()).strip("-")
    return f"{s[:40]}-{isbn[-4:]}" if isbn else s[:40]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    author_no = sys.argv[1]
    author_name = sys.argv[2] if len(sys.argv) > 2 else None
    path = ROOT / "books.json"
    books = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    known_yes24 = {b.get("yes24") for b in books}

    added = 0
    for gid, title in author_books(author_no):
        if gid in known_yes24:
            print(f"  = {title} (이미 등록)")
            continue
        info = yes24_info(gid)
        kid = kyobo_id(info["isbn"])
        if author_no not in info["authorIds"] and (not author_name or author_name not in info["author"]):
            print(f"  ? {title}: 저자 목록에 없음 ({info['author']}) — 건너뜀")
            continue
        book = {"id": slug(info["title"] or title, info["isbn"]), "title": info["title"] or title,
                "author": info["author"], "publisher": info["publisher"], "pubDate": info["pubDate"],
                "isbn": info["isbn"], "kyobo": kid, "yes24": gid}
        books.append(book)
        added += 1
        print(f"  + {book['title']} | ISBN {book['isbn']} | 교보 {kid} | 예스24 {gid}")
        time.sleep(0.5)

    path.write_text(json.dumps(books, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"books.json: {len(books)}권 (신규 {added})")


if __name__ == "__main__":
    main()
