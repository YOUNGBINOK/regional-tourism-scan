import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleMarker, MapContainer, TileLayer, Tooltip } from 'react-leaflet';
import { Bar, BarChart, Cell, ResponsiveContainer, XAxis, YAxis } from 'recharts';
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
  observed_indices: {
    base_ym: string; aggregation: string;
    stay_intensity: number | null; spend_intensity: number | null;
    lodging_share_index: number | null; one_night_index: number | null;
    two_nights_index: number | null; three_plus_nights_index: number | null;
    visitor_diversity: number | null; spend_diversity: number | null; international_diversity: number | null;
    spatial_dispersion: number | null;
  };
  analysis: { status: 'partial'; message: string; missing_inputs: string[] };
};
type StabilitySnapshot = { window_days: number; areas: Record<string, { days_observed: number; stability_index: number | null }> };

// 4단계 데이터 신뢰도 라벨 (AGENTS.md §3.3)
type DataTier = 'measured' | 'derived' | 'modeled' | 'pending';
const tierLabel: Record<DataTier, string> = { measured: '실측', derived: '파생지표', modeled: '모델 추정', pending: '향후 분석 가능' };

function Tier({ tier }: { tier: DataTier }) {
  return <em className={`tier tier-${tier}`}>{tierLabel[tier]}</em>;
}

