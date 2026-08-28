# R-GAP — Regional Recoverable Tourism Value Gap Engine

AGENTS.md의 명세를 기준으로 구성한 공모전 제출용 풀스택 프로젝트입니다.

| Layer | Implementation |
|---|---|
| Frontend | React + TypeScript + Vite, React-Leaflet, Recharts |
| Backend | FastAPI (KTO 수집·정규화 / 지표 진단 / 예산 시나리오 API) |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Analysis | 동월·유사 관광구조 표준화, 소비 잔차, 75분위 프론티어를 위한 데이터 모델 |

## Run

```bash
npm install && npm run dev
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload --port 8000
docker compose up -d db
```

API 키는 프로젝트 루트의 `.env`에 입력합니다. 키는 브라우저가 아닌 FastAPI만 읽으며, `/v1/data-sources/status`는 연결 여부만 반환합니다.

공급자마다 승인된 API 상품의 엔드포인트가 다르므로, `.env`의 `*_BASE_URL`에 문서상 기본 URL을 입력한 뒤 `POST /v1/data-sources/fetch`에 상대 경로와 필터를 전달합니다. 키 파라미터명이 다르면 `*_API_KEY_PARAM`도 문서에 맞게 변경합니다. API 키는 응답·프론트엔드에 절대 노출되지 않습니다.

한국관광공사 빅데이터 지역별 방문자수 GW는 `KTO_TOURISM_DATALAB_BASE_URL=https://apis.data.go.kr/B551011/DataLabService`와 `serviceKey` 방식으로 사전 구성했습니다. 제공 포털의 활용신청 상세 화면에서 개별 오퍼레이션 경로와 필수 조회조건을 확인한 뒤, `/v1/data-sources/fetch`에 상대 경로와 조건을 전달합니다.

전용 방문자수 API도 제공합니다. `POST /v1/data-sources/kto/regional-visitors`에 아래처럼 요청합니다.

```json
{"scope":"local","start_ymd":"20260701","end_ymd":"20260731","page_no":1,"num_rows":1000}
```

- `scope: metro` → 광역 지자체 `/metcoRegnVisitrDDList`
- `scope: local` → 기초 지자체 `/locgoRegnVisitrDDList`

같은 KTO 키로 활용신청 후 연결되는 R-GAP 핵심 데이터셋은 `GET /v1/data-sources/kto/catalog`에서 확인할 수 있습니다. 이 응답은 키를 노출하지 않고 각 데이터셋의 연결 준비 상태만 보여 줍니다. 체류·소비 강도는 `POST /v1/data-sources/kto/metric`으로 조회합니다.

승인된 `기초지자체 중심 관광지 정보`는 `LocgoHubTarService1/areaBasedList1`으로 자동 연결됩니다. 대시보드는 반환된 최대 100개 중심 관광지 좌표의 지리적 중심으로부터 RMS 거리(km)를 계산해 **중심지 공간확산 보조지표**로 표시합니다. 이 값은 내비게이션 연계 중심지의 지리적 분포이며 관광지별 실제 방문점유율 HHI나 AGENTS.md의 정식 공간분산 D를 대체하지 않습니다.

```json
{"dataset":"demand_intensity","metric":"stay","params":{"pageNo":"1","numOfRows":"100"}}
```

대시보드 적재 전용 조회 경로는 다음과 같으며, XML을 정규화한 JSON을 반환합니다.

```text
POST /v1/data-sources/kto/demand-intensity/stay
POST /v1/data-sources/kto/demand-intensity/spend
POST /v1/data-sources/kto/tourism-diversity/visitor
POST /v1/data-sources/kto/tourism-diversity/spend
POST /v1/data-sources/kto/tourism-diversity/international
```

각 요청 본문은 `{"area_cd":"47130","base_ym":"202607"}` 형식입니다.

한 지자체의 검증 완료 지표를 한 번에 수집하려면 `POST /v1/data-sources/kto/region-snapshot`을 사용합니다.

```json
{"area_cd":"47130","base_ym":"202607","start_ymd":"20260701","end_ymd":"20260731"}
```

이 요청은 지역별 방문자수, 체류·소비 강도, 관광 다양성 지표를 병렬 조회합니다. 응답은 적재 전 정규화 JSON이며, 이후 완전한 TCEI·R-GAP 계산 파이프라인의 입력값으로 사용할 수 있습니다.

