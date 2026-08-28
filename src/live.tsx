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
    attraction_crowding_forecast: number | null;
    spatial_dispersion: number | null;
  };
  analysis: { status: 'partial'; message: string; missing_inputs: string[] };
};
type StabilitySnapshot = { window_days: number; areas: Record<string, { days_observed: number; stability_index: number | null }> };

// 4단계 데이터 신뢰도 라벨 (AGENTS.md §3.3)
type DataTier = 'measured' | 'derived' | 'modeled' | 'pending';
const tierLabel: Record<DataTier, string> = { measured: '원천자료', derived: '파생지표', modeled: '규칙기반 진단', pending: '산출 대기' };

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
const formatSigned = (value: number, unit: '%' | 'p') => `${value > 0 ? '+' : ''}${value.toFixed(1)}${unit}`;

const median = (values: number[]) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

// 지역명 받침 유무에 따라 '은/는' 조사를 선택한다 (예: 경주시는 / 강릉시는 / 안산시는 / 전주시는).
const topicParticle = (name: string) => {
  const code = name.charCodeAt(name.length - 1) - 0xac00;
  const hasBatchim = code >= 0 && code <= 11171 && code % 28 !== 0;
  return `${name}${hasBatchim ? '은' : '는'}`;
};

type Axis = { key: string; label: string; diff: number | null; unit: '%' | 'p'; threshold: number; tier: DataTier; note: string };

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

    // 단기 수요 안정성: 지역 방문자 원천이 지자체 단위로 필터링되지 않으므로,
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

  // 현재는 표본 4개 관광거점 비교다. 이상치 영향을 줄이기 위해 평균 대신 중앙값을 쓴다.
  // 전국 단위 유사 관광구조 군집은 §4.1(AGENTS.md) 고도화 단계에서 확장한다.
  const peers = regions.filter((item) => item.id !== regionId).map((item) => snapshots[item.id]).filter((item): item is LiveSnapshot => !!item);
  const peerMedian = (pick: (snap: LiveSnapshot) => number | null) => {
    const values = peers.map(pick).filter((value): value is number => value != null);
    return median(values);
  };
  const ratioDiff = (value: number | null, reference: number | null) => value == null || reference == null || reference === 0 ? null : ((value - reference) / reference) * 100;
  const pointDiff = (value: number | null, reference: number | null) => value == null || reference == null ? null : value - reference;

  const demandDiff = snapshot ? ratioDiff(snapshot.area.outside_visitors, peerMedian((s) => s.area.outside_visitors)) : null;
  const stayDiff = snapshot ? pointDiff(snapshot.observed_indices.stay_intensity, peerMedian((s) => s.observed_indices.stay_intensity)) : null;
  const lodgingDiff = snapshot ? pointDiff(snapshot.observed_indices.lodging_share_index, peerMedian((s) => s.observed_indices.lodging_share_index)) : null;
  const spendDiff = snapshot ? pointDiff(snapshot.observed_indices.spend_intensity, peerMedian((s) => s.observed_indices.spend_intensity)) : null;
  const dispersionDiff = snapshot ? pointDiff(snapshot.observed_indices.spatial_dispersion, peerMedian((s) => s.observed_indices.spatial_dispersion)) : null;

  // 단기 수요 안정성: 최근 7일 변동성에서 지역별 값을 뽑아 표본 중앙값과 비교한다.
  const stabilityValue = (id: string) => stability?.areas[id]?.stability_index ?? null;
  const peerStabilityMedian = () => {
    const values = regions.filter((item) => item.id !== regionId).map((item) => stabilityValue(item.id)).filter((value): value is number => value != null);
    return median(values);
  };
  const stabilityDiff = stability ? pointDiff(stabilityValue(regionId), peerStabilityMedian()) : null;

  const axes: Axis[] = [
    { key: 'demand', label: '관광수요', diff: demandDiff, unit: '%', threshold: 10, tier: 'derived', note: '외지인 방문자수 · 표본 비교지역 중앙값 대비 증감률' },
    { key: 'stay', label: '체류강도', diff: stayDiff, unit: 'p', threshold: 5, tier: 'derived', note: 'KTO 체류강도 지수 · 표본 비교지역 중앙값 대비 지수점수 차이' },
    { key: 'spend', label: '소비강도', diff: spendDiff, unit: 'p', threshold: 5, tier: 'derived', note: 'KTO 소비강도 지수 · 표본 비교지역 중앙값 대비 지수점수 차이' },
    { key: 'stayShare', label: '숙박비중', diff: lodgingDiff, unit: 'p', threshold: 5, tier: 'derived', note: 'KTO 숙박 비중 지수(2102) · 표본 비교지역 중앙값 대비 지수점수 차이' },
    { key: 'dispersion', label: '공간분산', diff: dispersionDiff, unit: 'p', threshold: 5, tier: 'pending', note: '관광지별 실제 방문 점유율이 필요합니다. 30일 혼잡 예측값은 공간 점유율이 아니므로 대체하지 않습니다.' },
    { key: 'stability', label: '단기 수요 안정성', diff: stabilityDiff, unit: 'p', threshold: 5, tier: 'derived', note: '최근 7일 외지인 방문자 변동계수 역산값 · 연간 계절성 지표가 아님' },
  ];
  const severity = (axis: Axis) => axis.diff == null ? 0 : Math.max(0, -axis.diff / axis.threshold);
  const isWeak = (diff: number | null, threshold: number) => diff != null && diff <= -threshold;

  // 원인분해: 현재 원천·파생지표로 판별 가능한 관계(수요→체류→소비 전환)만 규칙 기반으로 판단한다.
  // 숙박공급/야간콘텐츠 등 2차 구조지표가 연동되기 전까지는 CASE A/B 세부 유형 대신 상위 유형만 제시한다.
  let regionType = '진단 데이터 수집 중';
  let diagnosisText = '표본 데이터가 모두 모이면 비교지역 진단을 표시합니다.';
  if (isWeak(dispersionDiff, 5) && !isWeak(demandDiff, 10)) {
    regionType = '단일거점편중형';
    diagnosisText = `${topicParticle(region.name)} 관광수요·체류는 양호하지만, 특정 관광지에 방문이 집중돼 표본 비교지역보다 공간적으로 분산되지 못하고 있습니다. 관광지 간 연결·연계 콘텐츠가 우선 과제입니다.`;
  } else if (demandDiff != null && stayDiff != null && spendDiff != null) {
    if (!isWeak(demandDiff, 10) && (isWeak(stayDiff, 5) || isWeak(lodgingDiff, 5))) {
      regionType = '체류전환 부족형';
      diagnosisText = `${topicParticle(region.name)} 관광수요는 표본 중앙값 수준 이상이지만, 확보된 방문이 체류·숙박으로 충분히 이어지지 않고 있습니다. 숙박·체류 콘텐츠 강화가 우선 과제입니다.`;
    } else if (!isWeak(stayDiff, 5) && isWeak(spendDiff, 5)) {
      regionType = '소비연결 부족형';
      diagnosisText = `${topicParticle(region.name)} 관광객 유입과 체류에는 문제가 없지만, 확보된 관광수요가 소비로 충분히 연결되지 않고 있습니다. 상권 연계·소비 유도 정책이 우선 과제입니다.`;
    } else if (isWeak(demandDiff, 10) && isWeak(stayDiff, 5) && isWeak(spendDiff, 5)) {
      regionType = '복합취약형';
      diagnosisText = `${topicParticle(region.name)} 수요·체류·소비 전 구간이 표본 중앙값을 밑돌고 있어 개별 정책보다 구조적 진단이 우선 필요합니다.`;
    } else if (isWeak(demandDiff, 10)) {
      regionType = '수요부족형';
      diagnosisText = `${topicParticle(region.name)} 체류·소비 전환 자체는 양호하지만, 유입되는 관광수요 자체가 표본 중앙값보다 적습니다.`;
    } else {
      regionType = '안정형';
      diagnosisText = `${topicParticle(region.name)} 수요·체류·소비 축 모두 표본 중앙값과 비슷하거나 앞서 있습니다. 전국 유사구조 군집이 구축되면 다시 검증해야 합니다.`;
    }
  }

  // 정책 우선순위 TOP 3: 계산 가능한 축의 취약 정도로 순위를 매기고, 데이터가 없는 축은 "향후 분석" 항목으로 채운다.
  const rankedWeak = axes.filter((axis) => severity(axis) > 0).sort((a, b) => severity(b) - severity(a));
  const pendingAxes = axes.filter((axis) => axis.diff == null);
  const priorities = [...rankedWeak, ...pendingAxes].slice(0, 3);
  const promotionLowPriority = demandDiff != null && demandDiff >= -5;
  const chartData = axes.filter((axis) => axis.diff != null).map((axis) => ({ name: axis.label, diff: (axis.diff as number) / axis.threshold }));

  useEffect(() => {
    if (weightsTouched || !snapshot) return;
    const byKey = Object.fromEntries(axes.map((axis) => [axis.key, severity(axis)]));
    const raw = [byKey.stay + byKey.stayShare, byKey.spend, byKey.dispersion, byKey.stability];
    const totalSeverity = raw.reduce((sum, value) => sum + value, 0);
    setWeights(totalSeverity > 0 ? raw.map((value) => Math.max(1, Math.round(100 * value / totalSeverity))) : [25, 25, 25, 25]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId, stayDiff, lodgingDiff, spendDiff, dispersionDiff, stabilityDiff]);

  const stats = snapshot ? [
    ['외지인 방문자', formatNumber.format(snapshot.area.outside_visitors), '선택 기준일 · 통신자료 기반 KTO 추정치', 'measured' as DataTier],
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
    ['관광지 혼잡 예측', snapshot.observed_indices.attraction_crowding_forecast?.toFixed(1) ?? '--', '관광지별 최혼잡 시점=100 · 향후 30일 평균', 'modeled' as DataTier],
  ] : [];
  const methodologyTerms = [
    ['외지인 방문자', '이동통신 자료로 추정한 일별 방문자입니다. 관광 목적을 직접 확인한 관광객 수가 아니며 여러 날 체류하면 날짜별로 다시 집계될 수 있습니다.'],
    ['KTO 강도·숙박지수', '체류·소비·숙박 비중·숙박일수별 지표의 상대적 강도를 나타내는 지수점수입니다. 87.9를 숙박률 87.9%로 읽으면 안 됩니다.'],
    ['관광지 혼잡 예측', '각 관광지의 가장 붐비는 시기를 100으로 둔 향후 30일 상대 혼잡도입니다. 관광지 간 방문 점유율이 아니므로 공간분산 지수로 사용하지 않습니다.'],
    ['공간분산', '지역 방문이 여러 관광지에 얼마나 고르게 분산됐는지를 뜻합니다. 관광지별 실제 방문 점유율이 확보되기 전까지 산출 대기 상태입니다.'],
    ['단기 수요 안정성', '최근 7일 외지인 방문자의 변동계수(CV)를 100×(1−CV)로 역산한 파생지표입니다. 연간 계절성을 대신하지 않습니다.'],
    ['표본 중앙값 비교', '현재 4개 표본지역 중 선택지역을 제외한 3개 지역의 중앙값과 비교합니다. 아직 전국 유사 관광구조 군집이나 프론티어 비교는 아닙니다.'],
    ['취약도·예산배분', '축별 음의 편차를 임계값으로 나눈 표준화 결손도입니다. 체류·숙박, 소비, 공간, 단기 안정성 결손도의 합 대비 비중으로 초기 예산안을 배분합니다.'],
    ['TCEI', '체류(S)·소비(C)·공간분산(D)·계절안정성(B)의 백분위 기하평균입니다. 현재는 연간 계절성과 소비 잔차가 완성되지 않아 산출하지 않습니다.'],
    ['R-GAP', '유사 관광구조 75분위 프론티어 TCEI와 실제 TCEI의 양(+)의 차이입니다. 현재 화면의 규칙기반 유형·우선순위와 동일한 점수가 아닙니다.'],
  ];

  return <>
    <header><div className="logo"><b>R</b>Regional Tourism Scan<i /></div><nav><a href="#map">기준일 방문자 현황</a><a href="#diagnosis">관광현황 진단</a><a href="#priority">정책 우선순위</a><a href="#methodology">용어·알고리즘</a><a href="#simulator">예산 시뮬레이터</a></nav><button type="button">정책 브리프 PDF ↗</button></header>
    <main data-live-analysis="true">
      <section className="hero live-hero"><small>● DATA LAB CONNECTION · KTO TOURISM DATA LAB</small><div><article><h1>지금은 <em>Data Lab API 자료</em>로<br />확인합니다.</h1><p>선택한 기준일과 지자체의 한국관광공사 통신 기반 방문자 추정치와 관광지수를 서버에서 조회합니다. 표본 비교를 통해 &ldquo;무엇이 상대적으로 부족한가&rdquo;를 진단합니다.</p><a href="#map">기준일 데이터 보기 ↓</a></article><aside><span>DATA STATUS <b>{loading ? 'LOADING' : snapshot ? 'CONNECTED' : 'CONNECTION REQUIRED'}</b></span><strong>{snapshot ? 'OK' : '--'}</strong><div className="bars">{[28, 42, 36, 58, 49, 68, 57, 79].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div><p><b>{snapshot ? snapshot.source : 'KTO API 연결 확인 필요'}</b><span>{snapshot?.base_ymd || date.replace(/-/g, '.')}</span></p></aside></div></section>
      <section id="map" className="section"><div className="heading"><div><small>01 / REGION SELECT</small><h2>지역을 선택하면,<br /><em>진단이 시작됩니다</em></h2></div><div className="live-controls"><label>기준일<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label><button type="button" onClick={() => void loadAll()} disabled={loading}>{loading ? '조회 중' : '기준일 데이터 조회'}</button></div></div>
        <div className="maplayout"><article className="map"><div>외지인 방문자 · KTO 일별 집계 <span>선택 지역을 클릭하세요</span></div><MapContainer center={[36.25, 127.8]} zoom={6.3} scrollWheelZoom={false}><TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />{regions.map((item) => <CircleMarker key={item.id} center={[item.lat, item.lng]} radius={item.id === regionId ? 18 : 10} pathOptions={{ color: item.id === regionId ? '#173b2b' : '#fff', weight: item.id === regionId ? 4 : 2, fillColor: item.id === regionId ? '#4f8249' : '#99c982', fillOpacity: .9 }} eventHandlers={{ click: () => select(item.id) }}><Tooltip>{item.province} {item.name}</Tooltip></CircleMarker>)}</MapContainer><small>※ 현재는 표본 4개 관광거점만 비교지역으로 사용합니다. 전국 경계·전수 진단은 다음 데이터 파이프라인 단계에서 확장합니다.</small></article>
          <aside className="summary"><small>SELECTED MUNICIPALITY</small><h3>{region.province} {region.name}</h3><label>기준일 외지인 방문자</label><strong>{snapshot ? formatNumber.format(snapshot.area.outside_visitors) : '--'}</strong><i><b style={{ width: `${snapshot?.national_comparison.outside_visitor_percentile || 0}%` }} /></i><p>전국 270개 코드 내 백분위 <b>{snapshot ? `${snapshot.national_comparison.outside_visitor_percentile}%` : '--'}</b></p><label>데이터 상태</label><div className="badges"><span>{snapshot ? 'KTO 통신기반 추정치' : '조회 대기'}</span><span>{snapshot ? snapshot.base_ymd : date.replace(/-/g, '')}</span></div><p className="desc">{error || (snapshot ? '선택 기준일의 외지인 방문자 추정치입니다. 아래 비교진단은 현재 4개 표본지역 중앙값을 기준으로 합니다.' : '조회 버튼을 눌러 KTO 방문자 데이터를 불러오세요.')}</p></aside></div>
      </section>
      <section id="diagnosis" className="section live-section"><div className="heading"><div><small>02 / OBSERVED METRICS</small><h2>{region.name}의<br /><em>관측자료 기반 진단</em></h2></div><label>진단 대상<select value={regionId} onChange={(event) => select(event.target.value)}>{regions.map((item) => <option key={item.id} value={item.id}>{item.province} {item.name}</option>)}</select></label></div>
        <div className="live-stats">{stats.map(([label, value, caption, tier]) => <article key={label as string}><small>{label} <Tier tier={tier as DataTier} /></small><strong>{value}</strong><p>{caption}</p></article>)}</div>
      </section>
      <section id="peer" className="section live-section"><div className="heading"><div><small>03 / SAMPLE BENCHMARK</small><h2>현재는 전국 모형 전,<br /><em>표본 중앙값과 비교합니다</em></h2></div></div>
        <p className="peer-note">비교지역: {regions.filter((item) => item.id !== regionId).map((item) => item.name).join(' · ')} · 선택지역을 제외한 3개 표본 중앙값 기준</p>
        <div className="cards"><article>{axes.map((axis) => <div className="sbar" key={axis.key}><span>{axis.label} <Tier tier={axis.tier} /></span><i><b style={{ width: axis.diff == null ? '0%' : `${Math.min(100, 20 * Math.abs(axis.diff) / axis.threshold)}%`, background: axis.diff == null ? '#d7ddd4' : axis.diff < 0 ? '#d45f43' : '#8fbc7e' }} /></i><em>{axis.diff == null ? '데이터 없음' : formatSigned(axis.diff, axis.unit)}</em></div>)}<p className="insight">{diagnosisText}</p></article></div>
      </section>
      <section id="priority" className="section live-section"><div className="heading"><div><small>04 / WEAKNESS → ROOT CAUSE → PRIORITY</small><h2>{region.name} 정책 우선순위<br /><em>TOP 3</em></h2></div><span className="badges"><span>{regionType} <Tier tier="modeled" /></span></span></div>
        <p className="peer-note">{diagnosisText}</p>
        {chartData.length > 0 && <div className="priority-chart">
          <small>축별 표준화 편차 · −1은 축별 취약 임계값</small>
          <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 42)}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }}>
              <XAxis type="number" tick={{ fontSize: 10, fill: '#7e8983' }} tickFormatter={(value) => `${Number(value).toFixed(1)}×`} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={96} tick={{ fontSize: 11, fill: '#3f4a44' }} axisLine={false} tickLine={false} />
              <Bar dataKey="diff" radius={3}>
                {chartData.map((item) => <Cell key={item.name} fill={item.diff < 0 ? '#d45f43' : '#8fbc7e'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>}
        <div className="cards">{priorities.map((axis, index) => <article key={axis.key}><small>{index + 1}순위 · {axis.label} <Tier tier={axis.tier} /></small><strong>{axis.diff == null ? '데이터 연동 후 진단' : `${axis.label} 강화 필요`}</strong><p>근거: {axis.diff == null ? axis.note : `표본 중앙값 대비 ${formatSigned(axis.diff, axis.unit)} · 취약도 ${severity(axis).toFixed(2)}`}</p></article>)}</div>
        {promotionLowPriority && <p className="insight low-priority">[우선순위 낮음] 추가 관광홍보 — 방문수요가 표본 중앙값 수준이므로, 홍보 확대보다 위 우선순위 항목을 먼저 검토합니다.</p>}
      </section>
      <section id="methodology" className="section live-section methodology"><div className="heading"><div><small>05 / TERMS &amp; ALGORITHM</small><h2>용어와 계산법을<br /><em>투명하게 공개합니다</em></h2></div><p>현재 제공값과 향후 R-GAP 산출을 구분합니다.\n각 값의 단위·비교범위·한계를 함께 확인하세요.</p></div><div className="term-grid">{methodologyTerms.map(([term, description]) => <article key={term}><h3>{term}</h3><p>{description}</p></article>)}</div></section>
      <section id="simulator" className="sim"><div className="inside"><div className="heading"><div><small>06 / BUDGET PORTFOLIO SIMULATOR (보조 기능)</small><h2>다음 <em>{budget}억</em>은<br />어디에 배분할까요?</h2></div><p>표준화 결손도 비중을 초기값으로 제안합니다. 예산 시뮬레이터는 진단 이후 의사결정을 탐색하는 보조 도구이며 효과예측 모형이 아닙니다.</p></div><div className="simgrid"><article className="budget"><label>증분 관광예산 <b>{budget}억 원</b><input type="range" min="3" max="30" value={budget} onChange={(event) => setBudget(Number(event.target.value))} /></label><small>3억 <span>30억</span></small><p>정책 항목별 가중치 <em>합계 {weightTotal}</em></p>{allocations.map((item, index) => <div className="allocation" key={item.name}><span>{item.name}</span><input aria-label={`${item.name} 가중치`} type="range" min="1" max="80" value={item.weight} onChange={(event) => { setWeightsTouched(true); setWeights((current) => current.map((value, itemIndex) => itemIndex === index ? Number(event.target.value) : value)); }} /><b>{item.amount.toFixed(1)}억</b></div>)}</article><aside className="impact"><small>SCENARIO PORTFOLIO</small><h3>현재 배분안</h3><strong>{budget}억</strong><span>{weightsTouched ? '실무자 조정 가중치 기준' : '표준화 결손도 기반 초기값'}</span>{allocations.map((item) => <p key={item.name}><span>{item.name}</span><b>{Math.round(item.weight / weightTotal * 100)}%</b></p>)}<em>현재 배분은 표본 비교 진단에 따른 시나리오입니다. 75분위 프론티어가 완성되면 R-GAP 누수 기여도 기반 자동추천으로 전환합니다.</em></aside></div></div></section>
      <section id="tshift" className="section"><div className="heading"><div><small>07 / 정책 시행 후 효과검증 프레임</small><h2>정책은 실험하고,<br /><em>효과는 증명합니다.</em></h2></div><p>현재 정책 성과가 아닌, 야간·계절 누수 정책의 사전 등록과 DiD 사후검증을 위한 실행 템플릿입니다.</p></div><div className="did">{[['01', '정책 패키지 설계', '체류 동선·야간 콘텐츠·지역 상권을 하나의 전환 여정으로 설계합니다.'], ['02', '비교지역 선정 · 변화 가설 등록', '성과지표, 대상·비교지역, 관찰기간을 사업 시작 전 고정합니다.'], ['03', '사전/사후 데이터 수집 · DiD 효과 리포트', '정책 전후 변화와 비교군 차이를 비교해 순효과를 검증합니다.']].map(([step, title, description]) => <article key={step}><small>{step}</small><h3>{title}</h3><p>{description}</p></article>)}</div></section>
      <section className="meta"><small>DATA INTERPRETATION / REQUIRED META INFO</small><div><b>원천자료·파생지표·규칙기반 진단을 구분합니다.</b><p>방문자수는 이동통신 자료 기반의 KTO 추정치이며 관광객 실인원과 동일하지 않습니다. 체류·소비·숙박 지수는 비율이나 인원수가 아닌 KTO 지수점수입니다. 관광지 혼잡도는 KTO 예측값, 단기 안정성은 자체 파생지표이며 공간분산은 산출 대기입니다. 표본 비교 유형·우선순위는 규칙기반 진단입니다. 전국 유사구조 군집, 연간 계절성, 소비 잔차와 75분위 프론티어가 완성되기 전에는 TCEI·R-GAP으로 표시하지 않습니다.</p></div></section>
    </main><footer><div className="logo"><b>R</b>Regional Tourism Scan</div><small>Regional Tourism Scan · Regional Recoverable Tourism Value Gap Engine · KTO Tourism Data Challenge</small></footer>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
