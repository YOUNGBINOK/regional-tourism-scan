# R-GAP — Regional Recoverable Tourism Value Gap Engine

AGENTS.md의 명세를 기준으로 구성한 공모전 제출용 풀스택 프로젝트입니다.

| Layer | Implementation |
|---|---|
| Frontend | React + TypeScript + Vite, React-Leaflet, Recharts |
| Backend | FastAPI (TCEI / R-GAP / 예산 포트폴리오 API) |
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

웹 앱은 `http://localhost:5173`, API 문서는 `http://localhost:8000/docs`에서 확인합니다.

## Production deployment

- Frontend: https://regional-tourism-scan.vercel.app/
- Backend: Render에서 이 저장소를 **Blueprint**로 연결하고 `render.yaml`을 선택합니다.
- Render 환경변수 `CORS_ORIGINS`에는 프론트 주소를, Vercel의 `VITE_API_BASE_URL`에는 Render API 주소를 입력합니다.

## Included specification coverage

- Leaflet 기반 전국 R-GAP 지도와 지자체 선택, 유형 배지
- Recharts 기반 TCEI 프론티어 레이더·누수 기여도 파이 차트
- 숙박 공급 비교 패널과 예산 자동 배분 및 실무자 조정 슬라이더
- T-Shift 정책 실험의 DiD 사후검증 데이터 모델
- PostGIS 지자체 경계, 월별 관광지표, R-GAP 결과, 정책실험 스키마
- 원시 방문객당 소비액을 쓰지 않는 소비 잔차 필드 및 데이터 해석 경고 UI

프론트의 현재 값은 시연용 시드입니다. 한국관광 데이터랩·지방재정365 적재 파이프라인이 연결되면 FastAPI가 TCEI/R-GAP을 재산출하고 `rgap_results`에 적재하도록 확장합니다.