`TatsCnctrRateService`(관광지 집중률)와 `AreaTarResDemService`(관광 자원 수요)는 활용신청 승인 후에도 상세기능 화면에 표시되는 **오퍼레이션명**이 필요합니다. 키는 기존 `KTO_TOURISM_DATALAB_API_KEY`를 그대로 쓰며, 해당 이름만 `.env`의 `KTO_ATTRACTION_CONCENTRATION_ENDPOINT`, `KTO_TOURISM_RESOURCE_DEMAND_ENDPOINT`에 넣으면 `POST /v1/data-sources/kto/configured/{dataset}`으로 호출할 수 있습니다. 전체 URL이나 키를 프론트엔드에 넣지 마세요.

지방재정365와 KOSIS 키도 서버 환경변수에 등록돼 있습니다. 지방재정365는 기관별 실제 API 기본 URL, KOSIS는 필요한 통계표 ID(`statId`)를 선택하면 같은 공급자 어댑터에 연결할 수 있습니다. `PUBLIC_DATA_PORTAL_API_KEY`는 비워 두어도 KTO 키를 자동 재사용하므로 중복 발급할 필요가 없습니다.

웹 앱은 `http://localhost:5173`, API 문서는 `http://localhost:8000/docs`에서 확인합니다.

## 정책 브리프 PDF

대시보드 오른쪽 위의 **정책 브리프** 버튼을 누르면 선택한 지자체, 표본 비교 진단, 지표별 취약도, 예산 초기 시나리오를 A4 보고서 형식으로 구성한 인쇄 화면이 열립니다. 브라우저 인쇄 창에서 **PDF로 저장**을 선택하면 됩니다. 보고서에는 원천자료·파생지표·규칙기반 진단의 구분과 데이터 해석 유의사항을 함께 표기합니다.

## Production deployment

- Frontend: https://regional-tourism-scan.vercel.app/
- API: Vercel의 `/api` Python Function으로 함께 배포됩니다. `api/index.py`가 FastAPI를 `/api` 접두사로 마운트하므로 프론트엔드는 별도 공개 백엔드 URL 없이 `/api/v1/...`을 호출합니다.
- Vercel Project Settings → Environment Variables에 `KTO_TOURISM_DATALAB_API_KEY`를 Production과 Preview에 등록합니다. KTO 키는 브라우저로 전달되지 않습니다.
- 별도 Render 배포가 필요할 경우에는 기존 `render.yaml`을 사용할 수 있으며, 그때만 Vercel의 `VITE_API_BASE_URL`에 Render API 주소를 입력합니다.

## Included specification coverage

- Leaflet 기반 지자체 선택 지도와 표본 비교 진단 유형 배지
- Recharts 기반 표본 중앙값 대비 표준화 취약도 차트
- 숙박 공급 비교 패널과 예산 자동 배분 및 실무자 조정 슬라이더
- T-Shift 정책 실험의 DiD 사후검증 데이터 모델
- PostGIS 지자체 경계, 월별 관광지표, R-GAP 결과, 정책실험 스키마
- 원시 방문객당 소비액을 쓰지 않는 소비 잔차 필드 및 데이터 해석 경고 UI

현재 화면은 한국관광공사 API에서 조회한 이동통신 기반 방문자 추정치와 체류·숙박·소비·다양성·관광지 혼잡 예측 지수, 내비게이션 중심 관광지 좌표 기반 공간확산 거리를 사용합니다. 선택한 4개 도시 중 나머지 3개 도시의 중앙값을 비교 기준으로 사용하며, 이는 전국 대표값이나 구조적으로 유사한 지역 군집을 뜻하지 않습니다. 관광지 혼잡 예측은 관광지별 최혼잡 시점을 100으로 둔 값이고, 중심지 공간확산은 중심 관광지의 지리적 분포이므로 어느 쪽도 실제 방문점유율 기반 공간분산 D를 대체하지 않습니다. 7일 방문자 변동성 역시 연간 계절성의 대체값이 아닙니다. 관광지별 실제 방문 점유율, 12개월 계절성, 소비 잔차의 동월·유사구조 표준화, 75분위 프론티어가 갖춰질 때만 AGENTS.md 규격의 TCEI와 R-GAP을 산출합니다.
