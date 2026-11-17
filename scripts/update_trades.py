#!/usr/bin/env python3
"""국토부 아파트 매매 실거래가 주간 갱신.

- data/trades.csv (원천 데이터)를 갱신하고 data/trades_agg.json (앱용 요약)을 다시 만듭니다.
- 매번 최근 3개월을 다시 받아 늦게 신고된 거래·취소 거래를 반영합니다.
- complexes.json 에 새로 넣은 단지(데이터가 0건)는 2015년부터 자동으로 채웁니다.
- 인증키는 환경변수 MOLIT_KEY (GitHub Secrets) 에서 읽습니다.

사용: MOLIT_KEY=... python scripts/update_trades.py            (수집 + 요약)
      python scripts/update_trades.py --aggregate-only          (요약만 다시 계산)
"""
import json, os, sys, time, datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / 'scripts/complexes.json').read_text(encoding='utf-8'))
TRADES = ROOT / 'data/trades.csv'
AGG = ROOT / 'data/trades_agg.json'
PTS = ROOT / 'data/trades_pts.json'
REB = ROOT / 'data/reb.json'
URL = 'https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade'
COLS = ['법정동','아파트명','전용면적','계약년','계약월','계약일','거래금액_만원','층','건축년도','시군구코드','계약일자','단지라벨','평형구분','지역']
START = (2015, 1)
RECENT_MONTHS = 3
KST = dt.timezone(dt.timedelta(hours=9))


def ptype(a):
    if 57 <= a < 62: return '59㎡(24평)'
    if 83 <= a < 86: return '84㎡(34평)'
    return f'{round(a)}㎡'


def months_between(a, b):
    y, m = a
    out = []
    while (y, m) <= b:
        out.append(f'{y}{m:02d}')
        m += 1
        if m > 12: y, m = y + 1, 1
    return out


def fetch_month(key, lawd, ym):
    import requests
    items, page = [], 1
    while True:
        for attempt in range(4):
            try:
                r = requests.get(URL, params={'serviceKey': key, 'LAWD_CD': lawd, 'DEAL_YMD': ym,
                                              'numOfRows': 1000, 'pageNo': page}, timeout=30)
                root = ET.fromstring(r.content)
                code = root.findtext('.//resultCode')
                if code != '000':
                    raise RuntimeError(f'resultCode={code} {root.findtext(".//resultMsg")} {r.text[:200]}')
                break
            except Exception as e:  # 재시도
                if attempt == 3:
                    raise RuntimeError(f'{lawd} {ym}: {e}')
                time.sleep(2 + attempt * 3)
        its = root.findall('.//item')
        items += its
        total = int(root.findtext('.//totalCount') or 0)
        if len(items) >= total or not its:
            return items
        page += 1


def g(it, tag):
    v = it.findtext(tag)
    return (v or '').strip()


