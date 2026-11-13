#!/usr/bin/env python3
"""한국부동산원 주간 아파트가격동향 → data/reb.json

1) R-ONE 공표보고서에서 '주간아파트가격동향조사 시계열통계표' 엑셀을 받아(헤드리스 브라우저),
2) 매도신호·지수 차트·지역 보정에 필요한 값만 뽑아 data/reb.json 으로 저장합니다.

사용: python scripts/update_reb.py                 (다운로드 + 변환)
      python scripts/update_reb.py 파일.xlsx       (이미 받은 엑셀로 변환만)
"""
import json, sys, datetime as dt
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data/reb.json'
LIST_URL = 'https://www.reb.or.kr/r-one/portal/bbs/rpt/searchBulletinPage.do'
SIDO = {'서울', '경기', '인천', '부산', '대구', '대전', '울산', '세종', '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주', '전남광주'}

WEEKLY = {'강남': '서울>강남', '서초': '서울>서초', '송파': '서울>송파', '강동': '서울>강동', '성동': '서울>성동', '서울': '서울',
          '경기': '경기', '과천': '경기>과천', '분당': '경기>성남>분당', '성남수정': '경기>성남>수정', '수지': '경기>용인>수지',
          '기흥': '경기>용인>기흥', '영통': '경기>수원>영통', '안양동안': '경기>안양>동안', '하남': '경기>하남', '구리': '경기>구리',
          '남양주': '경기>남양주', '군포': '경기>군포', '안양만안': '경기>안양>만안'}
MONTHLY = ['서울', '송파', '경기', '남양주', '구리', '하남', '분당', '수지', '안양동안']
REGION = {'구리': '경기>구리', '남양주': '경기>남양주', '하남': '경기>하남', '성남수정': '경기>성남>수정', '성남중원': '경기>성남>중원',
          '수지': '경기>용인>수지', '강동': '서울>강동', '안양만안': '경기>안양>만안', '영통': '경기>수원>영통', '군포': '경기>군포',
          '수원권선': '경기>수원>권선', '수원팔달': '경기>수원>팔달'}
SUPPLY = {'동남권': '동남권', '경기': '경기(', '동부1권': '동부1권', '경부1권': '경부1권'}


def download():
    from playwright.sync_api import sync_playwright
    tmp = ROOT / 'data/_reb.xlsx'
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(accept_downloads=True)
        pg.goto(LIST_URL, wait_until='networkidle', timeout=90000)
        pg.get_by_text('주간아파트가격동향조사 시계열통계표').first.click()
        pg.wait_for_load_state('networkidle')
        link = pg.locator('a', has_text='주간아파트가격동향조사 시계열.xlsx').first
        with pg.expect_download(timeout=300000) as dl:
            link.click()
        dl.value.save_as(tmp)
        b.close()
    return tmp


def qualified_headers(r1, r2):
    names, sido, city = {}, None, None
    for i, (a, b) in enumerate(zip(r1, r2)):
        if i == 0:
            continue
        a = str(a).replace('\n', '').strip() if a is not None else None
        b = str(b).replace('\n', '').strip() if b is not None else None
        if a in SIDO:
            sido, city = a, None
            q = a
        elif a:
            city = a
            q = f'{sido}>{a}' if sido else a
        elif b:
            q = f'{sido}>{city}>{b}' if city else f'{sido}>{b}'
        else:
            continue
        names.setdefault(q, i)  # 처음 나온 열 사용
    return names


def convert(xlsx):
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    rows = list(wb['매매지수'].iter_rows(values_only=True))
    H = qualified_headers(rows[1], rows[2])
    data = [r for r in rows[5:] if isinstance(r[0], dt.datetime)]
    dates = [r[0] for r in data]

    def col(q):
        i = H[q]
        return [float(r[i]) if r[i] is not None else None for r in data]

    def pct(s):
        return [None if (a is None or b is None) else round((b / a - 1) * 100, 2) for a, b in zip([None] + s[:-1], s)]

    W = {k: pct(col(q))[-12:] for k, q in WEEKLY.items()}
    weekly = {'dates': [d.strftime('%Y-%m-%d') for d in dates[-12:]], 'series': W}

    # 월말 지수, 2018-12 = 100
    month_last = {}
    for i, d in enumerate(dates):
        month_last[(d.year, d.month)] = i
    keys = [k for k in sorted(month_last) if k >= (2018, 12)]
    mi = {'months': [f'{y}-{m:02d}' for y, m in keys], 'series': {}}
    for k in MONTHLY:
        s = col(WEEKLY[k])
        base = s[month_last[(2018, 12)]]
        mi['series'][k] = [round(s[month_last[x]] / base * 100, 1) for x in keys]

    region = {k: [[d.strftime('%Y-%m-%d'), round(v, 3)] for d, v in list(zip(dates, col(q)))[-30:] if v is not None]
              for k, q in REGION.items()}

    # 매매수급지수
    srows = list(wb['수급동향'].iter_rows(values_only=True))
    cur, sname = None, {}
    for i, (a, b, c) in enumerate(zip(srows[1], srows[2], srows[3])):
        if a is not None: cur = str(a).replace('\n', '')
        sname[i] = ((str(b).replace('\n', '') if b else cur), str(c))
    sd = [r for r in srows[5:] if r[0] is not None and str(r[0])[:2] == '20']
    supply = {'dates': [str(r[0])[:10] for r in sd[-12:]], 'series': {}}
    for i, (n, t) in sname.items():
        if t != '매매수급':
            continue
        for lab, kw in SUPPLY.items():
            if kw in n and lab not in supply['series']:
                supply['series'][lab] = [round(float(r[i]), 1) for r in sd[-12:]]

    out = {'asof_reb': dates[-1].strftime('%Y-%m-%d'), 'weekly': weekly, 'monthlyIndex': mi,
           'supply': supply, 'regionIdx': region}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'부동산원 {out["asof_reb"]} 기준 → {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else download()
    convert(src)
    if src.name == '_reb.xlsx':
        src.unlink()
