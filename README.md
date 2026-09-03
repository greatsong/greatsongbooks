# greatsongbooks — 책 순위·재고 대시보드

교보문고·예스24 공개 페이지에서 책의 순위, 판매지수, 매장별 재고를 매일 07:00(KST)에 자동 수집해
GitHub Pages 대시보드로 보여 준다. 서버 없이 GitHub Actions + 정적 페이지만 사용한다.

## 구조

| 파일 | 역할 |
|---|---|
| `books.json` | 추적할 책 목록 (`kyobo`: 교보 상품 ID `S0002…`, `yes24`: 예스24 상품 번호) |
| `collect.py` | 수집기. 표준 라이브러리만 사용 |
| `data/history.jsonl` | 스냅샷 누적 (1줄 = 책 1권의 1회 수집) |
| `data/latest.json` | 책별 최신 스냅샷 |
| `data/stores.json` | 교보문고 매장 코드 → 이름·지역·주소 |
| `index.html` | 대시보드 (Chart.js, 정적) |
| `.github/workflows/collect.yml` | 매일 07:00(KST) 수집 후 `data/` 커밋 |

## 수집 항목

- 교보문고: 주간베스트 순위(분야), 온라인 재고, 매장별 재고(37곳), 찜 수, MD 추천·품절 등 플래그, 리뷰 통계(구매자 리뷰 수·평점 분포), 함께 구매한 책, 키워드, 진행 중 이벤트, 판매가
- 예스24: 판매지수, 베스트 순위(판매지수가 충분할 때만 노출), 리뷰 수·평점·평점 분포, 한줄평 수
- 알라딘: Sales Point, 리뷰 수, 100자평 수
- 리뷰 본문: 교보 리뷰, 예스24 리뷰·한줄평, 알라딘 리뷰·100자평 (`data/reviews/<id>.json`)

두 사이트 모두 실제 누적 판매 부수는 제공하지 않는다. 판매지수·Sales Point·매장 재고 감소분으로 추정한다.

## 저자 도서 자동 등록

```bash
python3 discover.py 259610 송석리   # 예스24 저자 번호 → books.json 에 새 책 추가
```

## 로컬 실행

```bash
python3 collect.py                     # 1회 수집
python3 -m http.server 4045            # http://localhost:4045 에서 대시보드 확인
```

## 책 추가

`books.json`에 항목을 추가하고 커밋하면 다음 수집부터 반영된다. 대시보드 상단 선택 상자에서 책을 고를 수 있다.