def collect():
    key = os.environ.get('MOLIT_KEY', '').strip()
    if not key:
        sys.exit('MOLIT_KEY 환경변수가 없습니다 (GitHub → Settings → Secrets → Actions 에 등록).')
    df = pd.read_csv(TRADES, dtype={'시군구코드': str}) if TRADES.exists() else pd.DataFrame(columns=COLS)
    now = dt.datetime.now(KST)
    cur = (now.year, now.month)
    rm = now.month - (RECENT_MONTHS - 1)
    ry = now.year
    while rm < 1: rm += 12; ry -= 1
    recent = months_between((ry, rm), cur)

    by_lawd = {}
    for c in CFG['complexes']:
        by_lawd.setdefault(c['lawd'], []).append(c)

    added = removed = 0
    log = []
    for lawd, cs in by_lawd.items():
        name2c = {n: c for c in cs for n in c['names']}
        missing = [c for c in cs if not (df['단지라벨'] == c['label']).any()]
        plan = {ym: set(name2c) for ym in recent}  # 최근 3개월: 모든 단지
        for c in missing:  # 새 단지: 처음부터
            since = tuple(int(x) for x in c.get('since', f'{START[0]}-{START[1]:02d}').split('-'))
            for ym in months_between(since, cur):
                plan.setdefault(ym, set()).update(c['names'])
        for ym in sorted(plan):
            names = plan[ym]
            rows = []
            for it in fetch_month(key, lawd, ym):
                nm = g(it, 'aptNm')
                if nm not in names or g(it, 'cdealType'):
                    continue
                c = name2c[nm]
                y, m, d = int(g(it, 'dealYear')), int(g(it, 'dealMonth')), int(g(it, 'dealDay'))
                area = float(g(it, 'excluUseAr'))
                rows.append({'법정동': g(it, 'umdNm'), '아파트명': nm, '전용면적': area, '계약년': y, '계약월': m, '계약일': d,
                             '거래금액_만원': int(g(it, 'dealAmount').replace(',', '')), '층': int(g(it, 'floor') or 0),
                             '건축년도': int(g(it, 'buildYear') or 0), '시군구코드': lawd, '계약일자': f'{y}-{m:02d}-{d:02d}',
                             '단지라벨': c['label'], '평형구분': ptype(area), '지역': c['region']})
            y, m = int(ym[:4]), int(ym[4:])
            mask = (df['시군구코드'].astype(str) == lawd) & (df['계약년'] == y) & (df['계약월'] == m) & (df['아파트명'].isin(names))
            removed += int(mask.sum())
            df = df[~mask]
            if rows:
                df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
                added += len(rows)
            time.sleep(0.15)
        log.append(f'{lawd}: {len(plan)}개월 조회' + (f' (신규 백필 {", ".join(c["label"] for c in missing)})' if missing else ''))
    df = df[COLS].sort_values(['단지라벨', '계약일자']).reset_index(drop=True)
    df.to_csv(TRADES, index=False, encoding='utf-8-sig')
    print('\n'.join(log))
    print(f'교체 {removed}건 → 신규 {added}건, 전체 {len(df)}건')