const regions: Region[] = [
  { id: '47130', name: '경주시', province: '경상북도', lat: 35.856, lng: 129.224 },
  { id: '51150', name: '강릉시', province: '강원특별자치도', lat: 37.752, lng: 128.876 },
  { id: '50110', name: '제주시', province: '제주특별자치도', lat: 33.499, lng: 126.531 },
  { id: '52110', name: '전주시', province: '전북특별자치도', lat: 35.824, lng: 127.148 },
];
const apiBase = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const formatNumber = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 });
const formatSigned = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(0)}%`;

// 취약영역 탐지 임계값: 표본 비교지역 평균 대비 ±10%p를 벗어나면 강점/취약으로 분류
const classifyDiff = (diff: number | null) => diff == null ? '데이터 부족' : diff <= -10 ? '취약' : diff >= 10 ? '강점' : '보통';

// 지역명 받침 유무에 따라 '은/는' 조사를 선택한다 (예: 경주시는 / 강릉시는 / 안산시는 / 전주시는).
const topicParticle = (name: string) => {
  const code = name.charCodeAt(name.length - 1) - 0xac00;
  const hasBatchim = code >= 0 && code <= 11171 && code % 28 !== 0;
  return `${name}${hasBatchim ? '은' : '는'}`;
};

type Axis = { key: string; label: string; diff: number | null; tier: DataTier; note: string };

function App() {
  const [regionId, setRegionId] = useState('47130');
  const [date, setDate] = useState('2026-07-01');
  const [snapshots, setSnapshots] = useState<Record<string, LiveSnapshot | null>>({});
  const [stability, setStability] = useState<StabilitySnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [budget, setBudget] = useState(10);
  const [weights, setWeights] = useState([40, 25, 20, 15]);
  const [weightsTouched, setWeightsTouched] = useState(false);
  const region = useMemo(() => regions.find((item) => item.id === regionId)!, [regionId]);
  const policyItems = ['체류·숙박', '관광소비', '공간연계', '야간·계절'];
  const weightTotal = weights.reduce((total, value) => total + value, 0);
  const allocations = policyItems.map((name, index) => ({ name, weight: weights[index], amount: budget * weights[index] / weightTotal }));

  const loadAll = async () => {
    setLoading(true);
    setError('');
    try {
      const entries = await Promise.all(regions.map(async (item) => {
        const response = await fetch(`${apiBase}/v1/analysis/live-visitor`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ area_cd: item.id, base_ymd: date.replace(/-/g, '') }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `${item.name} 실데이터 요청에 실패했습니다.`);
        return [item.id, data as LiveSnapshot] as const;
      }));
      setSnapshots(Object.fromEntries(entries));
    } catch (cause) {
      setSnapshots({});
      setError(cause instanceof Error ? cause.message : '실데이터 요청에 실패했습니다.');
    } finally { setLoading(false); }

    // 계절/시간 안정성: 지역 방문자 원천이 지자체 단위로 필터링되지 않으므로,
    // 표본 4개 지역을 한 번에 조회해 공유 응답에서 지역별 변동성을 계산한다.
    try {
      const response = await fetch(`${apiBase}/v1/analysis/visitor-stability`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cds: regions.map((item) => item.id), base_ymd: date.replace(/-/g, ''), window_days: 7 }),
      });
      const data = await response.json();
      setStability(response.ok ? data : null);
    } catch { setStability(null); }
  };

  useEffect(() => { void loadAll(); }, [date]);
  const select = (next: string) => { setRegionId(next); };
  const snapshot = snapshots[regionId] || null;

  // 유사지역 비교: 현재는 표본 4개 관광거점을 비교지역 삼아 평균 대비 상대값을 계산한다.
  // 전국 단위 유사 관광구조 군집은 §4.1(AGENTS.md) 고도화 단계에서 확장 예정.
  const peers = regions.filter((item) => item.id !== regionId).map((item) => snapshots[item.id]).filter((item): item is LiveSnapshot => !!item);
  const peerAvg = (pick: (snap: LiveSnapshot) => number | null) => {
    const values = peers.map(pick).filter((value): value is number => value != null);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  };
  const diffOf = (value: number | null, avg: number | null) => value == null || avg == null || avg === 0 ? null : ((value - avg) / avg) * 100;

  const demandDiff = snapshot ? diffOf(snapshot.area.outside_visitors, peerAvg((s) => s.area.outside_visitors)) : null;
  const stayDiff = snapshot ? diffOf(snapshot.observed_indices.stay_intensity, peerAvg((s) => s.observed_indices.stay_intensity)) : null;
  const lodgingDiff = snapshot ? diffOf(snapshot.observed_indices.lodging_share_index, peerAvg((s) => s.observed_indices.lodging_share_index)) : null;
  const spendDiff = snapshot ? diffOf(snapshot.observed_indices.spend_intensity, peerAvg((s) => s.observed_indices.spend_intensity)) : null;
  const dispersionDiff = snapshot ? diffOf(snapshot.observed_indices.spatial_dispersion, peerAvg((s) => s.observed_indices.spatial_dispersion)) : null;

  // 계절/시간 안정성: 표본 지역 공유 조회 결과(최근 7일 변동성)에서 지역별 값을 뽑아 동일하게 비교지역 평균 대비로 계산한다.
  const stabilityValue = (id: string) => stability?.areas[id]?.stability_index ?? null;
  const peerStabilityAvg = () => {
    const values = regions.filter((item) => item.id !== regionId).map((item) => stabilityValue(item.id)).filter((value): value is number => value != null);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  };
  const seasonDiff = stability ? diffOf(stabilityValue(regionId), peerStabilityAvg()) : null;

  const axes: Axis[] = [
    { key: 'demand', label: '관광수요', diff: demandDiff, tier: 'derived', note: '외지인 방문자수 · 표본 비교지역 평균 대비' },
    { key: 'stay', label: '체류', diff: stayDiff, tier: 'derived', note: '체류 강도지수 · 표본 비교지역 평균 대비' },
    { key: 'spend', label: '관광소비', diff: spendDiff, tier: 'derived', note: '소비 강도지수 · 표본 비교지역 평균 대비' },
    { key: 'stayShare', label: '숙박', diff: lodgingDiff, tier: 'derived', note: '숙박 방문자 비중 지수(2102) · 표본 비교지역 평균 대비' },
    { key: 'dispersion', label: '공간확산', diff: dispersionDiff, tier: 'derived', note: '관광지 집중도(최근 30일 예측, 조회일과 무관) · 표본 비교지역 평균 대비' },
    { key: 'season', label: '계절/시간 안정성', diff: seasonDiff, tier: 'derived', note: '최근 7일 일별 방문자 변동성(연 단위 계절성은 향후 확장) · 표본 비교지역 평균 대비' },
  ];

  // 원인분해: 현재 실측·파생지표만으로 판별 가능한 관계(수요→체류→소비 전환)만 규칙 기반으로 판단한다.
  // 숙박공급/야간콘텐츠 등 2차 구조지표가 연동되기 전까지는 CASE A/B 세부 유형 대신 상위 유형만 제시한다.
  let regionType = '진단 데이터 수집 중';
  let diagnosisText = '표본 데이터가 모두 모이면 유사지역 비교 진단을 표시합니다.';
  if (dispersionDiff != null && dispersionDiff <= -15 && (demandDiff == null || demandDiff >= -10)) {
    regionType = '단일거점편중형';
    diagnosisText = `${topicParticle(region.name)} 관광수요·체류는 양호하지만, 특정 관광지에 방문이 집중돼 표본 비교지역보다 공간적으로 분산되지 못하고 있습니다. 관광지 간 연결·연계 콘텐츠가 우선 과제입니다.`;
  } else if (demandDiff != null && stayDiff != null && spendDiff != null) {
    if (demandDiff >= -10 && stayDiff <= -10) {
      regionType = '체류전환 부족형';
      diagnosisText = `${topicParticle(region.name)} 관광수요는 표본 비교지역 평균 수준 이상이지만, 확보된 방문이 체류로 충분히 이어지지 않고 있습니다. 숙박·체류 콘텐츠 강화가 우선 과제입니다.`;
    } else if (stayDiff >= -10 && spendDiff <= -10) {
      regionType = '소비연결 부족형';
      diagnosisText = `${topicParticle(region.name)} 관광객 유입과 체류에는 문제가 없지만, 확보된 관광수요가 소비로 충분히 연결되지 않고 있습니다. 상권 연계·소비 유도 정책이 우선 과제입니다.`;
    } else if (demandDiff <= -10 && stayDiff <= -10 && spendDiff <= -10) {
      regionType = '복합취약형';
      diagnosisText = `${topicParticle(region.name)} 수요·체류·소비 전 구간이 표본 비교지역 평균을 밑돌고 있어 개별 정책보다 구조적 진단이 우선 필요합니다.`;
    } else if (demandDiff <= -10) {
      regionType = '수요부족형';
      diagnosisText = `${topicParticle(region.name)} 체류·소비 전환 자체는 양호하지만, 유입되는 관광수요 자체가 표본 비교지역 평균보다 적습니다.`;
    } else {
      regionType = '안정형';
      diagnosisText = `${topicParticle(region.name)} 수요·체류·소비 축 모두 표본 비교지역 평균과 비슷하거나 앞서 있습니다. 신규 유형 정책보다 세부 축(숙박·공간확산) 데이터 연동 후 재진단을 권장합니다.`;
    }
  }

  // 정책 우선순위 TOP 3: 계산 가능한 축의 취약 정도로 순위를 매기고, 데이터가 없는 축은 "향후 분석" 항목으로 채운다.
  const rankedWeak = axes.filter((axis) => axis.diff != null).sort((a, b) => (a.diff ?? 0) - (b.diff ?? 0)).filter((axis) => (axis.diff ?? 0) < 0);
  const pendingAxes = axes.filter((axis) => axis.diff == null);
  const priorities = [...rankedWeak, ...pendingAxes].slice(0, 3);
  const promotionLowPriority = demandDiff != null && demandDiff >= -5;
  const chartData = axes.filter((axis) => axis.diff != null).map((axis) => ({ name: axis.label, diff: axis.diff as number }));

  useEffect(() => {
    if (weightsTouched || !snapshot) return;
    const stayWeight = stayDiff != null && stayDiff < -10 ? 45 : 30;
    const spendWeight = spendDiff != null && spendDiff < -10 ? 35 : 22;
    const rest = 100 - stayWeight - spendWeight;
    setWeights([stayWeight, spendWeight, Math.round(rest * .55), rest - Math.round(rest * .55)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId, stayDiff, spendDiff]);

  const stats = snapshot ? [
    ['외지인 방문자', formatNumber.format(snapshot.area.outside_visitors), '최신 방문자 데이터 · KTO 일별 집계', 'measured' as DataTier],
    ['관광 체류 강도', snapshot.observed_indices.stay_intensity?.toFixed(2) || '--', `${snapshot.observed_indices.base_ym} · ${snapshot.observed_indices.aggregation}`, 'derived' as DataTier],
    ['숙박 방문자 비중', snapshot.observed_indices.lodging_share_index?.toFixed(2) || '--', 'KTO 세부지표 2102 · 지수값', 'derived' as DataTier],
    ['1박 방문자', snapshot.observed_indices.one_night_index?.toFixed(2) || '--', 'KTO 세부지표 2103 · 지수값', 'derived' as DataTier],
    ['2박 방문자', snapshot.observed_indices.two_nights_index?.toFixed(2) || '--', 'KTO 세부지표 2104 · 지수값', 'derived' as DataTier],
    ['3박 이상 방문자', snapshot.observed_indices.three_plus_nights_index?.toFixed(2) || '--', 'KTO 세부지표 2105 · 지수값', 'derived' as DataTier],
    ['관광 소비 강도', snapshot.observed_indices.spend_intensity?.toFixed(2) || '--', `${snapshot.observed_indices.base_ym} · ${snapshot.observed_indices.aggregation}`, 'derived' as DataTier],
    ['관광객 다양성', snapshot.observed_indices.visitor_diversity?.toFixed(2) || '--', '연령별 방문객 구성 지표', 'derived' as DataTier],
    ['관광소비 다양성', snapshot.observed_indices.spend_diversity?.toFixed(2) || '--', '연령별 관광소비 구성 지표', 'derived' as DataTier],
    ['국제적 다양성', snapshot.observed_indices.international_diversity?.toFixed(2) || '--', '외국인 소비·국적 다양성 지표', 'derived' as DataTier],
    ['외지인 수요 백분위', `${snapshot.national_comparison.outside_visitor_percentile}%`, `${snapshot.national_comparison.municipality_count}개 지역 비교`, 'derived' as DataTier],
    ['외지인 비중', `${snapshot.visitor_mix.outside_share}%`, '현지인·외지인·외국인 합계 대비', 'derived' as DataTier],
    ['공간확산 지수', snapshot.observed_indices.spatial_dispersion?.toFixed(1) ?? '--', '관광지 집중도 기반 · 최근 30일 예측', 'derived' as DataTier],
  ] : [];

  return <>
    <header><div className="logo"><b>R</b>Regional Tourism Scan<i /></div><nav><a href="#map">최신 방문자 현황</a><a href="#diagnosis">관광현황 진단</a><a href="#priority">정책 우선순위</a><a href="#simulator">예산 시뮬레이터</a><a href="#tshift">효과검증 프레임</a></nav><button type="button">정책 브리프 PDF ↗</button></header>
    <main data-live-analysis="true">
      <section className="hero live-hero"><small>● DATA LAB CONNECTION · KTO TOURISM DATA LAB</small><div><article><h1>지금은 <em>Data Lab 최신 데이터</em>로<br />확인합니다.</h1><p>선택한 날짜와 지자체의 한국관광공사 통신 기반 방문자수를 서버에서 직접 조회합니다. 방문객 수가 아니라 &ldquo;무엇이 부족하고 어떤 정책이 먼저 필요한가&rdquo;를 진단합니다.</p><a href="#map">최신 데이터 보기 ↓</a></article><aside><span>DATA STATUS <b>{loading ? 'LOADING' : snapshot ? 'CONNECTED' : 'CONNECTION REQUIRED'}</b></span><strong>{snapshot ? 'OK' : '--'}</strong><div className="bars">{[28, 42, 36, 58, 49, 68, 57, 79].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div><p><b>{snapshot ? snapshot.source : 'KTO API 연결 확인 필요'}</b><span>{snapshot?.base_ymd || date.replace(/-/g, '.')}</span></p></aside></div></section>
      <section id="map" className="section"><div className="heading"><div><small>01 / REGION SELECT</small><h2>지역을 선택하면,<br /><em>진단이 시작됩니다</em></h2></div><div className="live-controls"><label>기준일<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label><button type="button" onClick={() => void loadAll()} disabled={loading}>{loading ? '조회 중' : '최신 데이터 조회'}</button></div></div>
        <div className="maplayout"><article className="map"><div>외지인 방문자 · KTO 일별 집계 <span>선택 지역을 클릭하세요</span></div><MapContainer center={[36.25, 127.8]} zoom={6.3} scrollWheelZoom={false}><TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />{regions.map((item) => <CircleMarker key={item.id} center={[item.lat, item.lng]} radius={item.id === regionId ? 18 : 10} pathOptions={{ color: item.id === regionId ? '#173b2b' : '#fff', weight: item.id === regionId ? 4 : 2, fillColor: item.id === regionId ? '#4f8249' : '#99c982', fillOpacity: .9 }} eventHandlers={{ click: () => select(item.id) }}><Tooltip>{item.province} {item.name}</Tooltip></CircleMarker>)}</MapContainer><small>※ 현재는 표본 4개 관광거점만 비교지역으로 사용합니다. 전국 경계·전수 진단은 다음 데이터 파이프라인 단계에서 확장합니다.</small></article>
          <aside className="summary"><small>SELECTED MUNICIPALITY</small><h3>{region.province} {region.name}</h3><label>최신 외지인 방문자</label><strong>{snapshot ? formatNumber.format(snapshot.area.outside_visitors) : '--'}</strong><i><b style={{ width: `${snapshot?.national_comparison.outside_visitor_percentile || 0}%` }} /></i><p>전국 비교 백분위 <b>{snapshot ? `${snapshot.national_comparison.outside_visitor_percentile}%` : '--'}</b></p><label>데이터 상태</label><div className="badges"><span>{snapshot ? '최신 KTO 방문자수' : '조회 대기'}</span><span>{snapshot ? snapshot.base_ymd : date.replace(/-/g, '')}</span></div><p className="desc">{error || (snapshot ? '외지인 방문자 기준의 실측 스냅샷입니다. 아래에서 유사지역 비교와 원인진단을 확인하세요.' : '조회 버튼을 눌러 KTO 방문자 데이터를 불러오세요.')}</p></aside></div>
      </section>
      <section id="diagnosis" className="section live-section"><div className="heading"><div><small>02 / OBSERVED METRICS</small><h2>{region.name}의<br /><em>실측 방문자 진단</em></h2></div><label>진단 대상<select value={regionId} onChange={(event) => select(event.target.value)}>{regions.map((item) => <option key={item.id} value={item.id}>{item.province} {item.name}</option>)}</select></label></div>
        <div className="live-stats">{stats.map(([label, value, caption, tier]) => <article key={label as string}><small>{label} <Tier tier={tier as DataTier} /></small><strong>{value}</strong><p>{caption}</p></article>)}</div>
      </section>
      <section id="peer" className="section live-section"><div className="heading"><div><small>03 / PEER GROUP COMPARISON</small><h2>전국 평균이 아니라,<br /><em>유사지역과 비교합니다</em></h2></div></div>
        <p className="peer-note">비교지역: {regions.filter((item) => item.id !== regionId).map((item) => item.name).join(' · ')} (표본 4개 관광거점 평균 기준)</p>
        <div className="cards"><article>{axes.map((axis) => <div className="sbar" key={axis.key}><span>{axis.label} <Tier tier={axis.tier} /></span><i><b style={{ width: axis.diff == null ? '0%' : `${Math.min(100, Math.abs(axis.diff))}%`, background: axis.diff == null ? '#d7ddd4' : axis.diff < 0 ? '#d45f43' : '#8fbc7e' }} /></i><em>{axis.diff == null ? '데이터 없음' : formatSigned(axis.diff)}</em></div>)}<p className="insight">{diagnosisText}</p></article></div>
      </section>
      <section id="priority" className="section live-section"><div className="heading"><div><small>04 / WEAKNESS → ROOT CAUSE → PRIORITY</small><h2>{region.name} 정책 우선순위<br /><em>TOP 3</em></h2></div><span className="badges"><span>{regionType} <Tier tier="modeled" /></span></span></div>
        <p className="peer-note">{diagnosisText}</p>
        {chartData.length > 0 && <div className="priority-chart">
          <small>축별 표본 비교지역 평균 대비 (%)</small>
          <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 42)}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }}>
              <XAxis type="number" tick={{ fontSize: 10, fill: '#7e8983' }} tickFormatter={(value) => `${value}%`} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={96} tick={{ fontSize: 11, fill: '#3f4a44' }} axisLine={false} tickLine={false} />
              <Bar dataKey="diff" radius={3}>
                {chartData.map((item) => <Cell key={item.name} fill={item.diff < 0 ? '#d45f43' : '#8fbc7e'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>}
        <div className="cards">{priorities.map((axis, index) => <article key={axis.key}><small>{index + 1}순위 · {axis.label} <Tier tier={axis.tier} /></small><strong>{axis.diff == null ? '데이터 연동 후 진단' : `${axis.label} 강화 필요`}</strong><p>근거: {axis.diff == null ? axis.note : `표본 비교지역 평균 대비 ${formatSigned(axis.diff)} (${classifyDiff(axis.diff)})`}</p></article>)}</div>
        {promotionLowPriority && <p className="insight low-priority">[우선순위 낮음] 추가 관광홍보 — 방문수요 자체는 이미 표본 비교지역 평균 수준이므로, 홍보 확대보다 위 우선순위 항목이 먼저 필요합니다.</p>}
      </section>
      <section id="simulator" className="sim"><div className="inside"><div className="heading"><div><small>05 / BUDGET PORTFOLIO SIMULATOR (보조 기능)</small><h2>다음 <em>{budget}억</em>은<br />어디에 배분할까요?</h2></div><p>위 정책 우선순위 진단을 기본값으로 제안합니다. 예산 시뮬레이터는 진단의 근거가 아니라, 진단 이후 의사결정을 탐색하는 보조 도구입니다.</p></div><div className="simgrid"><article className="budget"><label>증분 관광예산 <b>{budget}억 원</b><input type="range" min="3" max="30" value={budget} onChange={(event) => setBudget(Number(event.target.value))} /></label><small>3억 <span>30억</span></small><p>정책 항목별 가중치 <em>합계 {weightTotal}</em></p>{allocations.map((item, index) => <div className="allocation" key={item.name}><span>{item.name}</span><input aria-label={`${item.name} 가중치`} type="range" min="1" max="80" value={item.weight} onChange={(event) => { setWeightsTouched(true); setWeights((current) => current.map((value, itemIndex) => itemIndex === index ? Number(event.target.value) : value)); }} /><b>{item.amount.toFixed(1)}억</b></div>)}</article><aside className="impact"><small>SCENARIO PORTFOLIO</small><h3>현재 배분안</h3><strong>{budget}억</strong><span>{weightsTouched ? '실무자 조정 가중치 기준' : '진단 결과 기반 제안 가중치'}</span>{allocations.map((item) => <p key={item.name}><span>{item.name}</span><b>{item.weight / weightTotal * 100 | 0}%</b></p>)}<em>이 배분은 실무자 입력 시나리오입니다. R-GAP 자동 추천은 필수 4대 지표 적재 후 활성화됩니다.</em></aside></div></div></section>
      <section id="tshift" className="section"><div className="heading"><div><small>06 / 정책 시행 후 효과검증 프레임</small><h2>정책은 실험하고,<br /><em>효과는 증명합니다.</em></h2></div><p>현재 정책 성과가 아닌, 야간·계절 누수 정책의 사전 등록과 DiD 사후검증을 위한 실행 템플릿입니다.</p></div><div className="did">{[['01', '정책 패키지 설계', '체류 동선·야간 콘텐츠·지역 상권을 하나의 전환 여정으로 설계합니다.'], ['02', '비교지역 선정 · 변화 가설 등록', '성과지표, 대상·비교지역, 관찰기간을 사업 시작 전 고정합니다.'], ['03', '사전/사후 데이터 수집 · DiD 효과 리포트', '정책 전후 변화와 비교군 차이를 비교해 순효과를 검증합니다.']].map(([step, title, description]) => <article key={step}><small>{step}</small><h3>{title}</h3><p>{description}</p></article>)}</div></section>
      <section className="meta"><small>DATA INTERPRETATION / REQUIRED META INFO</small><div><b>실측값과 추정값을 구분합니다.</b><p>방문자수는 한국관광공사 통신 기반 지역별 방문자수 GW의 일별 집계입니다(실측). 체류·소비 강도와 숙박 비중·숙박일수별 방문자 지표, 공간확산(관광지 집중도 기반), 계절/시간 안정성(최근 7일 변동성)은 원천값을 정규화한 파생지표이며, 유사지역 비교·유형 판별은 규칙 기반 모델 추정값입니다. 숙박 세부값은 실제 비율이나 인원수가 아닌 KTO 지수값이며, 연 단위 계절성·75분위 프론티어는 향후 분석 가능 지표로 표시합니다.</p></div></section>
    </main><footer><div className="logo"><b>R</b>Regional Tourism Scan</div><small>Regional Tourism Scan · Regional Recoverable Tourism Value Gap Engine · KTO Tourism Data Challenge</small></footer>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
