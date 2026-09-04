import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleMarker, MapContainer, TileLayer, Tooltip } from 'react-leaflet';
import { Bar, BarChart, Cell, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import 'leaflet/dist/leaflet.css';
import './styles.css';
import './live.css';
// 전국 시군구 중심좌표(252개, 2024-12-31 기준). 통계청 SGIS 행정동 경계(KOGL 제1유형,
// vuski/admdongkor 저장소 CC BY 4.0 가공)를 시군구 단위로 평균해 산출했다. 구가 있는
// 시(수원·전주 등)는 구 단위로만 존재해, KTO 방문자 원천의 시 단위 집계 코드와는
// resolveCentroid()의 이름 접두 매칭으로 연결한다.
import sigunguCentroids from './kr_sigungu_centroids.json';

type Region = { id: string; name: string; province: string; lat: number; lng: number };
type LiveSnapshot = {
  source: string;
  source_status: 'live';
  base_ymd: string;
  area: { area_cd: string; area_name: string; resident_visitors: number; outside_visitors: number; foreign_visitors: number };
  national_comparison: { municipality_count: number; outside_visitor_percentile: number; window_days?: number };
  visitor_mix: { outside_share: number; foreign_share: number };
  observed_indices: {
    base_ym: string; aggregation: string;
    stay_intensity: number | null; spend_intensity: number | null;
    lodging_share_index: number | null; one_night_index: number | null;
    two_nights_index: number | null; three_plus_nights_index: number | null;
    visitor_diversity: number | null; spend_diversity: number | null; international_diversity: number | null;
    attraction_crowding_forecast: number | null;
    spatial_dispersion: number | null;
    spatial_dispersion_detail?: { hub_count: number; spread_km: number; method: string; is_visit_share_dispersion: false } | null;
  };
  analysis: { status: 'partial'; message: string; missing_inputs: string[] };
};
type StabilitySnapshot = { window_days: number; areas: Record<string, { days_observed: number; stability_index: number | null }> };
type PeerAxisSet = {
  stay_intensity: number | null; spend_intensity: number | null; lodging_share_index: number | null;
  dispersion_spread_km: number | null;
};
type PgCategory = 'PG-1' | 'PG-2' | 'PG-3' | 'PG-4';
type NationalPeer = {
  area_cd: string; area_name: string; rank: number; outside_visitors: number; percentile: number;
  population: number | null; population_density: number | null; pg_category: PgCategory | null;
  axes: PeerAxisSet | null;
  fetch_ok: boolean;
};
type PeerAxisStats = PeerAxisSet;
type PeerSampleSizes = { stay_intensity: number; spend_intensity: number; lodging_share_index: number; dispersion_spread_km: number };
type PeerGroup = {
  admin_type: string; capital_region: boolean; relaxed: boolean; criteria_note: string;
  pg_category: PgCategory | null; pg_category_label: string | null;
  count: number; peers: NationalPeer[]; medians: PeerAxisStats; top_quartile: PeerAxisStats;
  bottom_quartile: PeerAxisStats; sample_size: PeerSampleSizes;
  target_population: number | null; target_population_density: number | null;
};
// PG-1~4: 인구밀도(A축)만으로 근사한 도시 성격 분류 — 관광수요·숙박공급(outcome
// 변수)은 진단 자체가 판정하려는 값이라 분류 기준에서 뺐다(자기참조 순환 방지).
// 기존 행정유형(시/군/자치구)·수도권 여부 분리 위에 얹는 추가 정렬 기준이며,
// PG가 다르다고 후보에서 제외하지는 않는다.
const PG_CATEGORY_LABELS: Record<PgCategory, string> = {
  'PG-1': '도심/상업 집중형', 'PG-2': '도농복합 관광거점형', 'PG-3': '대도시 주거/위성형', 'PG-4': '일반 지방/농어촌형',
};
type NationalPeersSnapshot = {
  available: true;
  base_ymd: string;
  window_days: number;
  national: { municipality_count: number; target_percentile: number; demand_level: '충분' | '부족' };
  target: { area_cd: string; area_name: string; rank: number; outside_visitors: number; percentile: number } | null;
  peer_group: PeerGroup;
  peers_failed: number;
} | { available: false; reason: string; base_ymd: string };
type MoisBusinessSnapshot = {
  source: string;
  raw_record_count: number;
  operating_business_count: number;
  operating_tourism_accommodation_business_count: number;
  metric_type: string;
  not_a_room_count: boolean;
  province_group_record_count: number;
  low_sample: boolean;
  region?: { id: string; name: string; province: string } | null;
  items: Array<Record<string, string | null>>;
};
type RankedRegion = { area_cd: string; area_name: string; resident_visitors: number; outside_visitors: number; foreign_visitors: number; rank: number; percentile: number };
type NationalRankingSnapshot = { available: true; base_ymd: string; window_days: number; regions: RankedRegion[] } | { available: false; reason: string; base_ymd: string };

type PgRegionRow = {
  area_cd: string; area_name: string; province: string | null; admin_type: string; pg_category: PgCategory | null;
  percentile: number; population: number | null; population_density: number | null;
};
type PgCategoryRegionsSnapshot = { available: true; base_ymd: string; window_days: number; regions: PgRegionRow[] } | { available: false; reason: string; base_ymd: string };
type CompareRegionRow = PgRegionRow & { axes: PeerAxisSet | null; fetch_ok: boolean };
type CompareRegionsSnapshot = { available: true; base_ymd: string; window_days: number; regions: CompareRegionRow[] } | { available: false; reason: string; base_ymd: string };

const centroids = sigunguCentroids as Record<string, { name: string; province: string; lat: number; lng: number }>;
// KTO 방문자 원천은 구가 있는 시(수원·전주 등)를 시 단위로 집계해 별도 코드를 쓰지만,
// 중심좌표 표는 구 단위로만 존재한다. 코드가 표에 없으면 이름이 그 시로 시작하는
// 구들의 좌표를 평균해 근사 좌표를 만든다(예: "수원시" → 수원시장안구 등 4개 구 평균).
const resolveCentroid = (areaCd: string, areaName: string): { lat: number; lng: number; province: string } | null => {
  const direct = centroids[areaCd];
  if (direct) return direct;
  const districts = Object.values(centroids).filter((entry) => entry.name.startsWith(areaName));
  if (!districts.length) return null;
  return {
    lat: districts.reduce((sum, entry) => sum + entry.lat, 0) / districts.length,
    lng: districts.reduce((sum, entry) => sum + entry.lng, 0) / districts.length,
    province: districts[0].province,
  };
};

// 세종특별자치시처럼 도 이름과 지역명이 같으면 "세종특별자치시 세종특별자치시"로 겹쳐 보이므로 한 번만 표시한다.
const regionLabel = (item: { province: string; name: string }) => item.province === item.name ? item.name : `${item.province} ${item.name}`;
// PG-3에는 서울·부산·대구 등 서로 다른 광역시의 같은 이름 구가 함께 있다.
// 목록과 비교표에서 시·도를 항상 앞에 붙여 "중구" 같은 모호한 이름을 없앤다.
const pgRegionLabel = (item: { province: string | null; area_name: string }) =>
  item.province && item.province !== item.area_name ? `${item.province} ${item.area_name}` : item.area_name;

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
const formatManUnit = (value: number) => `${(value / 10000).toFixed(1)}만`;
const formatSigned = (value: number, unit: 'p' | 'km') => `${value > 0 ? '+' : ''}${value.toFixed(1)}${unit === 'km' ? ' km' : unit}`;

const median = (values: number[]) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

// 백엔드 _quantile()과 동일한 선형보간 분위수 — 안정성 축처럼 서버가 미리 계산해
// 주지 않는 값도 같은 방식으로 Peer 하위 25%를 구해 취약 판정 기준을 통일한다.
const quantile = (values: number[], q: number) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const position = q * (sorted.length - 1);
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, sorted.length - 1);
  const fraction = position - lower;
  return sorted[lower] + (sorted[upper] - sorted[lower]) * fraction;
};

// 지역명 받침 유무에 따라 '은/는' 조사를 선택한다 (예: 경주시는 / 강릉시는 / 안산시는 / 전주시는).
const topicParticle = (name: string) => {
  const code = name.charCodeAt(name.length - 1) - 0xac00;
  const hasBatchim = code >= 0 && code <= 11171 && code % 28 !== 0;
  return `${name}${hasBatchim ? '은' : '는'}`;
};

type Axis = {
  key: string; label: string; diff: number | null; unit: 'p' | 'km'; tier: DataTier; note: string;
  value: number | null; peerMedian: number | null; peerTop25: number | null; peerBottom25: number | null;
  peerSampleSize: number;
};

// Peer 3~4곳만으로 뽑은 하위 25%는 선형보간상 "두 번째로 작은 값"에 가까워, 그
// 밑으로만 내려가면 실제로는 흔한 편차인데도 "취약"으로 과다 판정된다(실데이터
// 100개 지역 배치 진단에서 확인 — 이 최소 표본 미만이면 취약 여부를 판정하지
// 않고 "판정 보류"로 남긴다).
const MIN_PEER_SAMPLE_FOR_WEAK_JUDGMENT = 4;