def monthly_series(trades, months):
    """월별 연속 시세: 6개월 이동 중앙값(3건 이상) → 부족하면 12개월(3건 이상) → 그래도 부족하면 None.
    10만원 단위로 반올림해 만원으로 반환 (앱 그래프용)."""
    by = {}
    for d, p in trades:
        by.setdefault(d[:7], []).append(p)
    per = [by.get(m, []) for m in months]
    out = []
    for i in range(len(months)):
        v = None
        for w in (6, 12):
            pool = [p for j in range(max(0, i - w + 1), i + 1) for p in per[j]]
            if len(pool) >= 3:
                a = sorted(pool); n = len(a)
                v = int(a[(n - 1) // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2)
                v = int(v / 10 + 0.5) * 10
                break
        out.append(v)
    return out


def med(s):
    return int(s.median()) if len(s) else None


def region_ratio(region_key, reb):
    """지역 부동산원 지수: 최신값 / 최근 26주 평균 (3개월 거래가 적을 때 보정용)"""
    ser = (reb.get('regionIdx') or {}).get(region_key)
    if not ser:
        return None
    vals = [v for _, v in ser][-26:]
    return vals[-1] / (sum(vals) / len(vals))


def aggregate():
    d = pd.read_csv(TRADES, dtype={'시군구코드': str})
    d['dt'] = pd.to_datetime(dict(year=d['계약년'], month=d['계약월'], day=d['계약일']))
    d['pt'] = d['평형구분'].str.extract(r'^(59|84)')[0]
    reb = json.loads(REB.read_text(encoding='utf-8')) if REB.exists() else {}
    last = d['dt'].max()
    months = [f'{y}-{m:02d}' for y in range(START[0], last.year + 1) for m in range(1, 13)
              if (y, m) >= START and (y, m) <= (last.year, last.month)]
    prev = json.loads(AGG.read_text(encoding='utf-8')).get('complexes', {}) if AGG.exists() else {}
    prev_pts = json.loads(PTS.read_text(encoding='utf-8')).get('pts', {}) if PTS.exists() else {}
    out = {}
    for c in CFG['complexes']:
        if not c.get('gap', True) and '현재집' not in c['label']:
            continue
        s0 = d[d['단지라벨'] == c['label']]
        for pt in c.get('pt', ['59', '84']):
            s = s0[s0['pt'] == pt]
            key = f"{c['label']}|{pt}"
            if s0.empty and key in prev:  # 아직 원천 데이터가 없는 단지는 이전 요약 유지 (첫 백필 전까지)
                e = dict(prev[key]); pm = e.get('m') or []
                e['m'] = (pm + [None] * len(months))[:len(months)]
                out[key] = e
                continue
            if len(s) < 30 and not c.get('note'):
                continue
            r6 = s[s['dt'] > last - pd.Timedelta(days=183)]['거래금액_만원']
            r3 = s[s['dt'] > last - pd.Timedelta(days=92)]['거래금액_만원']
            pk = s[s['dt'].dt.year.isin([2021, 2022])]['거래금액_만원']
            m21 = s[s['dt'].dt.year == 2021]['거래금액_만원']
            tail = s.sort_values('dt').tail(5)
            y1 = s[s['dt'] > last - pd.Timedelta(days=365)]
            hi = y1.loc[y1['거래금액_만원'].idxmax()] if len(y1) else None
            e = {'name': c['label'].replace('(현재집)', ''), 'region': c['region'], 'pt': pt,
                 'm': monthly_series([[t.strftime('%Y-%m-%d'), int(p)] for t, p in zip(s['dt'], s['거래금액_만원'])], months),
                 'n': int(len(s)),
                 'recent': [med(r6), int(len(r6))], 'r3': [med(r3), int(len(r3))],
                 'peak21': int(pk.max()) if len(pk) else None, 'med21': med(m21),
                 'last': [[t.strftime('%Y-%m-%d'), int(p), int(f)] for t, p, f in zip(tail['dt'], tail['거래금액_만원'], tail['층'])],
                 'hi12': [hi['dt'].strftime('%Y-%m-%d'), int(hi['거래금액_만원']), int(hi['층'])] if hi is not None else None,
                 'thz': bool(c.get('thz')), 'ref': bool(c.get('ref')), 'conv': c.get('conv'),
                 'vol': {str(k): int(v) for k, v in s0.groupby(s0['dt'].dt.year).size().items()}}
            if len(r3) >= 5:
                e['est'], e['estSrc'] = e['r3'][0], '3m'
            elif e['recent'][0]:
                ratio = region_ratio(CFG['regionIndex'].get(c['region']), reb) or 1.0
                e['est'], e['estSrc'] = int(round(e['recent'][0] * ratio / 100) * 100), 'idx'
            else:
                e['est'], e['estSrc'] = None, 'none'
            if c.get('note') and e['est'] is None:
                e['note'] = c['note']
            out[key] = e
    # 개별 거래 점 (그래프용): [2015-01-01부터 일수, 거래가(만원)]
    base = pd.Timestamp(f'{START[0]}-{START[1]:02d}-01')
    pts = {}
    for key in out:
        lab, pt = key.split('|')
        s = d[(d['단지라벨'] == lab) & (d['pt'] == pt)].sort_values('dt')
        if len(s):
            pts[key] = [[int((t - base).days), int(p)] for t, p in zip(s['dt'], s['거래금액_만원'])]
        elif key in prev_pts:  # 원천 데이터가 아직 없으면 이전 값 유지
            pts[key] = prev_pts[key]
    PTS.write_text(json.dumps({'start': base.strftime('%Y-%m-%d'), 'pts': pts}, separators=(',', ':')), encoding='utf-8')
    AGG.write_text(json.dumps({'asof_trades': last.strftime('%Y-%m-%d'), 'months': months,
                               'updated': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
                               'complexes': out}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'요약 {len(out)}개 (실거래 ~{last:%Y-%m-%d}) → {AGG.relative_to(ROOT)}')


if __name__ == '__main__':
    if '--aggregate-only' not in sys.argv:
        collect()
    aggregate()
