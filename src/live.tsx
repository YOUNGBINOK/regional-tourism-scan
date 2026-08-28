import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleMarker, MapContainer, TileLayer, Tooltip } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import './styles.css';
import './live.css';

type Region = { id: string; name: string; province: string; lat: number; lng: number };
type LiveSnapshot = {
  source: string;
  source_status: 'live';
  base_ymd: string;
  area: { area_cd: string; area_name: string; resident_visitors: number; outside_visitors: number; foreign_visitors: number };
  national_comparison: { municipality_count: number; outside_visitor_percentile: number };
  visitor_mix: { outside_share: number; foreign_share: number };
  analysis: { status: 'partial'; message: string; missing_inputs: string[] };
};

const regions: Region[] = [
  { id: '47130', name: '경주시', province: '경상북도', lat: 35.856, lng: 129.224 },
  { id: '51150', name: '강릉시', province: '강원특별자치도', lat: 37.752, lng: 128.876 },
  { id: '50110', name: '제주시', province: '제주특별자치도', lat: 33.499, lng: 126.531 },
  { id: '52110', name: '전주시', province: '전북특별자치도', lat: 35.824, lng: 127.148 },
];
const apiBase = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const formatNumber = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 });

function App() {
  const [regionId, setRegionId] = useState('47130');
  const [date, setDate] = useState('2026-07-01');
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [budget, setBudget] = useState(10);
  const [weights, setWeights] = useState([40, 25, 20, 15]);
  const region = useMemo(() => regions.find((item) => item.id === regionId)!, [regionId]);
  const policyItems = ['체류·숙박', '관광소비', '공간연계', '야간·계절'];
  const weightTotal = weights.reduce((total, value) => total + value, 0);
  const allocations = policyItems.map((name, index) => ({ name, weight: weights[index], amount: budget * weights[index] / weightTotal }));

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${apiBase}/v1/analysis/live-visitor`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cd: regionId, base_ymd: date.replace(/-/g, '') }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '실데이터 요청에 실패했습니다.');
      setSnapshot(data);
    } catch (cause) {
      setSnapshot(null);
      setError(cause instanceof Error ? cause.message : '실데이터 요청에 실패했습니다.');
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [regionId]);
  const select = (next: string) => { setRegionId(next); };
  const stats = snapshot ? [
    ['외지인 방문자', formatNumber.format(snapshot.area.outside_visitors), 'KTO 통신 기반 일별 집계'],
    ['외국인 방문자', formatNumber.format(snapshot.area.foreign_visitors), 'KTO 통신 기반 일별 집계'],
    ['외지인 수요 백분위', `${snapshot.national_comparison.outside_visitor_percentile}%`, `${snapshot.national_comparison.municipality_count}개 지역 비교`],
    ['외지인 비중', `${snapshot.visitor_mix.outside_share}%`, '현지인·외지인·외국인 합계 대비'],
  ] : [];

  return <>
    <header><div className="logo"><b>R</b>R-GAP<i /></div><nav><a href="#map">실시간 방문자 지도</a><a href="#diagnosis">실측 진단</a><a href="#analysis">R-GAP 산출 상태</a><a href="#simulator">예산 시뮬레이터</a><a href="#tshift">T-Shift 검증</a></nav><button type="button">정책 브리프 PDF ↗</button></header>
    <main data-live-analysis="true">
      <section className="hero live-hero"><small>● LIVE DATA CONNECTION · KTO TOURISM DATA LAB</small><div><article><h1>지금은 <em>실제 방문자 데이터</em>로<br />확인합니다.</h1><p>선택한 날짜와 지자체의 한국관광공사 통신 기반 방문자수를 서버에서 직접 조회합니다. R-GAP은 필요한 4대 지표가 모두 적재되었을 때만 산출합니다.</p><a href="#map">실시간 데이터 보기 ↓</a></article><aside><span>DATA STATUS <b>{loading ? 'LOADING' : snapshot ? 'LIVE' : 'CONNECTION REQUIRED'}</b></span><strong>{snapshot ? 'LIVE' : '--'}</strong><div className="bars">{[28, 42, 36, 58, 49, 68, 57, 79].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div><p><b>{snapshot ? snapshot.source : 'KTO API 연결 확인 필요'}</b><span>{snapshot?.base_ymd || date.replace(/-/g, '.')}</span></p></aside></div></section>
      <section id="map" className="section"><div className="heading"><div><small>01 / LIVE VISITOR MAP</small><h2>실시간 방문수,<br /><em>지역별로 확인</em></h2></div><div className="live-controls"><label>기준일<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label><button type="button" onClick={() => void load()} disabled={loading}>{loading ? '조회 중' : '실데이터 조회'}</button></div></div>
        <div className="maplayout"><article className="map"><div>외지인 방문자 · KTO 일별 집계 <span>선택 지역을 클릭하세요</span></div><MapContainer center={[36.25, 127.8]} zoom={6.3} scrollWheelZoom={false}><TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />{regions.map((item) => <CircleMarker key={item.id} center={[item.lat, item.lng]} radius={item.id === regionId ? 18 : 10} pathOptions={{ color: item.id === regionId ? '#173b2b' : '#fff', weight: item.id === regionId ? 4 : 2, fillColor: item.id === regionId ? '#4f8249' : '#99c982', fillOpacity: .9 }} eventHandlers={{ click: () => select(item.id) }}><Tooltip>{item.province} {item.name}</Tooltip></CircleMarker>)}</MapContainer><small>※ 현재는 선택 지자체의 실측값을 표시합니다. 전국 경계·월별 적재는 다음 데이터 파이프라인 단계에서 확장합니다.</small></article>
          <aside className="summary"><small>SELECTED MUNICIPALITY</small><h3>{region.province} {region.name}</h3><label>LIVE OUTSIDE VISITORS</label><strong>{snapshot ? formatNumber.format(snapshot.area.outside_visitors) : '--'}</strong><i><b style={{ width: `${snapshot?.national_comparison.outside_visitor_percentile || 0}%` }} /></i><p>전국 비교 백분위 <b>{snapshot ? `${snapshot.national_comparison.outside_visitor_percentile}%` : '--'}</b></p><label>데이터 상태</label><div className="badges"><span>{snapshot ? '실시간 KTO 방문자수' : '조회 대기'}</span><span>{snapshot ? snapshot.base_ymd : date.replace(/-/g, '')}</span></div><p className="desc">{error || (snapshot ? '외지인 방문자 기준의 실측 스냅샷입니다. 체류·소비·공간 지표가 채워지면 완전한 R-GAP 진단으로 전환됩니다.' : '조회 버튼을 눌러 KTO 방문자 데이터를 불러오세요.')}</p></aside></div>
      </section>
      <section id="diagnosis" className="section live-section"><div className="heading"><div><small>02 / OBSERVED METRICS</small><h2>{region.name}의<br /><em>실측 방문자 진단</em></h2></div><label>진단 대상<select value={regionId} onChange={(event) => select(event.target.value)}>{regions.map((item) => <option key={item.id} value={item.id}>{item.province} {item.name}</option>)}</select></label></div>
        <div className="live-stats">{stats.map(([label, value, caption]) => <article key={label}><small>{label}</small><strong>{value}</strong><p>{caption}</p></article>)}</div>
      </section>
      <section id="analysis" className="analysis-state"><div><small>03 / R-GAP CALCULATION STATUS</small><h2>실측값과 추정값을<br /><em>구분합니다.</em></h2><p>{snapshot?.analysis.message || 'KTO API 연결이 완료되면 실시간 방문자 실측값을 불러옵니다.'}</p><div className="readiness"><span>방문자 수 <b>실시간 연동</b></span><span>체류·숙박 <b>적재 대기</b></span><span>관광소비 <b>적재 대기</b></span><span>공간·계절성 <b>적재 대기</b></span></div></div><article><small>현재 R-GAP</small><strong>산출 보류</strong><p>공급 API가 빈 결과를 반환하고 있습니다. 임의 점수는 사용하지 않으며, 아래 정책 시나리오 기능은 R-GAP 산출과 별도로 사용할 수 있습니다.</p><div className="badges">{(snapshot?.analysis.missing_inputs || ['체류·숙박', '관광소비', '공간분산', '월별 계절성']).map((item) => <span key={item}>{item}</span>)}</div><a href="#simulator">예산 시나리오 열기 ↓</a></article></section>
      <section id="simulator" className="sim"><div className="inside"><div className="heading"><div><small>04 / BUDGET PORTFOLIO SIMULATOR</small><h2>다음 <em>{budget}억</em>은<br />어디에 배분할까요?</h2></div><p>R-GAP 자동추천 전 단계의 실무자용 정책 시나리오입니다.\n가중치는 직접 조정할 수 있으며, 실측 R-GAP으로 표시하지 않습니다.</p></div><div className="simgrid"><article className="budget"><label>증분 관광예산 <b>{budget}억 원</b><input type="range" min="3" max="30" value={budget} onChange={(event) => setBudget(Number(event.target.value))} /></label><small>3억 <span>30억</span></small><p>정책 항목별 가중치 <em>합계 {weightTotal}</em></p>{allocations.map((item, index) => <div className="allocation" key={item.name}><span>{item.name}</span><input aria-label={`${item.name} 가중치`} type="range" min="1" max="80" value={item.weight} onChange={(event) => setWeights((current) => current.map((value, itemIndex) => itemIndex === index ? Number(event.target.value) : value))} /><b>{item.amount.toFixed(1)}억</b></div>)}</article><aside className="impact"><small>SCENARIO PORTFOLIO</small><h3>현재 배분안</h3><strong>{budget}억</strong><span>사용자 조정 가중치 기준</span>{allocations.map((item) => <p key={item.name}><span>{item.name}</span><b>{item.weight / weightTotal * 100 | 0}%</b></p>)}<em>이 배분은 실무자 입력 시나리오입니다. R-GAP 자동 추천은 필수 4대 지표 적재 후 활성화됩니다.</em></aside></div></div></section>
      <section id="tshift" className="section"><div className="heading"><div><small>05 / T-SHIFT EXPERIMENT</small><h2>정책은 실험하고,<br /><em>효과는 증명합니다.</em></h2></div><p>야간·계절 누수 정책의 사전 등록과\nDiD 사후검증을 위한 실행 템플릿입니다.</p></div><div className="did">{[['01', '정책 패키지 설계', '체류 동선·야간 콘텐츠·지역 상권을 하나의 전환 여정으로 설계합니다.'], ['02', '변화 가설·비교군 등록', '성과지표, 대상·비교지역, 관찰기간을 사업 시작 전 고정합니다.'], ['03', 'DiD 효과 리포트', '정책 전후 변화와 비교군 차이를 비교해 순효과를 검증합니다.']].map(([step, title, description]) => <article key={step}><small>{step}</small><h3>{title}</h3><p>{description}</p></article>)}</div></section>
      <section className="meta"><small>DATA INTERPRETATION / REQUIRED META INFO</small><div><b>실측값과 추정값을 구분합니다.</b><p>방문자수는 한국관광공사 통신 기반 지역별 방문자수 GW의 일별 집계입니다. TCEI·R-GAP은 체류, 소비, 공간, 계절성의 동월·유사 관광구조 비교가 모두 갖춰질 때만 계산합니다.</p></div></section>
    </main><footer><div className="logo"><b>R</b>R-GAP</div><small>Regional Recoverable Tourism Value Gap Engine · KTO Tourism Data Challenge</small></footer>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