function App() {
  // 기본 진단 대상: 경주시는 전국 배치(data/national_diagnosis_findings.md)에서
  // 취약 판정을 받은 축이 하나도 없어("성과우수형") 정책 우선순위 화면이 빈
  // 상태로 뜬다 — 진단 기능을 보여주는 첫 화면으로는 부적절하다. 평택시는 같은
  // 배치에서 실제로 소비강도가 취약(Peer 하위25% 대비 IQR 1.71배 미달)으로
  // 잡힌, 재현 가능한 사례다.
  const [regionId, setRegionId] = useState('41220');
  // KTO GW는 제공 완료된 기준일만 조회할 수 있다. 검증된 최근 기준일을 초기값으로 둔다.
  const [date, setDate] = useState('2025-08-25');
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [stability, setStability] = useState<StabilitySnapshot | null>(null);
  const [nationalPeers, setNationalPeers] = useState<NationalPeersSnapshot | null>(null);
  const [nationalRanking, setNationalRanking] = useState<NationalRankingSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [businessOperation, setBusinessOperation] = useState<'info' | 'history'>('info');
  // 제주시가 4개 지원 지역 중 원자료가 가장 풍부하다(원자료 716건·관광숙박업
  // 170건 vs 경주 4건·0건) — 첫 조회에서 빈 결과에 가까운 화면을 보여주지
  // 않도록 기본값으로 둔다.
  const [businessRegionId, setBusinessRegionId] = useState('50110');
  const [businessBaseDate, setBusinessBaseDate] = useState(date.replace(/-/g, ''));
  const [businessData, setBusinessData] = useState<MoisBusinessSnapshot | null>(null);
  const [businessLoading, setBusinessLoading] = useState(false);
  const [businessError, setBusinessError] = useState('');
  const [lodgingEvidence, setLodgingEvidence] = useState<MoisBusinessSnapshot | { available: false } | null>(null);

  // PG 카테고리별 지역 비교: 카테고리를 고르면 그 안의 지역 목록을 보여주고,
  // 사용자가 직접 2~5곳을 골라 지표를 나란히 비교할 수 있게 한다.
  const [pgCategoryList, setPgCategoryList] = useState<PgCategoryRegionsSnapshot | null>(null);
  const [selectedPgCategory, setSelectedPgCategory] = useState<PgCategory>('PG-1');
  const [pgRegionQuery, setPgRegionQuery] = useState('');
  const [compareSelection, setCompareSelection] = useState<string[]>([]);
  const [compareResult, setCompareResult] = useState<CompareRegionsSnapshot | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState('');

  // 진단 대상 확장: 전국 스캔이 뜨면 그 목록에서, 아직이면 기존 4개 관광거점에서 이름을 찾는다.
  const rankingRegions = nationalRanking?.available ? nationalRanking.regions : [];
  const region = useMemo(() => {
    const ranked = rankingRegions.find((item) => item.area_cd === regionId);
    if (ranked) return { id: ranked.area_cd, name: ranked.area_name, province: resolveCentroid(ranked.area_cd, ranked.area_name)?.province || '' };
    const fixed = regions.find((item) => item.id === regionId);
    if (fixed) return fixed;
    // 전국 스캔이 아직 안 뜬 첫 렌더에서도(예: 4개 관광거점 밖 지역이 기본값일 때)
    // 지역명이 코드 숫자로 잠깐 보이지 않도록, 정적 중심좌표 표에서 바로 찾는다.
    const known = centroids[regionId];
    if (known) return { id: regionId, name: known.name, province: known.province };
    return { id: regionId, name: snapshot?.area.area_name || regionId, province: '' };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId, nationalRanking, snapshot]);

  // 전국에서 좌표를 확인할 수 있는 기초지자체(시/군/자치구)만 지도 핀·선택지로 확장한다.
  // 스캔 전에는 기존 4개 관광거점만 보여준다. 일반구("수원시 팔달구"처럼 상위 시 이름이
  // 붙어 공백이 생기는 항목)만 제외한다 — 자치구(강남구·해운대구)는 기초지자체이므로 유지하되,
  // Peer Group을 만들 때는 백엔드가 시/군/자치구를 서로 다른 풀로 분리해 비교한다.
  const isIndependentMunicipality = (name: string) => !name.includes(' ');
  const mapRegions = useMemo(() => {
    if (!rankingRegions.length) return regions;
    const resolved = rankingRegions
      .filter((item) => isIndependentMunicipality(item.area_name))
      .map((item) => {
        const coord = resolveCentroid(item.area_cd, item.area_name);
        return coord ? { id: item.area_cd, name: item.area_name, province: coord.province, lat: coord.lat, lng: coord.lng } : null;
      })
      .filter((item): item is Region => item !== null);
    return resolved.length ? resolved : regions;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nationalRanking]);

  // 검색창: 이름이 같은 지역이 여러 도에 있을 수 있어(예: 중구) 도 이름을 붙여 매칭한다.
  const [regionQuery, setRegionQuery] = useState('');
  const regionMatches = useMemo(() => {
    const query = regionQuery.trim();
    if (!query) return [];
    return mapRegions.filter((item) => `${item.province}${item.name}`.includes(query)).slice(0, 8);
  }, [regionQuery, mapRegions]);

  const select = (next: string) => { setRegionId(next); };

  // ① 전국 관광수요 스캔: 같은 기준일 전국 시군구 방문자 순위를 한 번만 조회해
  // 지도 핀과 진단 대상 선택지를 넓힌다. 기준일이 바뀔 때만 다시 부른다.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${apiBase}/v1/analysis/national-ranking`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ base_ymd: date.replace(/-/g, ''), window_days: 7 }),
        });
        const data = (await response.json()) as NationalRankingSnapshot;
        if (!cancelled) setNationalRanking(response.ok ? data : { available: false, reason: '전국 스캔 요청에 실패했습니다.', base_ymd: date.replace(/-/g, '') });
      } catch (cause) {
        if (!cancelled) setNationalRanking({ available: false, reason: cause instanceof Error ? cause.message : '전국 스캔 요청에 실패했습니다.', base_ymd: date.replace(/-/g, '') });
      }
    })();
    return () => { cancelled = true; };
  }, [date]);

  // PG 카테고리별 지역 목록: 기준일이 바뀔 때만 다시 불러온다. 서버가 이미
  // 캐시하는 순위·인구 조회를 재사용하므로, 이 섹션을 열지 않는 사용자에게도
  // 추가 KTO 호출 비용은 없다(national-ranking과 같은 캐시 키를 공유).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${apiBase}/v1/analysis/pg-categories`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ base_ymd: date.replace(/-/g, ''), window_days: 7 }),
        });
        const data = (await response.json()) as PgCategoryRegionsSnapshot;
        if (!cancelled) setPgCategoryList(response.ok ? data : { available: false, reason: 'PG 카테고리 목록 요청에 실패했습니다.', base_ymd: date.replace(/-/g, '') });
      } catch (cause) {
        if (!cancelled) setPgCategoryList({ available: false, reason: cause instanceof Error ? cause.message : 'PG 카테고리 목록 요청에 실패했습니다.', base_ymd: date.replace(/-/g, '') });
      }
    })();
    return () => { cancelled = true; setCompareSelection([]); setCompareResult(null); };
  }, [date]);

  const runCompareRegions = async () => {
    if (compareSelection.length < 2) { setCompareError('2곳 이상 선택하세요.'); return; }
    setCompareLoading(true); setCompareError(''); setCompareResult(null);
    try {
      const response = await fetch(`${apiBase}/v1/analysis/compare-regions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cds: compareSelection, base_ymd: date.replace(/-/g, ''), window_days: 7 }),
      });
      const data = (await response.json()) as CompareRegionsSnapshot;
      if (!response.ok || !data.available) throw new Error(!data.available ? data.reason : '비교 요청에 실패했습니다.');
      setCompareResult(data);
    } catch (cause) {
      setCompareError(cause instanceof Error ? cause.message : '비교 요청에 실패했습니다.');
    } finally { setCompareLoading(false); }
  };

  const toggleCompareSelection = (areaCd: string) => {
    setCompareSelection((current) => current.includes(areaCd)
      ? current.filter((id) => id !== areaCd)
      : current.length >= 5 ? current : [...current, areaCd]);
  };

  // ② 잘 되는 곳들 중 취약점 탐지: 진단 대상 하나의 심층 지표 + 같은 기준일 전국
  // 순위에서 동적으로 뽑은 상위 수요 지역을 비교군으로 삼는다. 진단 대상이나
  // 기준일이 바뀔 때마다 다시 계산하며, "기준일 데이터 조회" 버튼으로도 다시 부를 수 있다.
  const loadDiagnosis = async (targetRegionId: string, targetDate: string, isCancelled: () => boolean) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${apiBase}/v1/analysis/live-visitor`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cd: targetRegionId, base_ymd: targetDate }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '실데이터 요청에 실패했습니다.');
      if (!isCancelled()) setSnapshot(data as LiveSnapshot);
    } catch (cause) {
      if (isCancelled()) return;
      setSnapshot(null);
      setError(cause instanceof Error ? cause.message : '실데이터 요청에 실패했습니다.');
    } finally { if (!isCancelled()) setLoading(false); }

    let peerIds: string[] = [];
    try {
      const response = await fetch(`${apiBase}/v1/analysis/national-peers`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cd: targetRegionId, base_ymd: targetDate, peer_count: 6, window_days: 7 }),
      });
      const data = (await response.json()) as NationalPeersSnapshot;
      if (isCancelled()) return;
      setNationalPeers(response.ok ? data : { available: false, reason: (data as { reason?: string }).reason || '전국 스캔 요청에 실패했습니다.', base_ymd: targetDate });
      if (response.ok && data.available) peerIds = data.peer_group.peers.map((peer) => peer.area_cd);
    } catch (cause) {
      if (isCancelled()) return;
      setNationalPeers({ available: false, reason: cause instanceof Error ? cause.message : '전국 스캔 요청에 실패했습니다.', base_ymd: targetDate });
    }

    // 단기 수요 안정성: 방문자 원천이 지자체 단위로 필터링되지 않으므로, 진단
    // 대상 + 전국 동적 비교군을 한 번에 조회해 공유 응답에서 변동성을 계산한다.
    // 전국 비교군을 못 구했을 때만 표본 4개 지역으로 되돌아간다.
    try {
      const areaCds = peerIds.length ? [targetRegionId, ...peerIds] : regions.map((item) => item.id);
      const response = await fetch(`${apiBase}/v1/analysis/visitor-stability`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ area_cds: areaCds, base_ymd: targetDate, window_days: 7 }),
      });
      const data = await response.json();
      if (!isCancelled()) setStability(response.ok ? data : null);
    } catch { if (!isCancelled()) setStability(null); }
  };

  useEffect(() => {
    let cancelled = false;
    void loadDiagnosis(regionId, date.replace(/-/g, ''), () => cancelled);
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, regionId]);

  const loadBusinessData = async () => {
    setBusinessLoading(true);
    setBusinessError('');
    setBusinessData(null);
    try {
      const params = new URLSearchParams();
      if (businessOperation === 'history') params.set('base_date', businessBaseDate.replace(/-/g, ''));
      const response = await fetch(`${apiBase}/v1/data-sources/mois/tourism-business/region/${businessRegionId}/${businessOperation}${params.toString() ? `?${params}` : ''}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '관광사업자 자료 요청에 실패했습니다.');
      setBusinessData(data as MoisBusinessSnapshot);
    } catch (cause) {
      setBusinessError(cause instanceof Error ? cause.message : '관광사업자 자료 요청에 실패했습니다.');
    } finally { setBusinessLoading(false); }
  };

  // 전국 비교(내 위치 확인)와 Peer Group 비교(내 문제 찾기)를 분리한다.
  // 전국은 관광수요의 절대적 위치 판단에만 쓰고, 체류·숙박·소비 등 취약성 판단은
  // 반드시 행정유형·수도권 여부가 같고 관광수요 규모가 유사한 Peer Group 안에서만 한다.
  const peersAvailable = nationalPeers?.available === true;
  const peerGroup = peersAvailable ? nationalPeers.peer_group : null;
  const pointDiff = (value: number | null, reference: number | null) => value == null || reference == null ? null : value - reference;

  const nationalPercentile = peersAvailable ? nationalPeers.national.target_percentile : null;
  const demandLevel = peersAvailable ? nationalPeers.national.demand_level : null;

  const stayDiff = snapshot && peerGroup ? pointDiff(snapshot.observed_indices.stay_intensity, peerGroup.medians.stay_intensity) : null;
  const lodgingDiff = snapshot && peerGroup ? pointDiff(snapshot.observed_indices.lodging_share_index, peerGroup.medians.lodging_share_index) : null;
  const spendDiff = snapshot && peerGroup ? pointDiff(snapshot.observed_indices.spend_intensity, peerGroup.medians.spend_intensity) : null;
  const dispersionDiff = snapshot && peerGroup ? pointDiff(snapshot.observed_indices.spatial_dispersion, peerGroup.medians.dispersion_spread_km) : null;

  // 단기 수요 안정성: 최근 7일 변동성에서 진단 대상과 Peer Group의 값을 뽑아 중앙값과 비교한다.
  const stabilityValue = (id: string) => stability?.areas[id]?.stability_index ?? null;
  const stabilityPeerIds = peerGroup ? peerGroup.peers.map((peer) => peer.area_cd) : regions.filter((item) => item.id !== regionId).map((item) => item.id);
  const peerStabilityValues = stabilityPeerIds.map(stabilityValue).filter((value): value is number => value != null);
  const peerStabilityMedian = median(peerStabilityValues);
  const stabilityDiff = stability ? pointDiff(stabilityValue(regionId), peerStabilityMedian) : null;

  const peerBasisNote = peerGroup
    ? `${peerGroup.criteria_note} ${peerGroup.count}곳 중앙값 대비${peerGroup.relaxed ? ' (수도권 조건 완화)' : ''}`
    : 'Peer Group 조회 대기';
  const peerStabilityBottom25 = quantile(peerStabilityValues, 0.25);
  const axes: Axis[] = [
    { key: 'stay', label: '체류강도', diff: stayDiff, unit: 'p', tier: 'derived', note: `KTO 체류강도 지수 · ${peerBasisNote}`,
      value: snapshot?.observed_indices.stay_intensity ?? null, peerMedian: peerGroup?.medians.stay_intensity ?? null,
      peerTop25: peerGroup?.top_quartile.stay_intensity ?? null, peerBottom25: peerGroup?.bottom_quartile.stay_intensity ?? null,
      peerSampleSize: peerGroup?.sample_size.stay_intensity ?? 0 },
    { key: 'spend', label: '소비강도', diff: spendDiff, unit: 'p', tier: 'derived', note: `KTO 소비강도 지수 · ${peerBasisNote}`,
      value: snapshot?.observed_indices.spend_intensity ?? null, peerMedian: peerGroup?.medians.spend_intensity ?? null,
      peerTop25: peerGroup?.top_quartile.spend_intensity ?? null, peerBottom25: peerGroup?.bottom_quartile.spend_intensity ?? null,
      peerSampleSize: peerGroup?.sample_size.spend_intensity ?? 0 },
    { key: 'stayShare', label: '숙박비중', diff: lodgingDiff, unit: 'p', tier: 'derived', note: `KTO 숙박 비중 지수(2102) · ${peerBasisNote}`,
      value: snapshot?.observed_indices.lodging_share_index ?? null, peerMedian: peerGroup?.medians.lodging_share_index ?? null,
      peerTop25: peerGroup?.top_quartile.lodging_share_index ?? null, peerBottom25: peerGroup?.bottom_quartile.lodging_share_index ?? null,
      peerSampleSize: peerGroup?.sample_size.lodging_share_index ?? 0 },
    // 공간확산은 방향(넓게 퍼진 것이 좋은지 좁게 모인 것이 좋은지)이 자명하지
    // 않다 — 다른 축은 전부 "낮으면 나쁘다"가 성립하지만, 관광지가 모여 있는
    // 것은 동선 설계상 장점일 수도 있다. 그래서 Peer 비교값은 참고용으로만
    // 보여주고, 아래 rankedWeak/우선순위 판정에서는 제외한다(informational로
    // 별도 처리 — isWeak/근소 배지를 씌우지 않는다).
    { key: 'dispersion', label: '중심지 공간확산', diff: dispersionDiff, unit: 'km', tier: peerGroup?.medians.dispersion_spread_km != null ? 'derived' : 'pending',
      note: `내비게이션 중심 관광지 좌표의 RMS 확산거리(km) · ${peerBasisNote} · 참고용(방향 미검증 — 우선순위 판정에서 제외)`,
      value: snapshot?.observed_indices.spatial_dispersion ?? null, peerMedian: peerGroup?.medians.dispersion_spread_km ?? null,
      peerTop25: peerGroup?.top_quartile.dispersion_spread_km ?? null, peerBottom25: peerGroup?.bottom_quartile.dispersion_spread_km ?? null,
      peerSampleSize: peerGroup?.sample_size.dispersion_spread_km ?? 0 },
    { key: 'stability', label: '단기 수요 안정성', diff: stabilityDiff, unit: 'p', tier: 'derived', note: `최근 7일 외지인 방문자 변동계수 역산값 · ${peerBasisNote} · 연간 계절성 지표가 아님`,
      value: stabilityValue(regionId), peerMedian: peerStabilityMedian,
      peerTop25: quantile(peerStabilityValues, 0.75), peerBottom25: peerStabilityBottom25,
      peerSampleSize: peerStabilityValues.length },
  ];

  // 취약 판정은 "Peer 중앙값보다 몇 p 낮은가"라는 고정 상수가 아니라, 이 Peer
  // Group 자체의 분포와 비교한다 — 그룹마다 실제 편차가 다르므로, 같은 ±5p라도
  // 어떤 그룹에서는 흔한 편차이고 어떤 그룹에서는 이례적일 수 있다. 판정에는
  // 두 개의 관문이 있다.
  //
  // ① Peer 표본이 4곳 미만이면 문턱 자체가 불안정하므로(선형보간상 "두 번째로
  //    작은 값"에 가까워짐) 아예 판정하지 않는다 — 전국 100개 지역 배치에서
  //    표본 3~4곳짜리 문턱이 취약률을 56%까지 부풀리는 것을 확인했다.
  // ② 하위 25% 문턱을 "겨우" 밑도는 것과 "뚜렷하게" 밑도는 것을 구분한다.
  //    미달 폭을 보지 않으면 취약 판정의 절반이 문턱 대비 3% 이내(최소 0.19%,
  //    금정구 체류강도 66.95 vs 67.08)의 오차 수준 차이로 만들어지고, 그 상태로는
  //    "1순위 정책 과제"의 근거가 될 수 없다.
  //
  // 미달 폭의 단위는 그 Peer Group의 사분위 범위(IQR = 상위25% − 하위25%)다.
  // 또래가 촘촘히 모인 그룹에서는 작은 차이도 의미가 있고, 원래 편차가 큰
  // 그룹에서는 같은 차이가 흔한 변동이기 때문이다. 0.5 IQR은 상자그림의 표준
  // 이상치 울타리(Q1 − 1.5×IQR)를 정책진단용으로 완화한 값이다 — 통계적
  // 이상치가 아니라 "또래보다 뚜렷하게 뒤처진 곳"을 찾는 것이 목적이다.
  // 이 문턱에서 전국 100개 지역 축 판정 취약률은 29% → 15%로 내려가, 하위 25%
  // 규칙이 정의상 만들어내던 25% 언저리의 고정값에서 실제로 벗어난다.
  const WEAK_MATERIALITY_IQR = 0.5;

  const peerIqr = (axis: Axis) => axis.peerTop25 != null && axis.peerBottom25 != null
    ? axis.peerTop25 - axis.peerBottom25 : null;
  // 하위 25% 미만이지만 미달 폭이 문턱에 못 미치는 상태(= "근소")도 값 자체는
  // 그대로 보여준다. 취약이라고 부르지 않을 뿐, 숨기지는 않는다.
  const isBelowBottomQuartile = (axis: Axis) => axis.value != null && axis.peerBottom25 != null
    && axis.peerSampleSize >= MIN_PEER_SAMPLE_FOR_WEAK_JUDGMENT && axis.value < axis.peerBottom25;
  // 취약도 = 하위 25% 문턱 아래로 IQR의 몇 배만큼 내려가 있는가. 단위가 다른
  // 축들을 같은 척도로 비교할 수 있고, 그대로 우선순위 정렬 기준이 된다.
  const severity = (axis: Axis) => {
    const iqr = peerIqr(axis);
    if (!isBelowBottomQuartile(axis) || iqr == null || iqr <= 0) return 0;
    return (axis.peerBottom25! - axis.value!) / iqr;
  };
  const isWeak = (axis: Axis) => severity(axis) >= WEAK_MATERIALITY_IQR;
  // 차트·막대 정규화 단위도 같은 IQR을 쓴다(산출 불가 시 5p로 대체).
  const axisSpan = (axis: Axis) => {
    const iqr = peerIqr(axis);
    return iqr != null && iqr > 0.01 ? iqr : 5;
  };

  // ①전국 위치 → ②수요 충분 여부 → ③Peer 성과비교 → ④유형판정 → ⑤원인분해.
  // "관광수요가 이미 확보된 지역의 숨은 취약점을 찾는다"는 문제의식을 지키기 위해,
  // 수요부족형/잠재력형은 심층 체류·숙박 원인분해 대신 1차 판정만 제공한다.
  const coreAxes = [
    { axis: axes[0], key: 'stay' as const }, { axis: axes[1], key: 'spend' as const }, { axis: axes[2], key: 'stayShare' as const },
  ];
  const weakCoreAxes = coreAxes.filter(({ axis }) => isWeak(axis));
  // 세 핵심 축 모두 값을 받았을 뿐 아니라, 그 값이 "취약 여부를 판정할 만큼"
  // Peer 표본을 확보했을 때만 유형을 확정한다. 이 관문이 없으면 Peer 표본이
  // 1~3곳뿐인 지역(예: 밀집 수도권 시 그룹)은 어떤 축도 isWeak를 통과할 수
  // 없어 근거 없이 항상 "성과우수형"으로 잘못 분류된다 — 우수해서가 아니라
  // 판정할 수 없어서인데, 화면은 그 차이를 구분하지 못했다.
  const coreSampleSufficient = coreAxes.every(({ axis }) => axis.peerSampleSize >= MIN_PEER_SAMPLE_FOR_WEAK_JUDGMENT);
  const hasCoreData = stayDiff != null && spendDiff != null && lodgingDiff != null && coreSampleSufficient;
  const hasWeakPerformance = weakCoreAxes.length > 0;

  const coreDiffsArrived = stayDiff != null && spendDiff != null && lodgingDiff != null;
  let macroType: '수요부족형' | '잠재력형' | '성과우수형' | '숨은취약형' | null = null;
  let regionType = coreDiffsArrived && !coreSampleSufficient ? '판정 보류 · Peer 표본 부족' : '진단 데이터 수집 중';
  let diagnosisText = peersAvailable
    ? (coreDiffsArrived && !coreSampleSufficient
        ? `Peer Group(${peerGroup?.criteria_note})의 표본이 적어(${coreAxes.map(({ axis }) => axis.peerSampleSize).join('/')}곳) 체류·소비·숙박 취약 여부를 판정할 수 없습니다. "성과우수형"으로 단정하지 않고 유형 판정을 보류합니다.`
        : 'Peer Group 데이터가 모두 모이면 진단을 표시합니다.')
    : '전국 관광수요 스캔 결과를 기다리는 중입니다.';

  if (demandLevel != null && hasCoreData) {
    if (demandLevel === '부족') {
      macroType = hasWeakPerformance ? '수요부족형' : '잠재력형';
      regionType = macroType;
      diagnosisText = macroType === '수요부족형'
        ? `${topicParticle(region.name)} 관광수요 자체가 전국 상위 ${(100 - (nationalPercentile ?? 0)).toFixed(0)}%대로 아직 충분하지 않습니다. 체류·숙박 전환보다 관광수요 확보가 우선 과제입니다.`
        : `${topicParticle(region.name)} 관광수요는 전국 기준으로 아직 낮지만, Peer Group(${peerGroup?.criteria_note})과 비교했을 때 체류·숙박·소비 성과는 상대적으로 양호합니다. 유입 확대 정책을 우선 검토할 수 있는 잠재력형입니다.`;
    } else {
      macroType = hasWeakPerformance ? '숨은취약형' : '성과우수형';
      regionType = macroType;
      if (macroType === '성과우수형') {
        diagnosisText = `${topicParticle(region.name)} 관광수요는 전국 상위 ${(100 - (nationalPercentile ?? 0)).toFixed(0)}%대로 충분하고, Peer Group 대비 체류·숙박·소비 성과도 양호합니다. 신규 정책보다 현재 수준의 유지·고도화가 적절합니다.`;
      } else {
        // 숨은취약형: 가장 취약한 축을 원인분해해 세부 유형을 붙인다.
        const weakest = [...weakCoreAxes].sort((a, b) => severity(a.axis) - severity(b.axis)).reverse()[0];
        if (weakest.key === 'stay' || weakest.key === 'stayShare') {
          regionType = '숨은취약형 · 체류전환 부족';
          diagnosisText = `${topicParticle(region.name)} 관광수요는 이미 충분(전국 상위 ${(100 - (nationalPercentile ?? 0)).toFixed(0)}%)하지만, 같은 조건의 Peer Group(${peerGroup?.criteria_note})과 비교하면 체류·숙박 전환이 상대적으로 낮습니다. 관광사업자 원자료로 숙박 공급 여건을 확인해 원인을 좁혀야 합니다.`;
        } else if (weakest.key === 'spend') {
          regionType = '숨은취약형 · 소비연결 부족';
          diagnosisText = `${topicParticle(region.name)} 관광수요와 체류는 Peer Group 수준이지만, 확보된 방문이 소비로 충분히 연결되지 않고 있습니다. 상권 연계·소비 유도 정책이 우선 과제입니다.`;
        } else {
          regionType = '숨은취약형';
          diagnosisText = `${topicParticle(region.name)} 관광수요는 이미 충분하지만 Peer Group 대비 일부 지표가 취약합니다. 아래 우선순위에서 구체적인 취약축을 확인하세요.`;
        }
      }
    }
  }

  // 원인분해 자동 연결: 숙박비중이 취약하면 관광사업자 원자료를 자동으로 불러와
  // 근거로 삼는다. OPN_ATMY_GRP_CD가 시군구가 아니라 도 단위로만 걸러지는 걸
  // 확인했기 때문에(백엔드가 도 전체를 받아 주소로 재필터링), 지금은 검증된
  // 4개 지역(경주·강릉·제주·전주)에서만 자동 연결한다.
  const lodgingIsWeak = isWeak(axes[2]);
  useEffect(() => {
    if (!lodgingIsWeak || !regions.some((item) => item.id === regionId)) { setLodgingEvidence(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${apiBase}/v1/data-sources/mois/tourism-business/region/${regionId}/info`);
        const data = await response.json();
        if (cancelled) return;
        setLodgingEvidence(response.ok ? (data as MoisBusinessSnapshot) : { available: false });
      } catch { if (!cancelled) setLodgingEvidence({ available: false }); }
    })();
    return () => { cancelled = true; };
  }, [lodgingIsWeak, regionId]);
  const lodgingEvidenceData = lodgingEvidence && !('available' in lodgingEvidence) ? lodgingEvidence : null;

  // 정책 우선순위 TOP 3: 계산 가능한 축의 취약 정도로 순위를 매기고, 데이터가 없는 축은 "향후 분석" 항목으로 채운다.
  // 우선순위에는 취약 판정을 통과한 축만 올린다 — 하위 25%를 근소하게 밑도는
  // 축(severity > 0이지만 문턱 미달)은 표에만 표시하고 정책 과제로 세우지 않는다.
  // 공간확산은 방향(넓게/좁게 중 무엇이 나은지)이 검증되지 않아 우선순위
  // 판정에서 제외한다 — 위 axes 정의의 주석 참고.
  // 정책 순위는 정책 방향이 검증된 체류·소비·숙박 축에서만 정한다. 공간확산은
  // 방향성이 검증되지 않았고, 최근 7일 안정성은 연간 계절성의 대체값이므로
  // 표에는 남기되 "정책 우선순위"로 오인시키지 않는다.
  const rankedWeak = axes.filter((axis) => ['stay', 'spend', 'stayShare'].includes(axis.key) && isWeak(axis)).sort((a, b) => severity(b) - severity(a));
  const priorities = rankedWeak.slice(0, 3);
  // 하위 25%를 밑돌지만 취약 문턱에는 못 미친 축 — 우선순위에서 뺀 이유를 밝히는 데 쓴다.
  const nearMissAxes = axes.filter((axis) => isBelowBottomQuartile(axis) && !isWeak(axis));
  // 관광수요 자체는 이미 충분(전국 상위 절반)하므로, 홍보 확대보다 Peer 대비 취약축이 우선이다.
  const promotionLowPriority = demandLevel === '충분';
  const chartData = axes.filter((axis) => axis.diff != null).map((axis) => ({ name: axis.label, diff: (axis.diff as number) / axisSpan(axis), directionVerified: axis.key !== 'dispersion' }));

  const pgRegionsAvailable = pgCategoryList?.available === true;
  const pgRegionsInCategory = useMemo(() => {
    if (!pgRegionsAvailable) return [];
    const query = pgRegionQuery.trim();
    return pgCategoryList.regions
      .filter((row) => row.pg_category === selectedPgCategory)
      .filter((row) => !query || row.area_name.includes(query))
      .sort((a, b) => b.percentile - a.percentile);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pgCategoryList, selectedPgCategory, pgRegionQuery]);
  const pgCategoryCounts = useMemo(() => {
    const counts: Record<PgCategory, number> = { 'PG-1': 0, 'PG-2': 0, 'PG-3': 0, 'PG-4': 0 };
    if (pgRegionsAvailable) for (const row of pgCategoryList.regions) if (row.pg_category) counts[row.pg_category] += 1;
    return counts;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pgCategoryList]);
  const compareAxisKeys: Array<{ key: keyof PeerAxisSet; label: string }> = [
    { key: 'stay_intensity', label: '체류강도' }, { key: 'spend_intensity', label: '소비강도' },
    { key: 'lodging_share_index', label: '숙박비중' }, { key: 'dispersion_spread_km', label: '중심지 공간확산(km)' },
  ];

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
    ['외지인 수요 백분위', `${snapshot.national_comparison.outside_visitor_percentile}%`,
      snapshot.national_comparison.window_days
        ? `기초지자체 ${snapshot.national_comparison.municipality_count}곳 · 최근 ${snapshot.national_comparison.window_days}일 평균`
        : `${snapshot.national_comparison.municipality_count}개 지역 비교 · 선택일 단일 집계(전국 위치 재계산 실패로 대체값)`,
      'derived' as DataTier],
    ['외지인 비중', `${snapshot.visitor_mix.outside_share}%`, '현지인·외지인·외국인 합계 대비', 'derived' as DataTier],
    ['관광지 혼잡 예측', snapshot.observed_indices.attraction_crowding_forecast?.toFixed(1) ?? '--', '관광지별 최혼잡 시점=100 · 향후 30일 평균', 'modeled' as DataTier],
    ['중심지 공간확산', snapshot.observed_indices.spatial_dispersion != null ? `${snapshot.observed_indices.spatial_dispersion.toFixed(2)} km` : '--', snapshot.observed_indices.spatial_dispersion_detail ? `내비게이션 중심 관광지 ${snapshot.observed_indices.spatial_dispersion_detail.hub_count}개 좌표` : '중심 관광지 API 조회 대기', snapshot.observed_indices.spatial_dispersion != null ? 'derived' as DataTier : 'pending' as DataTier],
  ] : [];
  const methodologyTerms = [
    ['외지인 방문자', '이동통신 자료로 추정한 일별 방문자입니다. 관광 목적을 직접 확인한 관광객 수가 아니며 여러 날 체류하면 날짜별로 다시 집계될 수 있습니다.'],
    ['KTO 강도·숙박지수', '체류·소비·숙박 비중·숙박일수별 지표의 상대적 강도를 나타내는 지수점수입니다. 87.9를 숙박률 87.9%로 읽으면 안 됩니다.'],
    ['관광지 혼잡 예측', '각 관광지의 가장 붐비는 시기를 100으로 둔 향후 30일 상대 혼잡도입니다. 관광지 간 방문 점유율이 아니므로 공간분산 지수로 사용하지 않습니다.'],
    ['중심지 공간확산', '내비게이션 연계 중심 관광지 좌표가 지리적 중심에서 얼마나 넓게 퍼져 있는지를 RMS 거리(km)로 계산합니다. 실제 방문자 점유율의 균등도를 뜻하는 정식 공간분산 D와는 구분합니다. 다른 축과 달리 “높을수록 좋다”가 성립하지 않습니다 — 관광지가 넓게 퍼진 것과 한곳에 모인 것 중 무엇이 나은지는 지역 여건에 따라 다르므로, Peer 비교값은 참고용으로만 제공하고 취약 판정·정책 우선순위에는 반영하지 않습니다.'],
    ['단기 수요 안정성', '최근 7일 외지인 방문자의 변동계수(CV)를 100×(1−CV)로 역산한 파생지표입니다. 연간 계절성을 대신하지 않습니다.'],
    ['전국 위치 vs Peer Group', '전국 위치(①)는 기초지자체 전체 중 관광수요 백분위로 수요 수준만 판정합니다. Peer Group(②)은 체류·숙박·소비 취약성을 진단할 때만 쓰며, 같은 행정유형(시/군/자치구)·수도권 여부 안에서 관광수요·인구·인구밀도 규모가 가장 비슷한 지역으로 구성합니다(각 지표를 비교군 내 백분위로 바꿔 함께 비교). 전국 상위지역 중앙값을 그대로 비교기준으로 쓰지 않습니다.'],
    ['인구·인구밀도 출처', '주민등록인구(KOSIS 행정구역별 주민등록인구, 월간)를 KTO와 동일한 5자리 지역코드로 직접 조회합니다. 면적은 통계청 SGIS 행정동 경계를 시군구 단위로 합산해 추정했습니다(지도 좌표와 같은 출처, 오차 3% 이내). 인구 데이터를 못 가져오면 관광수요 규모만으로 비교군을 구성합니다.'],
    ['행정유형 분리', '자치구(강남구·해운대구)는 기초지자체이지만 일반구(수원시 팔달구처럼 상위 시 이름이 붙는 구)는 기초지자체가 아니므로 진단 대상·Peer Group 어디에도 포함하지 않습니다. 시는 시·군끼리만, 자치구는 자치구끼리만 비교합니다.'],
    ['PG-1~4 (도시 성격)', '행정유형·수도권 분리 위에 얹는 추가 정렬 기준입니다. 같은 행정유형 안에서 인구밀도 상대 순위(자치구는 자치구끼리, 시/군은 시/군끼리)로만 나눕니다 — PG-1 도심/상업 집중형, PG-2 도농복합 관광거점형, PG-3 대도시 주거/위성형, PG-4 일반 지방/농어촌형. 관광수요·숙박공급으로는 절대 나누지 않습니다: 그 두 값은 이 진단이 "취약한가"를 판정하는 지표 자체라, 그걸로 비교군을 나누면 비슷하게 취약한 지역끼리만 묶여 약점이 보이지 않게 됩니다(자기참조 순환). PG가 다르다고 후보에서 제외하지는 않으며, 실제 밀도 데이터를 기준으로 하므로 "관광거점"으로 알려진 지역도 데이터상 PG-4로 분류될 수 있습니다.'],
    ['관광수요 판정 기준', '전국 기초지자체 백분위 50% 이상이면 “충분”, 미만이면 “부족”으로 1차 판정합니다. 부족 판정 지역은 체류·숙박 원인분해보다 관광수요 확보를 우선 과제로 제시합니다.'],
    ['Peer Group 상위 25%', 'Peer Group 중앙값과 함께, 그 안에서 실제로 잘하는 지역의 수준(상위 25% 지점)을 같이 보여줍니다. 중앙값보다 낮더라도 상위 25%와의 격차를 통해 개선 여지를 가늠할 수 있습니다.'],
    ['지도 좌표', '전국 시군구 중심좌표는 통계청 SGIS 행정동 경계(공공누리 제1유형)를 admdongkor 저장소(CC BY 4.0)가 가공한 2024년 자료를 시군구 단위로 평균해 만들었습니다. 구가 있는 시는 구 좌표 평균으로 근사합니다.'],
    ['취약 판정 기준', 'Peer Group 하위 25% 문턱보다 낮고, 그 미달 폭이 해당 Peer Group의 사분위 범위(IQR = 상위25%−하위25%)의 0.5배 이상일 때만 “취약”으로 판정합니다. 문턱을 근소하게 밑도는 경우는 “근소”로만 표시하고 정책 과제로 세우지 않습니다 — 오차 수준의 차이가 정책 우선순위를 만드는 것을 막기 위해서입니다. Peer 표본이 4곳 미만이면 문턱 자체가 불안정하므로 판정을 보류합니다.'],
    ['취약도', '하위 25% 문턱 아래로 Peer 사분위 범위(IQR)의 몇 배만큼 내려가 있는지를 나타냅니다. 단위가 다른 축들을 같은 척도로 비교해 정책 개입 순서를 정합니다. 예산 배분 비율이나 효과 예측이 아닙니다.'],
    ['TCEI', '체류(S)·소비(C)·공간분산(D)·계절안정성(B)의 백분위 기하평균입니다. 현재는 연간 계절성과 소비 잔차가 완성되지 않아 산출하지 않습니다.'],
    ['R-GAP', '유사 관광구조 75분위 프론티어 TCEI와 실제 TCEI의 양(+)의 차이입니다. 현재 화면의 규칙기반 유형·우선순위와 동일한 점수가 아닙니다.'],
  ];

  return <>
    <header><div className="logo"><b>R</b>Regional Tourism Scan<i /></div><nav><a href="#map">전국 관광수요</a><a href="#diagnosis">관광현황 진단</a><a href="#business-data">관광사업자 원자료</a><a href="#peer">지역 비교</a><a href="#pg-compare">카테고리별 비교</a><a href="#priority">정책 우선순위</a><a href="#methodology">용어·알고리즘</a></nav><button type="button">정책 브리프 PDF ↗</button></header>
    <main data-live-analysis="true">
      <section className="hero live-hero"><small>● DATA LAB CONNECTION · KTO TOURISM DATA LAB</small><div><article><h1>지금은 <em>Data Lab API 자료</em>로<br />확인합니다.</h1><p>선택한 기준일과 지자체의 한국관광공사 통신 기반 방문자 추정치와 관광지수를 서버에서 조회합니다. 전국 위치로 관광수요 수준을 먼저 판단한 뒤, 행정유형·수도권 여부·관광수요 규모가 비슷한 Peer Group과 비교해 &ldquo;무엇이 상대적으로 부족한가&rdquo;를 진단합니다.</p><a href="#map">기준일 데이터 보기 ↓</a></article><aside><span>DATA STATUS <b>{loading ? 'LOADING' : snapshot ? 'CONNECTED' : 'CONNECTION REQUIRED'}</b></span><strong>{snapshot ? 'OK' : '--'}</strong><div className="bars">{[28, 42, 36, 58, 49, 68, 57, 79].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div><p><b>{snapshot ? snapshot.source : 'KTO API 연결 확인 필요'}</b><span>{snapshot?.base_ymd || date.replace(/-/g, '.')}</span></p></aside></div></section>
      <section id="map" className="section"><div className="heading"><div><small>01 / REGION SELECT</small><h2>지역을 선택하면,<br /><em>진단이 시작됩니다</em></h2></div><div className="live-controls"><div className="region-search"><label>지역 검색<input type="text" value={regionQuery} placeholder="예: 강남구, 경상북도" onChange={(event) => setRegionQuery(event.target.value)} /></label>{regionMatches.length > 0 && <ul className="region-search-results">{regionMatches.map((item) => <li key={item.id}><button type="button" onClick={() => { select(item.id); setRegionQuery(''); }}>{regionLabel(item)}</button></li>)}</ul>}</div><label>기준일<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label><button type="button" onClick={() => void loadDiagnosis(regionId, date.replace(/-/g, ''), () => false)} disabled={loading}>{loading ? '조회 중' : '기준일 데이터 조회'}</button></div></div>
        <div className="maplayout"><article className="map"><div>외지인 방문자 · KTO 일별 집계 <span>{mapRegions.length > regions.length ? `전국 ${mapRegions.length}개 시군구 중 클릭하세요` : '선택 지역을 클릭하세요'}</span></div><MapContainer center={[36.25, 127.8]} zoom={6.3} scrollWheelZoom={false}><TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />{mapRegions.map((item) => <CircleMarker key={item.id} center={[item.lat, item.lng]} radius={item.id === regionId ? 16 : 6} pathOptions={{ color: item.id === regionId ? '#173b2b' : '#7a1f0f', weight: item.id === regionId ? 4 : 1.5, fillColor: item.id === regionId ? '#4f8249' : '#e8622f', fillOpacity: item.id === regionId ? .95 : .9 }} eventHandlers={{ click: () => select(item.id) }}><Tooltip>{regionLabel(item)}</Tooltip></CircleMarker>)}</MapContainer><small>※ {mapRegions.length > regions.length ? `전국 기초지자체(시/군/자치구) 중 좌표를 확인할 수 있는 ${mapRegions.length}곳을 진단 대상으로 선택할 수 있습니다.` : '전국 스캔 결과를 불러오는 중입니다. 우선 4개 관광거점 중에서 선택하세요.'} Peer Group은 선택 지역과 같은 행정유형·수도권 여부·관광수요 규모의 지역으로 매번 다시 구성합니다.</small></article>
          <aside className="summary"><small>SELECTED MUNICIPALITY</small><h3>{regionLabel(region)}</h3><label>기준일 외지인 방문자</label><strong>{snapshot ? formatNumber.format(snapshot.area.outside_visitors) : '--'}</strong><i><b style={{ width: `${snapshot?.national_comparison.outside_visitor_percentile || 0}%` }} /></i><p>{snapshot?.national_comparison.window_days ? `기초지자체 ${snapshot.national_comparison.municipality_count}곳 중 백분위` : '전국 코드 내 백분위'} <b>{snapshot ? `${snapshot.national_comparison.outside_visitor_percentile}%` : '--'}</b></p><label>데이터 상태</label><div className="badges"><span>{snapshot ? 'KTO 통신기반 추정치' : '조회 대기'}</span><span>{snapshot ? snapshot.base_ymd : date.replace(/-/g, '')}</span></div><p className="desc">{error || (snapshot ? '선택 기준일의 외지인 방문자 추정치입니다. 아래 진단은 전국 위치(내 위치 확인)와 Peer Group 비교(내 문제 찾기)로 나눠 보여드립니다.' : '조회 버튼을 눌러 KTO 방문자 데이터를 불러오세요.')}</p></aside></div>
      </section>
      <section id="diagnosis" className="section live-section"><div className="heading"><div><small>02 / OBSERVED METRICS</small><h2>{region.name}의<br /><em>관측자료 기반 진단</em></h2></div><label>진단 대상{mapRegions.length > regions.length && <em className="tier tier-derived">전국 {mapRegions.length}곳</em>}<select value={regionId} onChange={(event) => select(event.target.value)}>{mapRegions.map((item) => <option key={item.id} value={item.id}>{regionLabel(item)}</option>)}</select></label></div>
        <div className="live-stats">{stats.map(([label, value, caption, tier]) => <article key={label as string}><small>{label} <Tier tier={tier as DataTier} /></small><strong>{value}</strong><p>{caption}</p></article>)}</div>
      </section>
      <section id="business-data" className="section live-section business-data"><div className="heading"><div><small>03 / BUSINESS REGISTER SOURCE</small><h2>관광사업자 원자료로<br /><em>숙박 공급을 확인합니다</em></h2></div><p>행정안전부 문화·관광사업자 조회서비스 원자료입니다. 업소 수와 객실 수는 서로 다른 값입니다.</p></div>
        <div className="business-query"><div className="business-fields"><label>지역 선택<select value={businessRegionId} onChange={(event) => setBusinessRegionId(event.target.value)}>{regions.map((item) => <option key={item.id} value={item.id}>{item.province} {item.name}</option>)}</select></label><label>조회 기준<select value={businessOperation} onChange={(event) => setBusinessOperation(event.target.value as 'info' | 'history')}><option value="info">현재 정보</option><option value="history">기준일 이력</option></select></label>{businessOperation === 'history' && <label>기준일<input type="date" value={businessBaseDate.slice(0, 4) + '-' + businessBaseDate.slice(4, 6) + '-' + businessBaseDate.slice(6, 8)} onChange={(event) => setBusinessBaseDate(event.target.value.replace(/-/g, ''))} /></label>}<button type="button" onClick={() => void loadBusinessData()} disabled={businessLoading}>{businessLoading ? '조회 중' : '원자료 조회'}</button></div><p><b>지역만 선택하세요.</b> 제공기관의 개방자치단체코드는 서버에서 안전하게 변환합니다. 현재 진단 화면에서 제공하는 경주·강릉·제주·전주를 우선 지원하며 전국 코드표 적재 후 목록을 확장합니다.</p></div>
        {businessError && <p className="business-error">{businessError}</p>}
        {businessData && <div className="business-results"><div className="business-summary"><article><small>{region.name} 소재 레코드 <Tier tier="measured" /></small><strong>{formatNumber.format(businessData.raw_record_count)}</strong><p>제공기관 그룹코드 전체 {formatNumber.format(businessData.province_group_record_count)}건 중 주소로 재필터링</p></article><article><small>영업 중 사업체 <Tier tier="derived" /></small><strong>{formatNumber.format(businessData.operating_business_count)}</strong><p>`SALS_STTS_NM` 텍스트 기준</p></article><article><small>영업 중 관광숙박업소 <Tier tier="derived" /></small><strong>{formatNumber.format(businessData.operating_tourism_accommodation_business_count)}</strong><p>{businessData.metric_type}</p></article></div><p className="business-caution">※ 이것은 <b>관광숙박업소 수</b>이며 객실 수가 아닙니다.{businessData.low_sample ? ` 제공기관 원자료 자체가 작아(그룹 전체 ${businessData.province_group_record_count}건) 이 지역 통계는 참고용입니다.` : ''}</p><div className="business-table-wrap"><table><thead><tr><th>사업장명</th><th>관광사업 업종</th><th>영업상태</th><th>주소</th><th>갱신시점</th></tr></thead><tbody>{businessData.items.map((item, index) => <tr key={`${item.MNG_NO || 'row'}-${index}`}><td>{item.BPLC_NM || '-'}</td><td>{item.CULTR_SPTS_TPBIZ_NM || '-'}</td><td>{item.SALS_STTS_NM || '-'}</td><td>{item.ROAD_NM_ADDR || item.LOTNO_ADDR || '-'}</td><td>{item.DAT_UPDT_PNT || item.LAST_MDFCN_PNT || '-'}</td></tr>)}</tbody></table></div></div>}
      </section>
      <section id="peer" className="section live-section"><div className="heading"><div><small>04 / NATIONAL POSITION + PEER GROUP</small><h2>비슷한 조건의 지역과 비교해,<br /><em>숨은 취약점을 찾습니다</em></h2></div></div>
        <div className="cards"><article className="national-position"><small>① 전국 비교 — 내 위치 확인</small>
          {peersAvailable ? <>
            <p className="insight">{topicParticle(region.name)} 관광수요가 전국 기초지자체(시/군/자치구) {nationalPeers.national.municipality_count}곳 중 상위 {(100 - nationalPeers.national.target_percentile).toFixed(0)}%(백분위 {nationalPeers.national.target_percentile}%)로, &ldquo;{nationalPeers.national.demand_level}&rdquo;한 지역으로 판정됩니다.</p>
            <div className="badges"><span>{region.name}: 전국 상위 {(100 - nationalPeers.national.target_percentile).toFixed(0)}%</span><span>수요 판정: {nationalPeers.national.demand_level}</span></div>
          </> : <p className="insight">{nationalPeers && !nationalPeers.available ? `전국 비교 조회에 실패했습니다: ${nationalPeers.reason}` : '전국 관광수요 스캔 결과를 불러오는 중입니다…'}</p>}
        </article></div>
        <div className="cards"><article className="peer-diagnosis"><small>② Peer Group 비교 — 내 문제 찾기</small>
          <p className="peer-note">{peerGroup
            ? `비교군: ${peerGroup.criteria_note} · ${peerGroup.count}곳${peerGroup.relaxed ? ' (수도권 조건을 완화해 표본을 채웠습니다)' : ''}${peerGroup.target_population != null ? ` · ${region.name} 인구 ${formatManUnit(peerGroup.target_population)}${peerGroup.target_population_density != null ? ` · 밀도 ${formatNumber.format(peerGroup.target_population_density)}명/km²` : ''}` : ''}`
            : nationalPeers && !nationalPeers.available ? `Peer Group 조회에 실패했습니다: ${nationalPeers.reason}` : 'Peer Group을 구성하는 중입니다…'}</p>
          {peerGroup?.pg_category && <div className="pg-badge" title="인구밀도만으로 근사한 도시 성격 분류 — 행정유형·수도권 분리 위에 얹는 추가 정렬 기준이며, 다르다고 후보에서 빠지지는 않습니다. 06 용어 섹션에서 자세히 설명합니다.">
            <span className="pg-chip">{peerGroup.pg_category}</span> {peerGroup.pg_category_label} 우선 정렬
          </div>}
          {peerGroup && <details className="peer-list-disclosure"><summary>비교군 지역 목록 펼쳐보기 ({peerGroup.peers.length}곳)</summary>
            <ul className="peer-list">{peerGroup.peers.map((peer) => <li key={peer.area_cd}>{peer.area_name}
              {peer.pg_category && <span className={`pg-chip small${peer.pg_category === peerGroup.pg_category ? ' match' : ''}`}>{peer.pg_category}</span>}
              <span>전국 {peer.rank}위 · 상위 {(100 - peer.percentile).toFixed(0)}%{peer.population != null ? ` · 인구 ${formatManUnit(peer.population)}` : ''}{peer.population_density != null ? ` · 밀도 ${formatNumber.format(peer.population_density)}명/km²` : ''}</span></li>)}</ul>
          </details>}
          {axes.map((axis) => <div className="sbar" key={axis.key}>
            <span>{axis.label} <Tier tier={axis.tier} /></span>
            <i><b style={{ width: axis.diff == null ? '0%' : `${Math.min(100, 20 * Math.abs(axis.diff) / axisSpan(axis))}%`,
              background: axis.diff == null ? '#d7ddd4' : axis.key === 'dispersion' ? '#9aa39c' : axis.diff < 0 ? '#d45f43' : '#8fbc7e' }} /></i>
            <em>{axis.diff == null ? '데이터 없음' : formatSigned(axis.diff, axis.unit)}</em>
          </div>)}
          <div className="peer-value-table">
            <table>
              <thead><tr><th>지표</th><th>{region.name}</th><th>Peer 하위25%</th><th>Peer 중앙값</th><th>Peer 상위25%</th><th>비고</th></tr></thead>
              <tbody>{axes.filter((axis) => axis.value != null || axis.peerMedian != null).map((axis) => {
                const lowSample = axis.peerBottom25 != null && axis.peerSampleSize < MIN_PEER_SAMPLE_FOR_WEAK_JUDGMENT;
                // 공간확산은 방향 미검증이라 취약/근소 판정을 아예 매기지 않는다(위 axes 정의 참고).
                const directionVerified = axis.key !== 'dispersion';
                return <tr key={axis.key} className={directionVerified && isWeak(axis) ? 'weak' : undefined}>
                  <td>{axis.label}</td>
                  <td className="value">{axis.value != null ? `${axis.value.toFixed(1)}${axis.unit === 'km' ? ' km' : ''}` : '--'}</td>
                  <td>{axis.peerBottom25 != null ? `${axis.peerBottom25.toFixed(1)}${axis.unit === 'km' ? ' km' : ''}` : '--'}</td>
                  <td>{axis.peerMedian != null ? `${axis.peerMedian.toFixed(1)}${axis.unit === 'km' ? ' km' : ''}` : '--'}</td>
                  <td>{axis.peerTop25 != null ? `${axis.peerTop25.toFixed(1)}${axis.unit === 'km' ? ' km' : ''}` : '--'}</td>
                  <td className="remark">
                    {!directionVerified
                      ? (axis.value != null && <span className="badge neutral" title="관광지가 넓게 퍼진 것과 좁게 모인 것 중 무엇이 나은지 검증되지 않아, 값은 참고용으로만 제공하고 취약 여부는 판정하지 않습니다.">참고용 · 방향 미검증</span>)
                      : lowSample ? <span className="badge caution" title="Peer 표본이 적어 하위25% 문턱이 불안정합니다 — 취약 판정에 쓰지 않습니다.">표본 {axis.peerSampleSize}곳 · 판정 보류</span>
                      : isWeak(axis) ? <span className="badge weak" title={`하위25% 문턱보다 IQR의 ${severity(axis).toFixed(2)}배만큼 낮습니다 (판정 기준 ${WEAK_MATERIALITY_IQR}배 이상).`}>취약 · {severity(axis).toFixed(2)} IQR</span>
                      : isBelowBottomQuartile(axis) ? <span className="badge near" title={`하위25% 문턱을 밑돌지만 미달 폭이 IQR의 ${severity(axis).toFixed(2)}배로, 판정 기준(${WEAK_MATERIALITY_IQR}배)에 못 미칩니다 — 오차 수준의 차이로 보고 정책 과제로 세우지 않습니다.`}>근소 · {severity(axis).toFixed(2)} IQR</span>
                      : null}
                  </td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          <p className="insight">{diagnosisText}</p>
        </article></div>
      </section>
      <section id="pg-compare" className="section live-section"><div className="heading"><div><small>04b / PG CATEGORY COMPARISON</small><h2>같은 도시 성격끼리,<br /><em>직접 골라 비교합니다</em></h2></div></div>
        <p className="peer-note">위 Peer Group은 진단 대상 하나를 기준으로 자동 구성됩니다. 여기서는 PG-1~4 카테고리를 먼저 고르고, 그 안의 지역을 최대 5곳까지 직접 선택해 지표를 나란히 비교할 수 있습니다.</p>
        <div className="pg-compare-tool">
          <div className="pg-tabs">{(['PG-1', 'PG-2', 'PG-3', 'PG-4'] as PgCategory[]).map((category) => <button key={category} type="button"
            className={category === selectedPgCategory ? 'active' : undefined}
            onClick={() => setSelectedPgCategory(category)}>{category} <span>{PG_CATEGORY_LABELS[category]}</span><em>{pgCategoryCounts[category]}곳</em></button>)}</div>
          {!pgRegionsAvailable && <p className="insight">{pgCategoryList && !pgCategoryList.available ? `카테고리 목록 조회에 실패했습니다: ${pgCategoryList.reason}` : 'PG 카테고리 목록을 불러오는 중입니다…'}</p>}
          {pgRegionsAvailable && <>
            <input className="pg-region-search" type="text" placeholder={`${selectedPgCategory} 안에서 지역명으로 찾기`} value={pgRegionQuery} onChange={(event) => setPgRegionQuery(event.target.value)} />
            <ul className="pg-region-grid">{pgRegionsInCategory.map((row) => <li key={row.area_cd}>
              <label className={compareSelection.includes(row.area_cd) ? 'checked' : undefined}>
                <input type="checkbox" checked={compareSelection.includes(row.area_cd)}
                  disabled={!compareSelection.includes(row.area_cd) && compareSelection.length >= 5}
                  onChange={() => toggleCompareSelection(row.area_cd)} />
                <span>{pgRegionLabel(row)}</span>
                <small>상위 {(100 - row.percentile).toFixed(0)}%{row.population_density != null ? ` · 밀도 ${formatNumber.format(row.population_density)}명/km²` : ''}</small>
              </label>
            </li>)}</ul>
            {pgRegionsInCategory.length === 0 && <p className="insight">검색어와 일치하는 {selectedPgCategory} 지역이 없습니다.</p>}
          </>}
          <div className="pg-compare-actions">
            <span>{compareSelection.length}/5곳 선택됨</span>
            <button type="button" onClick={() => void runCompareRegions()} disabled={compareSelection.length < 2 || compareLoading}>{compareLoading ? '비교 중…' : '선택한 지역 비교하기'}</button>
            {compareSelection.length > 0 && <button type="button" className="ghost" onClick={() => { setCompareSelection([]); setCompareResult(null); }}>선택 초기화</button>}
          </div>
          {compareError && <p className="business-error">{compareError}</p>}
          {compareResult?.available && <div className="peer-value-table pg-compare-result">
            <p className="compare-scroll-hint">지역이 많을 때는 표를 좌우로 밀어 전체 비교값을 확인하세요 <b>← →</b></p>
            <table>
              <thead><tr><th>지표</th>{compareResult.regions.map((row) => <th key={row.area_cd}>{pgRegionLabel(row)}{row.pg_category && <em className="pg-chip small">{row.pg_category}</em>}</th>)}</tr></thead>
              <tbody>
                <tr><td>전국 수요 백분위</td>{compareResult.regions.map((row) => <td key={row.area_cd} className="value">상위 {(100 - row.percentile).toFixed(0)}%</td>)}</tr>
                <tr><td>인구</td>{compareResult.regions.map((row) => <td key={row.area_cd}>{row.population != null ? formatManUnit(row.population) : '--'}</td>)}</tr>
                <tr><td>인구밀도</td>{compareResult.regions.map((row) => <td key={row.area_cd}>{row.population_density != null ? `${formatNumber.format(row.population_density)}명/km²` : '--'}</td>)}</tr>
                {compareAxisKeys.map(({ key, label }) => <tr key={key}><td>{label}</td>{compareResult.regions.map((row) => <td key={row.area_cd} className="value">{row.fetch_ok && row.axes?.[key] != null ? row.axes[key]!.toFixed(1) : '--'}</td>)}</tr>)}
              </tbody>
            </table>
            {compareResult.regions.some((row) => !row.axes || Object.values(row.axes).every((value) => value == null)) &&
              <p className="business-caution">※ 일부 지역은 선택 기준월의 KTO 세부지표가 제공되지 않아 &ldquo;--&rdquo;로 표시됩니다. 구가 있는 시는 하위 구 지표를 합산해 조회하며, 그래도 값이 없을 때만 원천자료 미제공으로 처리합니다.</p>}
          </div>}
        </div>
      </section>
      <section id="priority" className="section live-section"><div className="heading"><div><small>05 / WEAKNESS → ROOT CAUSE → PRIORITY</small><h2>{region.name} 정책 우선순위<br /><em>TOP 3</em></h2></div><span className="badges"><span>{regionType} <Tier tier="modeled" /></span></span></div>
        <p className="peer-note">{diagnosisText}</p>
        {chartData.length > 0 && <div className="priority-chart">
          <small>Peer 중앙값 대비 편차 · 단위는 Peer 사분위 범위(IQR) 배수</small>
          <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 42)}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 28, bottom: 4, left: 4 }}>
              <XAxis type="number" tick={{ fontSize: 10, fill: '#7e8983' }} tickFormatter={(value) => `${Number(value).toFixed(1)}×`} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={96} tick={{ fontSize: 11, fill: '#3f4a44' }} axisLine={false} tickLine={false} />
              <Bar dataKey="diff" radius={3}>
                {chartData.map((item) => <Cell key={item.name} fill={!item.directionVerified ? '#9aa39c' : item.diff < 0 ? '#d45f43' : '#8fbc7e'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>}
        <div className="cards">{priorities.map((axis, index) => <article key={axis.key}><small>{index + 1}순위 · {axis.label} <Tier tier={axis.tier} /></small><strong>{axis.diff == null ? '데이터 연동 후 진단' : `${axis.label} 강화 필요`}</strong><p>근거: {axis.diff == null ? axis.note : `Peer 하위25%(${axis.peerBottom25?.toFixed(1)}) 대비 IQR의 ${severity(axis).toFixed(2)}배 미달 · 중앙값 대비 ${formatSigned(axis.diff, axis.unit)}`}</p>
          {axis.key === 'stayShare' && lodgingIsWeak && (
            lodgingEvidenceData ? <div className="root-cause-evidence">
              <p className="root-cause-link">→ 원인 확인 (자동 연결): 관광사업자 원자료 기준 {region.name} 소재 영업 중 관광숙박업소 <b>{formatNumber.format(lodgingEvidenceData.operating_tourism_accommodation_business_count)}건</b> ({lodgingEvidenceData.province_group_record_count}건 중 {region.name} 주소로 필터링)</p>
              {lodgingEvidenceData.low_sample && <p className="root-cause-caveat">※ 원자료 표본이 작아({lodgingEvidenceData.province_group_record_count}건) 공급 제약형/수요연결부족형을 단정하기보다 참고 근거로만 활용하세요. <a href="#business-data">상세 목록 보기</a></p>}
            </div>
            : regions.some((item) => item.id === regionId)
              ? <p className="root-cause-link">→ 원인 확인: 관광사업자 원자료를 불러오는 중입니다…</p>
              : <p className="root-cause-link">→ 원인 확인: 관광사업자 원자료 자동 연결은 현재 경주·강릉·제주·전주만 지원합니다. <a href="#business-data">다른 지역은 원자료 화면에서 직접 확인하세요.</a></p>
          )}
        </article>)}</div>
        {/* 취약 판정을 통과한 축이 하나도 없을 때 빈 영역을 남기지 않는다. "우선순위가
            없다"는 것도 진단 결과이고, 근소하게 문턱을 밑돈 축이 있으면 그것까지 밝힌다. */}
        {!priorities.length && <div className="cards"><article><small>정책 우선순위 없음 <Tier tier="modeled" /></small>
          <strong>지금은 신규 정책 과제가 없습니다</strong>
          <p>{nearMissAxes.length
            ? `Peer Group 대비 취약으로 판정된 축이 없습니다. ${nearMissAxes.map((axis) => axis.label).join('·')}이(가) 하위 25% 문턱을 밑돌지만 미달 폭이 IQR의 ${WEAK_MATERIALITY_IQR}배에 못 미쳐, 오차 수준의 차이를 정책 과제로 세우지 않았습니다. 위 표의 "근소" 표시를 확인하세요.`
            : 'Peer Group 대비 취약으로 판정된 축이 없습니다. 현재 수준의 유지·고도화가 적절합니다.'}</p>
        </article></div>}
        {promotionLowPriority && <p className="insight low-priority">[우선순위 낮음] 추가 관광홍보 — 관광수요 자체는 이미 전국 기준으로 충분하므로, 홍보 확대보다 위 우선순위 항목을 먼저 검토합니다.</p>}
      </section>
      <section id="methodology" className="section live-section methodology"><div className="heading"><div><small>06 / TERMS &amp; ALGORITHM</small><h2>용어와 계산법을<br /><em>투명하게 공개합니다</em></h2></div><p>현재 제공값과 향후 R-GAP 산출을 구분합니다. 각 값의 단위·비교범위·한계를 함께 확인하세요.</p></div><div className="term-grid">{methodologyTerms.map(([term, description]) => <article key={term}><h3>{term}</h3><p>{description}</p></article>)}</div></section>
      <section id="tshift" className="section"><div className="heading"><div><small>07 / 정책 시행 후 효과검증 프레임</small><h2>정책은 실험하고,<br /><em>효과는 증명합니다.</em></h2></div><p>현재 정책 성과가 아닌, 야간·계절 누수 정책의 사전 등록과 DiD 사후검증을 위한 실행 템플릿입니다.</p></div><div className="did">{[['01', '정책 패키지 설계', '체류 동선·야간 콘텐츠·지역 상권을 하나의 전환 여정으로 설계합니다.'], ['02', '비교지역 선정 · 변화 가설 등록', '성과지표, 대상·비교지역, 관찰기간을 사업 시작 전 고정합니다.'], ['03', '사전/사후 데이터 수집 · DiD 효과 리포트', '정책 전후 변화와 비교군 차이를 비교해 순효과를 검증합니다.']].map(([step, title, description]) => <article key={step}><small>{step}</small><h3>{title}</h3><p>{description}</p></article>)}</div></section>
      <section className="meta"><small>DATA INTERPRETATION / REQUIRED META INFO</small><div><b>원천자료·파생지표·규칙기반 진단을 구분합니다.</b><p>방문자수는 이동통신 자료 기반의 KTO 추정치이며 관광객 실인원과 동일하지 않습니다. 체류·소비·숙박 지수는 비율이나 인원수가 아닌 KTO 지수점수입니다. 관광지 혼잡도는 KTO 예측값이며, 중심지 공간확산은 내비게이션 중심 관광지 좌표로 계산한 거리 기반 보조지표입니다. 전국 위치는 관광수요 수준만 판단하며, 체류·숙박·소비 취약성은 행정유형·수도권 여부·관광수요 규모가 같은 Peer Group 안에서만 판단합니다(전국 상위지역 중앙값을 그대로 쓰지 않습니다). Peer Group 기반 유형·우선순위는 규칙기반 진단입니다. 실제 관광지별 방문점유율, 인구·밀도 등 추가 구조변수 기반 유사도, 연간 계절성, 소비 잔차와 75분위 프론티어가 완성되기 전에는 TCEI·R-GAP으로 표시하지 않습니다.</p></div></section>
    </main><footer><div className="logo"><b>R</b>Regional Tourism Scan</div><small>Regional Tourism Scan · Regional Recoverable Tourism Value Gap Engine · KTO Tourism Data Challenge</small></footer>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
