# 갈아타기 전략 노트 (New-home v5)

덕소 → 상급지 갈아타기용 PWA. 매도 신호 · 목표 단지 갭 · 자금/DSR · 플랜·세금.

## 데이터 자동 업데이트 (매주 금요일 07:17 KST)

`.github/workflows/update-data.yml` 이 매주 실행되어 `data/` 를 갱신하고 커밋합니다. 앱은 열 때마다 `data/*.json` 을 새로 읽고, 실패하면 앱에 내장된 스냅샷을 씁니다.

| 파일 | 내용 | 만드는 스크립트 |
|---|---|---|
| `data/trades.csv` | 국토부 실거래 원천 데이터 (2015~) | `scripts/update_trades.py` |
| `data/trades_agg.json` | 단지별 월별 시세(2015~, 6개월 이동 중앙값)·최근 시세·갭 계산용 요약 | `scripts/update_trades.py` |
| `data/reb.json` | 한국부동산원 주간 변동률·매매수급지수·지수 추이 | `scripts/update_reb.py` |

### 처음 한 번만 설정
1. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `MOLIT_KEY`
   - Value: data.go.kr 인증키 (Decoding 키)
2. **Actions** 탭 → ‘주간 데이터 업데이트’ → **Run workflow** 로 한 번 실행
   - 첫 실행 때 새로 추가한 단지(안양·수원·군포)는 2015년부터 자동으로 채웁니다(약 10분).

### 단지 추가·삭제
`scripts/complexes.json` 에 항목을 추가하면 다음 실행 때 과거 데이터까지 자동 수집됩니다.

```json
{"label": "단지 표시명", "region": "지역(구)", "lawd": "시군구코드 5자리", "names": ["국토부 아파트명 그대로"], "gap": true}
```
`region` 은 `regionIndex` 목록의 이름(예: `구리`, `하남(미사)`)을 써야 부동산원 지수 보정이 됩니다.

### 수동 실행 (PC)
```bash
pip install pandas requests openpyxl
MOLIT_KEY=인증키 python scripts/update_trades.py
python scripts/update_reb.py "주간아파트가격동향조사 시계열.xlsx"   # 받아둔 엑셀로 변환
```
