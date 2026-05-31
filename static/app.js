// ===== EduData Watch v9 — 검토 우선도 지수제 + IA 재구성 =====
//  · 별/등급 사고 폐기. 사용자 노출은 "검토 우선도 지수" (소수점 1자리)
//  · 라벨(우선 검토/일반 검토/참고)은 score 임계 기반
//  · 대시보드 = 좌 필터 / 중 목록 / 우 통계
//  · 학교 상세 = 요약 5박스 + 룰 단위 Evidence Card
//  · 고급 탐색 = 별도 view (학교 상세의 커스텀 분석 이식)
const API = '';
let currentSchoolCode = null;
let chartMain = null, chartBully = null;
let allSchools = [];
let dashboardData = null;
let dashLoaded = false;
let chatHistory = [];
let chatContext = 'national';

// ── 사용자 노출 폴백 문구 — 서버와 톤 통일 ──
const FALLBACK_AI_TEXT = 'AI 보조 해석을 불러오지 못했습니다. 원천 데이터와 검토 신호를 기준으로 확인해 주세요.';

// ── 긴 학교명 줄바꿈 (단어 단위) ──
function wrapSchoolName(name) {
  if (!name || name.length <= 12) return name;
  return name
    .replace(/대학교/g, '대학교\u200B')
    .replace(/사범대학/g, '사범대학\u200B')
    .replace(/부속/g, '부속\u200B')
    .replace(/부설/g, '부설\u200B')
    .replace(/고등학교/g, '\u200B고등학교');
}

// ── 종합 점수 라벨 임계 (0~100, v4 점수체계) ──
// 70~ critical · 50~ major · 30~ minor · 0~ warning
const INDEX_THRESHOLD = { CRITICAL: 70, MAJOR: 50, MINOR: 30 };
function indexLabel(score) {
  const s = Number(score) || 0;
  if (s >= INDEX_THRESHOLD.CRITICAL) return '즉시 검토';
  if (s >= INDEX_THRESHOLD.MAJOR)    return '우선 검토 대상';
  if (s >= INDEX_THRESHOLD.MINOR)    return '일반 검토';
  return '참고';
}
function indexBin(score) {
  const s = Number(score) || 0;
  if (s >= INDEX_THRESHOLD.CRITICAL) return 'critical';
  if (s >= INDEX_THRESHOLD.MAJOR)    return 'major';
  if (s >= INDEX_THRESHOLD.MINOR)    return 'minor';
  return 'warning';
}
function indexCls(score) {
  // 기존 CSS(grade-priority/normal/ref) 재활용 — critical·major는 강조, minor·warning은 약화.
  const bin = indexBin(score);
  if (bin === 'critical' || bin === 'major') return 'grade-priority';
  if (bin === 'minor') return 'grade-normal';
  return 'grade-ref';
}
function fmtIndex(score) {
  if (score == null || isNaN(Number(score))) return '—';
  return Number(score).toFixed(1);
}

// ── 활성 필터 상태 ──
const activeFilters = {
  bin: new Set(),        // 점수 구간: 'critical' / 'major' / 'minor' / 'warning'
  category: new Set(),   // 카테고리 코드 (대분류)
  rule: new Set(),       // 룰 ID (세부)
  district: new Set(),   // 구 이름
  type: new Set(),       // 학교 유형
};
let currentSort = 'score_desc';

// ===== REGIONS (17 시·도) =====
const REGIONS = [
  { code: 'seoul', name: '서울특별시', active: true },
  { code: 'gyeonggi', name: '경기도', active: false },
  { code: 'incheon', name: '인천광역시', active: false },
  { code: 'gangwon', name: '강원특별자치도', active: false },
  { code: 'chungbuk', name: '충청북도', active: false },
  { code: 'chungnam', name: '충청남도', active: false },
  { code: 'jeonbuk', name: '전라북도', active: false },
  { code: 'jeonnam', name: '전라남도', active: false },
  { code: 'gyeongbuk', name: '경상북도', active: false },
  { code: 'gyeongnam', name: '경상남도', active: false },
  { code: 'jeju', name: '제주특별자치도', active: false },
  { code: 'daejeon', name: '대전광역시', active: false },
  { code: 'daegu', name: '대구광역시', active: false },
  { code: 'busan', name: '부산광역시', active: false },
  { code: 'ulsan', name: '울산광역시', active: false },
  { code: 'gwangju', name: '광주광역시', active: false },
  { code: 'sejong', name: '세종특별자치시', active: false },
];

// ===== 초기화 =====
(async () => {
  bindNav();
  bindChat();
  showView('national');
  renderNational(null);

  try {
    const [dash, schools] = await Promise.all([
      fetch(API + '/api/dashboard').then(r => r.json()),
      fetch(API + '/api/schools').then(r => r.json()),
    ]);
    dashboardData = dash;
    allSchools = schools;
    dashLoaded = true;
    renderNational(dash);
    const db = dash.data_basis || {};
    document.getElementById('nav-period').textContent = (db.year_range || '—') + ' 공시 기준';
  } catch (e) {
    console.error('초기 데이터 로드 실패', e);
  }
})();

// ===== NAV =====
function bindNav() {
  document.querySelector('[data-view="national"]').onclick = (e) => { e.preventDefault(); showView('national'); };
  document.querySelector('[data-view="dashboard"]').onclick = (e) => { e.preventDefault(); showView('dashboard'); loadDashboard(); };
  const ns = document.getElementById('nav-school');
  ns.onclick = (e) => { e.preventDefault(); if (currentSchoolCode) showView('school'); };
  document.querySelector('[data-view="rulelab"]').onclick = (e) => { e.preventDefault(); showView('rulelab'); };
  document.getElementById('back-to-national').onclick = (e) => { e.preventDefault(); showView('national'); };
  document.getElementById('back-to-dashboard').onclick = (e) => { e.preventDefault(); showView('dashboard'); loadDashboard(); };
  document.getElementById('nav-search').addEventListener('input', (e) => {
    const q = (e.target.value || '').trim();
    // 학교 상세·전국·룰랩에서 검색 시 학교 목록으로 자동 전환
    const onList = document.getElementById('view-dashboard').classList.contains('active');
    if (q && !onList) { showView('dashboard'); loadDashboard(); }
    renderSearchDropdown(q);
    applyFilterAndRender();
  });
  document.getElementById('nav-search').addEventListener('focus', (e) => {
    renderSearchDropdown((e.target.value || '').trim());
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.nav-search-wrap')) {
      const dd = document.getElementById('nav-search-dropdown');
      if (dd) dd.style.display = 'none';
    }
  });
}

// 자동완성 dropdown — 학교명 부분일치 후보 표시 (클릭 시 해당 학교 상세로 이동)
function renderSearchDropdown(q) {
  const dd = document.getElementById('nav-search-dropdown');
  if (!dd) return;
  if (!q) { dd.style.display = 'none'; dd.innerHTML = ''; return; }
  const ql = q.toLowerCase();
  const matches = (allSchools || []).filter(s =>
    s.school_name && s.school_name.toLowerCase().includes(ql)
  ).slice(0, 8);
  if (!matches.length) {
    dd.innerHTML = '<div class="nav-search-empty">일치하는 학교 없음</div>';
    dd.style.display = 'block';
    return;
  }
  dd.innerHTML = matches.map(s => {
    const safeName = (s.school_name || '').replace(/'/g, '&#39;');
    return `<div class="nav-search-item" onclick="goToSchool('${s.school_code}'); document.getElementById('nav-search-dropdown').style.display='none';">
      <span class="ns-name">${safeName}</span>
      <span class="ns-meta">${s.district || ''}·${s.school_type || ''}</span>
    </div>`;
  }).join('');
  dd.style.display = 'block';
}

function showView(view) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + view).classList.add('active');
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  chatContext = view;
  updateChatContext();
  window.scrollTo(0, 0);
}

// ===== NATIONAL VIEW =====
// 표본 문구 — API data_basis에서 항상 동적으로 만든다. 고정 숫자 박지 말 것.
function _districtsLine(districts, max) {
  if (!Array.isArray(districts) || !districts.length) return '';
  const arr = districts.map(d => d.endsWith && d.endsWith('구') ? d : d + '구');
  if (!max || arr.length <= max) return arr.join('·');
  return arr.slice(0, max).join('·') + ' 외 ' + (arr.length - max) + '구';
}
function _sampleNoteText(db) {
  if (!db || !db.schools) return '데이터 로드 중…';
  const schools = db.schools;
  const districts = Array.isArray(db.districts) ? db.districts : [];
  const distCount = districts.length;
  const distLine = _districtsLine(districts, 4);
  const yr = db.year_range || '';
  return `<b>프로토타입 표본 N=${schools}</b> · 서울 ${distCount}개 자치구 일반고`
       + (distLine ? ` (${distLine})` : '')
       + (yr ? ` · ${yr}년 공시` : '')
       + ` · 확장 시 전국 11,000+`;
}
function _nationalSubtitle(db) {
  if (!db || !db.schools) return '프로토타입 표본 · 시범운영';
  return `프로토타입 표본 · 시범운영: 서울 ${db.schools}교 일반고`;
}

function renderNational(dash) {
  const grid = document.getElementById('region-grid');
  if (!grid) return;

  const top1 = dash && dash.top3 && dash.top3[0];
  const totalDetections = dash ? dash.total_detections : null;
  const totalSchools = dash ? dash.total_schools : null;
  const db = dash ? (dash.data_basis || {}) : {};

  // national 뷰의 헤더/푸터 문구 동기화 — index.html의 고정 N=42 문구를 API 값으로 대체
  const subtitleEl = document.getElementById('national-subtitle');
  if (subtitleEl) subtitleEl.textContent = _nationalSubtitle(db);
  const footEl = document.getElementById('national-footnote');
  if (footEl) footEl.innerHTML = _sampleNoteText(db);

  const districtsLine = _districtsLine(db.districts || [], 4);
  const districtCount = (db.districts || []).length;

  grid.innerHTML = REGIONS.map(r => {
    if (r.active) {
      return `
        <div class="region-card active" onclick="goToRegion('${r.code}')">
          <div class="region-card-inner">
            <div class="region-name">${r.name}</div>
            <div class="region-districts">${districtCount ? districtCount + '개 자치구 일반고' : ''}${districtsLine ? ' · ' + districtsLine : ''}</div>
            <div class="region-stats">
              <span class="rstat">${totalSchools != null ? totalSchools + '교' : '—'}</span>
              <span class="rstat-sep">·</span>
              <span class="rstat">검토 후보 ${totalDetections != null ? totalDetections + '건' : '—'}</span>
              <span class="rstat-sep">·</span>
              <span class="rstat">${db.year_range ? db.year_range + '년' : '—'}</span>
            </div>
            ${top1
              ? `<div class="region-top"><span class="region-top-label">검토 우선도 1위</span> <b>${top1.school_name}</b> · 지수 ${fmtIndex(top1.score)}</div>`
              : '<div class="region-loading">데이터 로드 중…</div>'}
          </div>
          <div class="region-cta">서울 상세 보기 →</div>
        </div>`;
    }
    return `
      <div class="region-card disabled">
        <div class="region-name">${r.name}</div>
        <span class="region-coming">준비중</span>
      </div>`;
  }).join('');
}

function goToRegion(code) {
  if (code !== 'seoul') return;
  showView('dashboard');
  loadDashboard();
}

// ===== DASHBOARD =====
async function loadDashboard() {
  if (dashLoaded) {
    _renderDashboardUI(dashboardData, allSchools);
    return;
  }
  try {
    const [dash, schools] = await Promise.all([
      fetch(API + '/api/dashboard').then(r => r.json()),
      fetch(API + '/api/schools').then(r => r.json()),
    ]);
    dashboardData = dash;
    allSchools = schools;
    dashLoaded = true;
    _renderDashboardUI(dash, schools);
    renderNational(dash);
    const db = dash.data_basis || {};
    document.getElementById('nav-period').textContent = (db.year_range || '—') + ' 공시 기준';
  } catch (e) {
    console.error('대시보드 로드 실패', e);
    document.getElementById('top3-grid').innerHTML = '<div class="loading">데이터 로드 실패</div>';
  }
}

function _renderDashboardUI(dash, schools) {
  const db = dash.data_basis || {};
  document.getElementById('data-basis-line').textContent =
    `${db.source || '공시 데이터'} · ${db.year_range || ''}년 · 전체 ${db.schools || schools.length}교 · 검토 후보 ${dash.total_detections}건`;
  document.getElementById('sample-note').innerHTML = _sampleNoteText(db);

  renderTop3(dash.top3 || []);
  renderFilterPanel(dash);
  renderDistBars(dash.distribution || {});
  renderCatDist(dash.category_distribution || []);
  bindSort();
  applyFilterAndRender();
  renderRuleStatusNote(dash);
}

// §9: needs_mapping 룰 안내 — "현재 수집 범위 확인 필요"
function renderRuleStatusNote(dash) {
  const summary = (dash && dash.rule_status_summary) || null;
  if (!summary) return;
  const needs = (summary.rows || []).filter(r => r.status === 'needs_mapping');
  let note = document.getElementById('rule-status-note');
  const wrap = document.querySelector('.list-area');
  if (!needs.length) {
    if (note) note.remove();
    return;
  }
  if (!note) {
    note = document.createElement('div');
    note.id = 'rule-status-note';
    note.className = 'rule-status-note';
    wrap && wrap.appendChild(note);
  }
  note.innerHTML = `
    <span class="rsn-tag">매핑 확인 필요</span>
    <div><b>현재 수집 범위 확인 필요</b>: ${needs.map(r => `${r.name} (${r.rule_id})`).join(' · ')}.
    원천 컬럼 단일 확정이 어려워 룰 함수는 구현했으나 실 탐지는 보류했습니다.</div>`;
}

// ===== TOP 3 =====
function renderTop3(top3) {
  const el = document.getElementById('top3-grid');
  if (!top3.length) { el.innerHTML = '<div class="loading">데이터 없음</div>'; return; }
  el.innerHTML = top3.map((s, i) => {
    const rank = i + 1;
    const gl = indexLabel(s.score), gc = indexCls(s.score);
    const cats = (s.categories_ko || []).slice(0, 5).map(c => {
      const severe = s.rep && c.code === s.rep.category_code;
      return `<span class="cat-chip ${severe ? 'severe' : ''}" title="${c.code}">${c.ko}</span>`;
    }).join('');
    const rep = s.rep || {};
    return `
      <div class="top3-card ${rank === 1 ? 'rank1' : ''}" data-code="${s.school_code}" onclick="goToSchool('${s.school_code}')">
        <div class="top3-head">
          <div class="rank-block">
            <span class="rank-badge">${rank}순위</span>
            <span class="grade-badge ${gc}">${gl}</span>
          </div>
          <div class="priority-block">
            <span class="priority-label">검토 우선도 지수</span>
            <span class="priority-num">${fmtIndex(s.score)}</span>
          </div>
        </div>
        <div class="school-name">${s.school_name}</div>
        <div class="school-meta">${s.district || ''}구 · ${s.school_type || ''} · 카테고리 ${s.num_categories}개</div>
        <div class="cat-chips">${cats}</div>
        ${rep.rule_id ? `
          <div class="severe-row">
            <div class="severe-rule">${rep.rule_name_ko} <span class="rule-id">${rep.rule_id}</span></div>
            <div class="severe-numbers">${rep.detail || ''}</div>
          </div>` : ''}
        <div class="cta-row">
          <span class="rep-year">${rep.year ? rep.year + '년 공시' : ''}</span>
          <span class="cta">근거 보기 →</span>
        </div>
      </div>`;
  }).join('');
}

// ===== FILTER PANEL (좌측 — 지수 구간 / 룰 accordion / 25개 구 / 학교 유형) =====
function renderFilterPanel(dash) {
  const panel = document.getElementById('filter-panel');
  const ruleDist = dash.rule_distribution || [];
  const catDist = dash.category_distribution || [];
  const districts = dash.districts_all || [];

  // 학교 유형 수집 (allSchools에서)
  const typeSet = new Map();
  allSchools.forEach(s => {
    if (s.school_type) typeSet.set(s.school_type, (typeSet.get(s.school_type) || 0) + 1);
  });
  const types = Array.from(typeSet.entries()).sort((a, b) => b[1] - a[1]);

  // 룰 accordion 구조 — 카테고리별 묶음. RULE_META 25개 전부 노출, 상태 표시.
  const ruleByCat = {};
  ruleDist.forEach(r => {
    if (!ruleByCat[r.category_code]) ruleByCat[r.category_code] = { ko: r.category_ko, rules: [], total: 0 };
    ruleByCat[r.category_code].rules.push(r);
    if (r.status === 'active') ruleByCat[r.category_code].total += r.count;
  });
  // 카테고리 순서: dash.category_distribution 순서 따름
  const catOrder = catDist.map(c => c.code);

  // 구 활성/비활성 카운트
  const activeDistCount = districts.filter(d => d.active).length;
  const inactiveDistCount = districts.length - activeDistCount;

  panel.innerHTML = `
    <div class="filter-panel-head">
      <h4>필터</h4>
      <button class="filter-reset" id="filter-reset">전체 초기화</button>
    </div>

    <!-- 검토 우선도 구간 (v4 점수체계: 0~100) -->
    <div class="filter-section">
      <div class="filter-section-h">검토 우선도</div>
      <div class="fpc">
        <span class="fp-chip" data-filter="bin" data-val="critical">즉시 검토 <span class="fp-chip-cnt">(70+)</span></span>
        <span class="fp-chip" data-filter="bin" data-val="major">우선 검토 대상 <span class="fp-chip-cnt">(50~70)</span></span>
        <span class="fp-chip" data-filter="bin" data-val="minor">일반 검토 <span class="fp-chip-cnt">(30~50)</span></span>
        <span class="fp-chip" data-filter="bin" data-val="warning">참고 <span class="fp-chip-cnt">(0~30)</span></span>
      </div>
    </div>

    <!-- 룰/카테고리 accordion (RULE_META 전체 25개 + 상태 표시) -->
    <div class="filter-section">
      <div class="filter-section-h">룰 / 카테고리 <span class="fsh-cnt">${ruleDist.filter(r => r.status === 'active').reduce((a, r) => a + r.count, 0)}건</span></div>
      <div id="rule-acc-wrap">
        ${(() => {
          // 카테고리 순서: catOrder(탐지 있는 것 우선) + 그 외 (E1, F1 등)도 포함
          const seen = new Set();
          const order = [];
          catOrder.forEach(c => { if (ruleByCat[c]) { order.push(c); seen.add(c); } });
          Object.keys(ruleByCat).forEach(c => { if (!seen.has(c)) order.push(c); });
          return order.map(c => {
            const grp = ruleByCat[c];
            return `
              <div class="rule-acc" data-cat="${c}">
                <div class="rule-acc-head" onclick="toggleRuleAcc('${c}')">
                  <span class="rule-acc-title"><span class="rac-arr">▸</span> ${grp.ko}</span>
                  <span class="rule-acc-cnt">${grp.total}건</span>
                </div>
                <div class="rule-acc-body">
                  <div style="display:flex;justify-content:space-between;align-items:center;padding:2px 6px 4px">
                    <button class="rac-toggle-all" onclick="event.stopPropagation();toggleCatRules('${c}', true)">전체 선택</button>
                    <button class="rac-toggle-all" onclick="event.stopPropagation();toggleCatRules('${c}', false)">전체 해제</button>
                  </div>
                  ${grp.rules.map(r => _ruleAccItem(r)).join('')}
                </div>
              </div>`;
          }).join('');
        })()}
      </div>
    </div>

    <!-- 서울 25개 구 -->
    <div class="filter-section">
      <div class="filter-section-h">서울 25개 구 <span class="fsh-cnt">보유 ${activeDistCount}/확장 ${inactiveDistCount}</span></div>
      <div class="district-grid">
        ${districts.map(d => `
          <span class="fp-chip ${d.active ? '' : 'disabled'}"
                ${d.active ? `data-filter="district" data-val="${d.name}" onclick="toggleFilterChip(this)"` : ''}
                title="${d.active ? d.name + ' (' + d.schools + '교 보유)' : d.name + ' (데이터 없음)'}">
            ${d.name}${d.active ? `<span class="fp-chip-cnt">${d.schools}</span>` : ''}
          </span>`).join('')}
      </div>
      <div class="district-summary">데이터 보유 ${activeDistCount}개 구 / 확장 예정 ${inactiveDistCount}개 구</div>
    </div>

    <!-- 학교 유형 -->
    <div class="filter-section">
      <div class="filter-section-h">학교 유형</div>
      <div class="fpc">
        ${types.map(([t, c]) => `
          <span class="fp-chip" data-filter="type" data-val="${t}" onclick="toggleFilterChip(this)">
            ${t}<span class="fp-chip-cnt">${c}</span>
          </span>`).join('')}
      </div>
    </div>
  `;

  // 칩 바인딩
  panel.querySelectorAll('.fp-chip[data-filter="bin"]').forEach(ch => {
    ch.onclick = () => toggleFilterChip(ch);
  });
  document.getElementById('filter-reset').onclick = resetAllFilters;
}

// 룰 accordion 항목 한 개 — 상태별 표시
// D2-1·E1-1은 위험도 2 + 원천 시설 결측 영향으로 광범위 탐지되므로 "참고 성격" 안내 툴팁.
const _BROAD_REFERENCE_RULES = new Set(['D2-1', 'E1-1']);
function _ruleAccItem(r) {
  const st = r.status || 'active';
  if (st === 'active') {
    const cntCls = r.count > 0 ? 'rule-item-cnt' : 'rule-item-cnt zero';
    const click = `onclick="toggleRuleFilter('${r.rule_id}')"`;
    const broadTip = _BROAD_REFERENCE_RULES.has(r.rule_id)
      ? ' title="원천 입력 특성 영향으로 광범위 탐지 — 참고 수준 신호"' : '';
    const broadMark = _BROAD_REFERENCE_RULES.has(r.rule_id)
      ? ' <span class="rule-item-broad">참고</span>' : '';
    return `<div class="rule-item ${r.count === 0 ? 'no-hits' : ''}" data-rule="${r.rule_id}" ${click}${broadTip}>
      <span class="rule-item-id">${r.rule_id}</span>
      <span class="rule-item-name" title="${r.rule_name_ko}">${r.rule_name_ko}${broadMark}</span>
      <span class="${cntCls}">${r.count}건</span>
    </div>`;
  }
  // needs_mapping — 클릭 비활성, 안내 텍스트
  const tip = r.mapping_note ? r.mapping_note.replace(/"/g, '&quot;') : '현재 수집 범위 확인 필요';
  return `<div class="rule-item needs-mapping" data-rule="${r.rule_id}" title="${tip}">
    <span class="rule-item-id">${r.rule_id}</span>
    <span class="rule-item-name" title="${r.rule_name_ko}">${r.rule_name_ko}</span>
    <span class="rule-item-cnt mapping">매핑 확인 필요</span>
  </div>`;
}

// ===== 필터 토글 =====
function toggleFilterChip(el) {
  const f = el.dataset.filter, v = el.dataset.val;
  if (!f || !v) return;
  const set = activeFilters[f];
  if (!set) return;
  if (set.has(v)) set.delete(v); else set.add(v);
  el.classList.toggle('active');
  applyFilterAndRender();
}

function toggleRuleFilter(ruleId) {
  const set = activeFilters.rule;
  if (set.has(ruleId)) set.delete(ruleId); else set.add(ruleId);
  document.querySelectorAll(`.rule-item[data-rule="${ruleId}"]`).forEach(el => el.classList.toggle('active', set.has(ruleId)));
  applyFilterAndRender();
}

function toggleRuleAcc(cat) {
  const el = document.querySelector(`.rule-acc[data-cat="${cat}"]`);
  if (el) el.classList.toggle('open');
}

function toggleCatRules(cat, on) {
  const acc = document.querySelector(`.rule-acc[data-cat="${cat}"]`);
  if (!acc) return;
  acc.querySelectorAll('.rule-item').forEach(it => {
    if (it.classList.contains('needs-mapping')) return;   // 매핑 확인 필요 룰은 필터 선택 제외
    const rid = it.dataset.rule;
    if (on) activeFilters.rule.add(rid); else activeFilters.rule.delete(rid);
    it.classList.toggle('active', on);
  });
  applyFilterAndRender();
}

function resetAllFilters() {
  Object.values(activeFilters).forEach(s => s.clear());
  document.querySelectorAll('.fp-chip.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.rule-item.active').forEach(el => el.classList.remove('active'));
  document.getElementById('nav-search').value = '';
  applyFilterAndRender();
}

// ===== 정렬 =====
function bindSort() {
  const sel = document.getElementById('sort-select');
  if (!sel) return;
  sel.value = currentSort;
  sel.onchange = () => { currentSort = sel.value; applyFilterAndRender(); };
}

function sortSchools(arr) {
  const a = arr.slice();
  switch (currentSort) {
    case 'score_asc':  a.sort((x, y) => (x.score || 0) - (y.score || 0)); break;
    case 'name_asc':   a.sort((x, y) => (x.school_name || '').localeCompare(y.school_name || '', 'ko')); break;
    case 'name_desc':  a.sort((x, y) => (y.school_name || '').localeCompare(x.school_name || '', 'ko')); break;
    case 'dets_desc':  a.sort((x, y) => (y.num_detections || 0) - (x.num_detections || 0)); break;
    case 'cats_desc':  a.sort((x, y) => (y.num_categories || 0) - (x.num_categories || 0)); break;
    case 'score_desc':
    default:           a.sort((x, y) => (y.score || 0) - (x.score || 0));
  }
  return a;
}

// ===== 필터 적용 + 렌더 =====
function applyFilterAndRender() {
  const q = (document.getElementById('nav-search').value || '').toLowerCase().trim();
  const f = activeFilters;
  const filtered = allSchools.filter(s => {
    if (q && !s.school_name.toLowerCase().includes(q)) return false;
    if (f.bin.size > 0) {
      if (!f.bin.has(indexBin(s.score))) return false;
    }
    if (f.district.size > 0 && !f.district.has(s.district)) return false;
    if (f.type.size > 0 && !f.type.has(s.school_type)) return false;
    // 카테고리 필터 — 학교가 가진 카테고리 중 하나라도 선택 카테고리에 속하면 통과 (독립)
    if (f.category.size > 0) {
      const codes = (s.categories_ko || []).map(c => c.code);
      if (!codes.some(c => f.category.has(c))) return false;
    }
    // 룰 필터 — rule_ids 배열에서 정확 매칭. 선택 룰 중 하나라도 학교 rule_ids에 포함되면 통과 (독립)
    if (f.rule.size > 0) {
      const ruleIds = s.rule_ids || [];
      let hit = false;
      for (const rid of f.rule) {
        if (ruleIds.includes(rid)) { hit = true; break; }
      }
      if (!hit) return false;
    }
    return true;
  });
  const sorted = sortSchools(filtered);
  renderActiveChips();
  renderSchoolList(sorted, allSchools.length);
}

function renderActiveChips() {
  const wrap = document.getElementById('active-chips');
  const f = activeFilters;
  const chips = [];
  if (f.bin.size) {
    const BIN_LABEL = { critical: '즉시 검토', major: '우선 검토 대상', minor: '일반 검토', warning: '참고' };
    f.bin.forEach(v => chips.push({ f: 'bin', v, label: BIN_LABEL[v] || v }));
  }
  f.district.forEach(v => chips.push({ f: 'district', v, label: v }));
  f.type.forEach(v => chips.push({ f: 'type', v, label: v }));
  f.rule.forEach(v => chips.push({ f: 'rule', v, label: v }));
  f.category.forEach(v => chips.push({ f: 'category', v, label: v }));

  if (!chips.length) {
    wrap.innerHTML = `<span class="ach-label">선택 필터</span><span style="font-size:11px;color:var(--text-muted);font-style:italic">없음</span>`;
    return;
  }
  wrap.innerHTML = `<span class="ach-label">선택 필터</span>` + chips.map(c =>
    `<span class="active-chip">${c.label}<button class="ach-x" onclick="removeFilter('${c.f}','${c.v}')">×</button></span>`
  ).join('');
}

function removeFilter(f, v) {
  const set = activeFilters[f];
  if (!set) return;
  set.delete(v);
  document.querySelectorAll(`[data-filter="${f}"][data-val="${v}"]`).forEach(el => el.classList.remove('active'));
  if (f === 'rule') document.querySelectorAll(`.rule-item[data-rule="${v}"]`).forEach(el => el.classList.remove('active'));
  applyFilterAndRender();
}

// ===== DISTRIBUTION (우측 패널) — v4 점수체계 4단계 (필터 chip과 라벨 통일) =====
function renderDistBars(dist) {
  const order = [
    ['critical', '즉시 검토',      'priority'],
    ['major',    '우선 검토 대상',  'priority'],
    ['minor',    '일반 검토',      'normal'],
    ['warning',  '참고',           'normal'],
  ];
  const mx = Math.max(...Object.values(dist), 1);
  document.getElementById('dist-bars').innerHTML = order.map(([k, label, cls]) => {
    const v = dist[k] || 0;
    return `<div class="dist-row">
      <span class="dist-label">${label}</span>
      <div class="dist-bar-bg"><div class="dist-bar ${cls}" style="width:${(v / mx) * 100}%"></div></div>
      <span class="dist-count">${v}</span>
    </div>`;
  }).join('');
}

function renderCatDist(catDist) {
  document.getElementById('cat-dist').innerHTML = catDist.map(c => `
    <div class="cat-dist-row">
      <span class="cat-name">${c.ko}</span>
      <span class="cat-code-tag">${c.code}</span>
      <span class="cat-count">${c.count}</span>
    </div>`).join('');
}

// ===== SCHOOL LIST =====
function renderSchoolList(schools, total) {
  const tbl = document.getElementById('school-list-table');
  const countText = (total != null && total !== schools.length)
    ? `<b>${schools.length}</b>교 표시 / 전체 ${total}교 · 정렬: ${sortLabel(currentSort)}`
    : `<b>${schools.length}</b>교 · 정렬: ${sortLabel(currentSort)}`;
  document.getElementById('list-count').innerHTML = countText;

  if (!schools.length) {
    tbl.innerHTML = `<tbody><tr><td style="padding:36px;text-align:center;color:var(--text-muted)">조건에 맞는 학교가 없습니다. 좌측 필터를 조정해 보세요.</td></tr></tbody>`;
    return;
  }

  tbl.innerHTML = `
    <thead><tr><th>순위</th><th>학교</th><th>구·유형</th><th>탐지 카테고리</th><th>구분</th><th style="text-align:right" title="검토 우선도 지수">지수</th></tr></thead>
    <tbody>${schools.map(s => rowHtml(s)).join('')}</tbody>`;
  tbl.querySelectorAll('tbody tr[data-code]').forEach(tr => {
    tr.onclick = () => goToSchool(tr.dataset.code);
  });
}

function sortLabel(k) {
  return {
    score_desc: '지수 ↓', score_asc: '지수 ↑',
    name_asc: '학교명 가나다', name_desc: '학교명 역순',
    dets_desc: '신호 수', cats_desc: '카테고리 수'
  }[k] || k;
}

function rowHtml(s) {
  const gl = indexLabel(s.score), gc = indexCls(s.score);
  const cats = (s.categories_ko || []).slice(0, 5).map(c =>
    `<span class="cat-mini" title="${c.code}">${c.ko}</span>`).join('');
  return `<tr data-code="${s.school_code}">
    <td class="rank-cell">${s.rank}</td>
    <td class="school-cell">${wrapSchoolName(s.school_name)}</td>
    <td class="dist-cell">${s.district || ''} · ${s.school_type || ''}</td>
    <td class="cats-cell">${cats}</td>
    <td class="badge-cell"><span class="grade-badge ${gc}">${gl}</span></td>
    <td class="score-cell"><span class="idx-pill">${fmtIndex(s.score)}</span></td>
  </tr>`;
}

// ===== NAVIGATE TO SCHOOL =====
function goToSchool(code) {
  currentSchoolCode = code;
  document.getElementById('nav-school').style.display = '';
  showView('school');
  loadSchool(code);
}

async function loadSchool(code) {
  try {
    const d = await (await fetch(API + `/api/school/${code}`)).json();
    renderSchool(d);
  } catch (e) { console.error(e); }
}

// 현재 학교 상세 컨텍스트 (마스터-디테일 상태)
let currentSchoolData = null;
let currentSelectedRule = null;

function renderSchool(d) {
  currentSchoolData = d;
  const total = (dashboardData && dashboardData.total_schools) || allSchools.length || 0;
  const gl = indexLabel(d.score), gc = indexCls(d.score);
  document.getElementById('school-header').innerHTML = `
    <div class="sh-info">
      <h2>${d.school_name}</h2>
      <p class="sh-meta">${d.district}구 · ${d.school_type} · 2023~2025년 공시 · <span class="grade-badge ${gc}">${gl}</span></p>
    </div>
    <div class="sh-score">
      <span class="score-sub-label">검토 우선도 지수</span>
      <span class="score-num">${fmtIndex(d.score)}</span>
      <span class="score-label">${d.rank}위 / ${total}교</span>
    </div>`;

  // AI 보조 요약 — 학교 상단 한 곳에서만. 실패/없음이면 조용히 숨김.
  const aiWrap = document.getElementById('ai-summary-wrap');
  const aiSum = document.getElementById('ai-summary');
  const aiTxt = d.llm_explanation || '';
  const aiOk = aiTxt && !aiTxt.startsWith('(') && aiTxt !== FALLBACK_AI_TEXT;
  if (aiOk) {
    let txt = aiTxt.replace(/([②③])/g, '\n$1');
    aiSum.innerHTML = txt.split('\n').map(l => l.trim()).filter(Boolean).map(l => `<div style="margin-bottom:3px">${l}</div>`).join('');
    aiWrap.style.display = '';
  } else {
    aiWrap.style.display = 'none';
  }

  // 상단 요약 5박스
  const cats = d.summary.categories_ko || [];
  const repeatTxt = d.is_repeat ? '3년 반복 신호' : (d.summary.detections > 1 ? '복수 연도 신호' : '최근 연도 중심');
  document.getElementById('sd-summary-grid').innerHTML = `
    <div class="sd-summary-card lead">
      <div class="sdc-num">${fmtIndex(d.score)}</div>
      <div class="sdc-label">검토 우선도 지수</div>
      <div class="sdc-detail">${d.rank}위 / ${total}교 · ${gl}</div>
    </div>
    <div class="sd-summary-card">
      <div class="sdc-num">${d.summary.detections}<small>건</small></div>
      <div class="sdc-label">검토 신호 수</div>
      <div class="sdc-detail">연도×룰 단위</div>
    </div>
    <div class="sd-summary-card">
      <div class="sdc-num">${d.num_rules || 0}<small>개</small></div>
      <div class="sdc-label">관련 세부 룰</div>
      <div class="sdc-detail">${cats.length}개 카테고리</div>
    </div>
    <div class="sd-summary-card">
      <div class="sdc-num" style="font-size:14px;line-height:1.3">${repeatTxt}</div>
      <div class="sdc-label">반복 여부</div>
      <div class="sdc-detail">${d.is_repeat ? '동일 룰이 3년 연속' : '단일·복수 연도'}</div>
    </div>
    <div class="sd-summary-card">
      <div class="sdc-num" style="font-size:13px;line-height:1.3">${cats.slice(0, 2).join(' · ') || '—'}</div>
      <div class="sdc-label">주요 확인 영역</div>
      <div class="sdc-detail">${cats.length > 2 ? '외 ' + (cats.length - 2) + '개' : ''}</div>
    </div>`;

  // 마스터-디테일 — 좌 룰 리스트 / 우 선택 룰 상세 + Evidence Chart
  renderMasterDetail(d);

  // 자가진단 리포트 (종합 요약)
  renderSelfReport(d.self_report);

  // 원자료 테이블 (최하단, 기본 닫힘)
  renderDataTable(d.data_table, 'full-data-table');

  updateChatContext();
}

// ===== MASTER-DETAIL (좌 룰 리스트 / 우 선택 룰 상세) =====
function _collectRulesFromCards(cards) {
  // 카테고리 카드 → 룰 단위 평탄화. col_pairs(key+label 짝)를 detection 단위로 누적(key 기준 중복 제거).
  const map = {};
  (cards || []).forEach(cat => {
    (cat.rules || []).forEach(r => {
      if (!map[r.rule_id]) {
        map[r.rule_id] = {
          rule_id: r.rule_id,
          rule_name_ko: r.rule_name_ko,
          category_ko: cat.category_ko,
          cat_code: cat.cat_code,
          col_pairs: [],     // [{key, label}, ...] — 매칭은 key, 표시는 label
          col_labels: [],    // 표시 라벨 (한글)
          col_keys: [],      // 매칭 키 (영문)
          years: [],
          details: [],
          sr: 0,             // 룰 단위 max s_r (정렬·강조 단일 출처)
          data_table: cat.data_table || [],
          sixbox: r.sixbox || null,    // 첫 detection의 6박스를 룰 기본값으로 채택
        };
      }
      const m = map[r.rule_id];
      // 6박스가 비어 있고 새 detection이 갖고 있으면 보강
      if (!m.sixbox && r.sixbox) m.sixbox = r.sixbox;
      // col_pairs를 우선 가져옴 (백엔드가 짝지어 내려줌). 없으면 col_keys/col_labels 인덱스로 짝 추정.
      const pairs = Array.isArray(r.col_pairs) && r.col_pairs.length
        ? r.col_pairs
        : ((r.col_keys || []).map((k, i) => ({ key: k, label: (r.col_labels || [])[i] || k })));
      pairs.forEach(p => {
        if (!p || !p.key) return;
        if (!m.col_pairs.find(x => x.key === p.key)) {
          m.col_pairs.push({ key: p.key, label: p.label || p.key });
          m.col_keys.push(p.key);
          m.col_labels.push(p.label || p.key);
        }
      });
      m.years.push(r.year);
      m.details.push({ year: r.year, detail: r.detail });
      // 룰 카드 정렬·라벨 기준: s_r(탐지 건 점수 0~10) 단일 출처. star는 미사용.
      const sr = Number(r.s_r) || 0;
      if (sr > m.sr) m.sr = sr;
    });
  });
  return Object.values(map).sort((a, b) => {
    if (b.sr !== a.sr) return b.sr - a.sr;
    return a.rule_id.localeCompare(b.rule_id);
  });
}

// 탐지 건 점수 s_r(0~10)을 카드 시각 강조에 매핑.
// 임계: 7.5 / 4 — 점수체계.html "점수가 높으면 뭘 의미하는가" 4구간 단순화.
function _ruleGradeCls(sr) {
  const v = Number(sr) || 0;
  return v >= 7.5 ? 'grade-priority' : v >= 4 ? 'grade-normal' : 'grade-ref';
}
function _ruleGradeLabel(sr) {
  const v = Number(sr) || 0;
  if (v >= 7.5) return '즉시 검토';
  if (v >= 4)   return '우선 확인';
  return '참고';
}

function renderMasterDetail(d) {
  const rules = _collectRulesFromCards(d.detection_cards);
  const listEl = document.getElementById('md-rule-list');
  const cntEl = document.getElementById('md-master-cnt');
  cntEl.textContent = `${rules.length}개`;

  if (!rules.length) {
    listEl.innerHTML = '<div style="padding:18px;font-size:11px;color:var(--text-muted);text-align:center">표시할 검토 신호가 없습니다.</div>';
    document.getElementById('md-detail').innerHTML = '<div class="md-empty">표시할 검토 신호가 없습니다.</div>';
    currentSelectedRule = null;
    return;
  }

  listEl.innerHTML = rules.map(r => {
    const isRepeat = new Set(r.years).size >= 3;
    const yrs = Array.from(new Set(r.years)).sort();
    const yrTxt = yrs.length === 1 ? `${yrs[0]}년` : `${yrs[0]}~${yrs[yrs.length - 1]}년`;
    // 핵심 수치 1개 — 가장 최근 연도의 detail 첫 50자
    const latest = r.details.slice().sort((a, b) => b.year - a.year)[0];
    const headline = latest ? latest.detail : '';
    const gcls = _ruleGradeCls(r.sr);
    const glabel = _ruleGradeLabel(r.sr);
    return `
      <div class="md-rule" data-rule="${r.rule_id}" onclick="selectRule('${r.rule_id}')">
        <div class="md-rule-name">${r.rule_name_ko}<span class="md-rule-rid">${r.rule_id}</span></div>
        <div class="md-rule-meta">
          <span class="md-rule-year">${yrTxt}</span>
          ${isRepeat ? '<span class="md-rule-repeat">반복</span>' : ''}
          <span class="md-rule-grade ${gcls}">${glabel}</span>
        </div>
        <div class="md-rule-headline">${headline}</div>
      </div>`;
  }).join('');

  // 기본 선택: 가장 강한 신호 (s_r 우선, 동률 시 첫 번째)
  selectRule(rules[0].rule_id);
}

function selectRule(ruleId) {
  if (!currentSchoolData) return;
  const rules = _collectRulesFromCards(currentSchoolData.detection_cards);
  const rule = rules.find(r => r.rule_id === ruleId);
  if (!rule) return;
  currentSelectedRule = rule;

  // 좌측 활성 표시
  document.querySelectorAll('.md-rule').forEach(el => el.classList.toggle('active', el.dataset.rule === ruleId));

  // 우측 상세 렌더
  const yrs = Array.from(new Set(rule.years)).sort();
  const yrTxt = yrs.length === 1 ? `${yrs[0]}년` : `${yrs[0]}~${yrs[yrs.length - 1]}년`;
  const isRepeat = new Set(rule.years).size >= 3;
  const gcls = _ruleGradeCls(rule.sr);
  const glabel = _ruleGradeLabel(rule.sr);

  // 수치 테이블 (룰 관련 컬럼만)
  const dt = rule.data_table || [];
  const colLabels = new Set(rule.col_labels || []);
  const rows = dt.filter(row => colLabels.size === 0 || colLabels.has(row['지표']));
  let numTable = '';
  if (rows.length) {
    const yearsAll = Object.keys(rows[0]).filter(k => /^\d{4}$/.test(k)).sort();
    const detYears = new Set(rule.years.map(Number));
    numTable = `<table class="md-detail-num-table"><thead><tr><th>지표</th>${yearsAll.map(y => `<th${detYears.has(+y) ? ' style="background:var(--cobalt-bg);color:var(--cobalt)"' : ''}>${y}</th>`).join('')}<th>동료군 평균</th></tr></thead><tbody>`;
    rows.forEach(row => {
      numTable += '<tr><td>' + row['지표'] + '</td>';
      yearsAll.forEach(y => {
        const v = row[y];
        const fmt = v == null ? '-' : (typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1)) : v);
        numTable += `<td${detYears.has(+y) ? ' class="md-detected"' : ''}>${fmt}</td>`;
      });
      const p = row['동료군'];
      numTable += `<td class="md-peer">${p != null ? (Number.isInteger(p) ? p.toLocaleString() : p.toFixed(1)) : '-'}</td></tr>`;
    });
    numTable += '</tbody></table>';
  }

  // 연도별 detail 리스트 — 컬럼 정렬 (PDF 피드백 8: 연도/지표/괄호 3열)
  // detail이 "지표 값 (괄호 설명)" 형태면 3열로 분리, 아니면 그대로.
  const detailLines = rule.details.sort((a, b) => a.year - b.year).map(x => {
    const detail = x.detail || '';
    const m = detail.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
    if (m) {
      return `<div class="dd-row">
        <span class="dd-yr"><strong>${x.year}년</strong></span>
        <span class="dd-main">${m[1].trim()}</span>
        <span class="dd-paren">(${m[2].trim()})</span>
      </div>`;
    }
    return `<div class="dd-row dd-row-plain">
      <span class="dd-yr"><strong>${x.year}년</strong></span>
      <span class="dd-main">${detail}</span>
    </div>`;
  }).join('');

  // 차트 캡션 — 룰 단위
  const caption = _ruleChartCaption(rule);

  // 확인 권장 — 룰 단위 가이드 (AI 사용 안 함, 정적 매핑)
  const recText = _ruleRecommendation(rule);

  document.getElementById('md-detail').innerHTML = `
    <div class="md-detail-head">
      <div class="md-h-name">${rule.rule_name_ko}<span class="md-h-rid">${rule.rule_id}</span></div>
      <div class="md-h-meta">
        <span class="md-h-year">${yrTxt}</span>
        ${isRepeat ? '<span class="md-rule-repeat">반복</span>' : ''}
        <span class="md-h-grade grade-badge ${gcls}">${glabel}</span>
      </div>
    </div>

    <div class="md-detail-section-h">핵심 검토 신호</div>
    <div class="md-detail-detail-list">${detailLines}</div>

    <div class="md-detail-section-h">Evidence Chart — ${rule.col_labels.join(' · ') || '관련 지표'}</div>
    <div class="md-chart-wrap"><canvas id="md-evidence-chart"></canvas></div>
    <div class="md-chart-caption">${caption}</div>

    ${numTable ? `<div class="md-detail-section-h">수치 + 동료군 비교</div>${numTable}` : ''}

    <div class="md-detail-section-h">6박스 요약</div>
    ${_renderSixBox(rule.sixbox)}

    <div class="md-detail-section-h">확인 권장</div>
    <div class="md-detail-rec">${recText}</div>
  `;

  // Evidence Chart 그리기
  renderEvidenceChart(rule);
}

function _ruleChartCaption(rule) {
  // 룰별 한 줄 설명 — "왜 이 지표가 확인 대상으로 잡혔는지"
  const map = {
    'C1-1': '학생수와 학급수가 반대 방향으로 움직이면 자원 배분이 의도된 결과인지 확인이 필요합니다.',
    'C1-3': '학생수는 안정인데 교원수가 급변하면 정원·강사 분류 기준을 확인해 주세요.',
    'C1-8': '학급당 학생수의 급변은 학급수 또는 학생수 파싱·집계 정확성을 점검할 신호입니다.',
    'C3-3A': '피해학생수 대비 보호조치가 적으면 미조치 가능성을 확인해 주세요.',
    'C3-3B': '피해·보호조치 비대칭 신호. 가해학생 조치 미실시 사유도 함께 확인합니다.',
    'B1-1': '학생·교원의 전년 대비 ±10% 이상 변동은 정원 변동·이동 사유 확인이 필요합니다.',
    'B1-5': '진학률 급변은 졸업생 분모·진학 분류 기준 변경 가능성도 함께 확인합니다.',
    'B1-6': '학폭 심의 건수의 급증은 학년도와 공시연도 기준 차이를 함께 확인해 주세요.',
    'C2-3': '급식비 변동은 입력단위(천원) 준수 여부와 사업 외 항목 포함 여부를 확인합니다.',
    'C2-3+': '급식비 강한 변동. 단위 혼동·1회성 사업 반영 가능성을 우선 확인해 주세요.',
    'D2-2': '같은 구 일반고 분포의 극단값입니다. 학교 특성(소규모/예술 등) 정상 예외 가능성도 확인합니다.',
    'C5-1': '학생수 변동이 자연 감소 범위(-7~+3%)를 벗어났습니다. 신·편입학 또는 전출 사유를 확인합니다.',
    'E2-2': '3년간 동일 수치는 데이터 미갱신 가능성을 확인할 신호입니다.',
    "F1'-1": '학교알리미와 KESS 교원수 차이는 강사 포함/미포함 기준 차이일 수 있습니다.',
    'G1-1': '단년 급변은 없지만 다년에 걸쳐 같은 방향으로 누적 변화가 일어난 패턴입니다. B1(단년 급변동)에는 안 잡히는 누적 변화를 확인할 신호입니다. (본교 단일 시계열 기준 · 동료군 대비 비교는 G1-2/G1-3 후속 룰로 검토)',
  };
  return map[rule.rule_id] || '본교 시계열과 동료군(같은 구) 비교를 통해 확인이 필요한 지표입니다.';
}

function _ruleRecommendation(rule) {
  const map = {
    'C1-1': '<b>확인 권장</b>: 학생수·학급수 입력 원본을 함께 확인하고, 학급 신설/통폐합 여부와 학생 전출입을 점검해 주세요.',
    'C1-3': '<b>확인 권장</b>: 교원 총계 산정 기준(강사 포함 여부)을 확인하고, 정원·기간제·강사 분류를 점검해 주세요.',
    'C1-8': '<b>확인 권장</b>: 학급수 원본 표기("28(3)" 등 괄호 포함 시 총학급수 파싱 정확성)를 우선 확인해 주세요.',
    'C3-3A': '<b>확인 권장</b>: 피해학생 보호조치 미실시 사유 또는 미입력 사유를 확인해 주세요. 가해학생 조치 별도 미입력 시 가해학생수 포함 여부도 점검합니다.',
    'C3-3B': '<b>확인 권장</b>: 보호조치·가해조치 입력 누락 여부를 확인하고, 학년도(공시연도 -1) 기준이 맞는지 함께 점검해 주세요.',
    'B1-1': '<b>확인 권장</b>: 직전 2년 평균 대비 변동 폭의 사유(통폐합·정원 조정·이동 등)를 확인해 주세요.',
    'B1-5': '<b>확인 권장</b>: 진학률 산정 분모(졸업생 수)와 진학 분류 기준(전문대 포함 여부 등)을 확인해 주세요.',
    'B1-6': '<b>확인 권장</b>: 학폭 심의 건수의 학년도 기준(공시연도 -1)을 확인하고, 실태조사 결과와 함께 점검해 주세요.',
    'C2-3': '<b>확인 권장</b>: 급식비 입력단위(천원)와 사업 항목 분류를 확인해 주세요.',
    'C2-3+': '<b>확인 권장</b>: 급식비 단위 혼동(천원↔원) 또는 1회성 사업 반영 여부를 우선 확인해 주세요.',
    'D2-2': '<b>확인 권장</b>: 학교 유형(소규모·예술고 등)에 따른 정상 예외 가능성을 확인하고, 동료군 정의가 적정한지 함께 점검해 주세요.',
    'C5-1': '<b>확인 권장</b>: 신·편입학, 전출·전입, 자퇴·휴학 등 학생수 변동 사유를 확인해 주세요.',
    'E2-2': '<b>확인 권장</b>: 3년간 동일 수치가 실제 변동 없음인지, 입력 갱신 누락인지 확인해 주세요.',
    "F1'-1": '<b>확인 권장</b>: 학교알리미 교원총계에서 강사를 제외하고 KESS와 비교해 주세요.',
    'G1-1': '<b>확인 권장</b>: 다년 누적 변동의 사유(인구 변화·학교 운영 변화·정책 영향 등)를 함께 확인해 주세요. 단년 급변 룰(B1)에는 잡히지 않지만 누적 추세는 지속 모니터링 권장.',
  };
  return map[rule.rule_id] || '<b>확인 권장</b>: 본교 값과 동료군 값을 비교하여 정상 예외 가능성과 입력 정확성을 확인해 주세요.';
}

// ===== EVIDENCE CHART (룰 타입별 분기) =====
//  · D2-2 → Peer Range Dot (동료군 분포 + 본교 위치)
//  · C5-1 → Delta Arrow (1학년 → 2학년 진급 인원)
//  · E2-2 → Status Timeline (연도별 동일값 점선 연결)
//  · 그 외 → 라인 차트 (fallback, 일반 추세 확인용)
let mdChart = null;
function renderEvidenceChart(rule) {
  const cd = currentSchoolData && currentSchoolData.chart_data;
  if (!cd) return;
  const wrap = document.getElementById('md-chart-wrap-container');
  // wrap이 없는 경우(기존 마크업이 canvas만 갖고 있음): canvas 부모를 동적으로 교체
  const cvs = document.getElementById('md-evidence-chart');
  if (!cvs) return;
  const host = cvs.parentElement;        // .md-chart-wrap
  if (mdChart) { mdChart.destroy(); mdChart = null; }

  // 룰 ID로 차트 타입 결정
  const rid = rule.rule_id;
  if (rid === 'D2-2' || rid === 'D2-1') {
    return _renderPeerRangeDot(host, rule);
  }
  if (rid === 'C5-1') {
    return _renderDeltaArrow(host, rule);
  }
  if (rid === 'E2-2' || rid === 'E1-1' || rid === 'E1-2') {
    return _renderStatusTimeline(host, rule);
  }
  if (rid === 'G1-1') {
    return _renderDriftTrend(host, rule);
  }
  // fallback: 기존 라인 차트
  return _renderLineChart(host, rule);
}

// ── 공통 6박스 렌더러 (룰 단위 + 챗봇 공유) ──
//  · 백엔드가 LLM 없이 정적 + 자동 주입으로 안전하게 채워서 내려옴 (sixbox 객체)
//  · 표시 항목: 1.핵심 발견 / 2.수치 변화 / 3.패턴 해석 / 4.동료군 맥락 / 5.정상 예외 / 6.확인 권장
//  · sixbox가 없거나 일부 비면 그 박스는 "—"로 조용히 표시 (화면 깨짐 방지)
function _renderSixBox(sixbox) {
  if (!sixbox || typeof sixbox !== 'object') {
    return '<div class="sb-empty">6박스 요약 데이터가 없습니다.</div>';
  }
  const items = [
    { key: 'finding',   lb: '1. 핵심 발견',     icon: '🎯' },
    { key: 'numbers',   lb: '2. 수치 변화',     icon: '📊' },
    { key: 'pattern',   lb: '3. 패턴 해석',     icon: '🔍' },
    { key: 'peer',      lb: '4. 동료군 맥락',   icon: '🏫' },
    { key: 'normal',    lb: '5. 정상 예외 가능성', icon: '💡' },
    { key: 'recommend', lb: '6. 확인 권장',     icon: '✅' },
  ];
  return `<div class="sb-grid">${items.map(it => {
    const val = (sixbox[it.key] || '').toString().trim();
    return `<div class="sb-card sb-${it.key}">
      <div class="sb-h"><span class="sb-i">${it.icon}</span><span class="sb-lb">${it.lb}</span></div>
      <div class="sb-v">${val || '—'}</div>
    </div>`;
  }).join('')}</div>`;
}

// ── 공통 헬퍼: 매칭은 col_key, 표시는 한국어 col_label ──
// 백엔드가 col_pairs로 짝을 보존하므로 result.label은 항상 한국어.
function _seriesForRule(rule) {
  const cd = currentSchoolData && currentSchoolData.chart_data;
  if (!cd || !cd.series) return [];
  const result = [];
  const usedKeys = new Set();
  const usedLabels = new Set();
  const pushIfFound = (key, label) => {
    if (!key && !label) return;
    const dispLabel = label || key;
    if (usedKeys.has(key) || usedLabels.has(dispLabel)) return;
    // 우선 col_key로 매칭, 없으면 label로
    const payload = (key && cd.series[key]) || (label && cd.series[label]) || null;
    if (!payload) return;
    usedKeys.add(key); usedLabels.add(dispLabel);
    result.push({ key: key || dispLabel, label: dispLabel, payload });
  };
  (rule.col_pairs || []).forEach(p => pushIfFound(p.key, p.label));
  // col_pairs가 비었을 때 col_keys/col_labels로 보강
  if (!result.length) {
    const keys = rule.col_keys || [];
    const labels = rule.col_labels || [];
    const n = Math.max(keys.length, labels.length);
    for (let i = 0; i < n; i++) pushIfFound(keys[i], labels[i]);
  }
  // 그래도 비면 학생수 fallback
  if (!result.length && cd.series['학생수']) {
    result.push({ key: 'student_count', label: '학생수', payload: cd.series['학생수'] });
  }
  return result;
}

// ── (Fallback) 라인 차트 ──
function _renderLineChart(host, rule) {
  // host 내부에 canvas 보장
  host.innerHTML = '<canvas id="md-evidence-chart"></canvas>';
  const cvs = document.getElementById('md-evidence-chart');
  const cd = currentSchoolData.chart_data;
  const labels = cd.labels;
  const detYears = new Set(rule.years.map(Number));

  const series = _seriesForRule(rule).slice(0, 2);
  if (!series.length) { host.innerHTML = '<div class="ev-no-chart">표시할 시계열 데이터가 없습니다.</div>'; return; }

  const palette = ['#1D4ED8', '#7C3AED'];
  const peerPalette = ['rgba(29,78,216,0.18)', 'rgba(124,58,237,0.18)'];
  const datasets = [];
  series.forEach(({ label, payload: s }, i) => {
    const color = palette[i % palette.length];
    const bandColor = peerPalette[i % peerPalette.length];
    if (s.peer_max && s.peer_min && s.peer_max.some(v => v != null)) {
      datasets.push({ label: `${label} 동료군 범위 최대`, data: s.peer_max, borderColor: 'transparent', backgroundColor: bandColor, pointRadius: 0, fill: false, order: 30 + i });
      datasets.push({ label: `${label} 동료군 범위`, data: s.peer_min, borderColor: 'transparent', backgroundColor: bandColor, pointRadius: 0, fill: '-1', order: 31 + i });
    }
    datasets.push({ label: `${label} 동료군 평균`, data: s.peer_mean, borderColor: color, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, fill: false, order: 20 + i });
    datasets.push({ label: `${label} (본교)`, data: s.self, borderColor: color, backgroundColor: color, borderWidth: 3.5, pointRadius: 5, pointHoverRadius: 7, tension: 0.25, fill: false, order: 1 + i });
  });

  const detectedYearPlugin = {
    id: 'detectedYearBg',
    beforeDatasetsDraw(chart) {
      const ctx = chart.ctx, xScale = chart.scales.x, yScale = chart.scales.y;
      ctx.save();
      labels.forEach((yr, idx) => {
        if (detYears.has(+yr)) {
          const x = xScale.getPixelForValue(yr);
          const halfW = (xScale.width / labels.length) * 0.5;
          ctx.fillStyle = 'rgba(245, 158, 11, 0.10)';
          ctx.fillRect(x - halfW, yScale.top, halfW * 2, yScale.height);
        }
      });
      ctx.restore();
    },
  };

  mdChart = new Chart(cvs, {
    type: 'line', data: { labels, datasets }, plugins: [detectedYearPlugin],
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 12, family: 'Pretendard Variable', weight: '600' }, usePointStyle: true, padding: 12, filter: (item) => !item.text.includes('범위 최대') } },
        tooltip: { titleFont: { size: 13, weight: '700' }, bodyFont: { size: 12.5 }, padding: 10, callbacks: { title: (items) => `${items[0].label}년${detYears.has(+items[0].label) ? ' · 검토 신호 발생' : ''}` } },
      },
      scales: { y: { beginAtZero: false, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 13 } } }, x: { grid: { display: false }, ticks: { font: { size: 13, weight: '700' } } } },
    },
  });
}

// ── E2-2 / E1-1 / E1-2 — Status Timeline ──
// 연도별 점 + 같은 값(또는 미입력)이 반복되면 점선 연결, 마지막에 상태 라벨
function _renderStatusTimeline(host, rule) {
  const cd = currentSchoolData.chart_data;
  const labels = cd.labels;                       // 연도 배열
  const series = _seriesForRule(rule)[0];          // 첫 컬럼 (E2-2/E1은 detection별 단일 컬럼)
  const vals = series ? series.payload.self : labels.map(() => null);
  const colLabel = series ? series.label : (rule.col_labels[0] || '');
  const detYears = new Set(rule.years.map(Number));

  // 분기: E1은 NaN(미입력) 패턴, E2-2는 동일값 패턴
  const isMissingRule = (rule.rule_id === 'E1-1' || rule.rule_id === 'E1-2');
  const isSameRule = (rule.rule_id === 'E2-2');

  const dots = labels.map((yr, i) => {
    const v = vals[i];
    const missing = v == null;
    const isLatest = i === labels.length - 1;
    let cls = 'st-dot';
    if (missing) cls += ' missing';
    else if (isSameRule) cls += ' same';
    if (detYears.has(+yr)) cls += ' detected';
    const valTxt = missing ? '미입력' : (Number.isInteger(v) ? Number(v).toLocaleString() : Number(v).toFixed(1));
    return `<div class="st-col ${isLatest ? 'st-latest' : ''}">
      <div class="${cls}" title="${yr}년: ${valTxt}"></div>
      <div class="st-val">${valTxt}</div>
      <div class="st-yr">${yr}</div>
    </div>`;
  }).join('');

  let summary = '';
  if (isSameRule) {
    const last3 = vals.slice(-3).filter(v => v != null);
    if (last3.length >= 3 && last3.every(v => v === last3[0])) {
      const t = last3[0];
      summary = `<div class="st-sum">최근 3년 동일값 <b>${Number.isInteger(t) ? Number(t).toLocaleString() : Number(t).toFixed(1)}</b> · 입력 갱신 여부 확인 필요</div>`;
    } else {
      summary = `<div class="st-sum">최근 ${labels.length}년 시계열에서 같은 값이 반복된 구간 표시</div>`;
    }
  } else if (isMissingRule) {
    const missCnt = vals.filter(v => v == null).length;
    summary = `<div class="st-sum">${labels.length}년 중 ${missCnt}년 미입력 · 동료군 입력 패턴과 함께 확인 권장</div>`;
  }

  host.innerHTML = `
    <div class="ev-comp ev-timeline">
      <div class="ev-comp-h">${colLabel} <span class="ev-comp-h-sub">상태 타임라인</span></div>
      <div class="st-row">${dots}</div>
      ${summary}
    </div>`;
}

// ── C5-1 — Delta Arrow ──
// 학년 진급 추적: 전년 1학년 → 당해 2학년 인원 변화를 화살표로 표시 (-7~+3% 정상 밴드)
function _renderDeltaArrow(host, rule) {
  const cd = currentSchoolData.chart_data;
  const labels = cd.labels;
  const g1 = (cd.series && (cd.series['grade1_students'] || cd.series['1학년 학생수'])) || null;
  const g2 = (cd.series && (cd.series['grade2_students'] || cd.series['2학년 학생수'])) || null;
  if (!g1 || !g2) { _renderLineChart(host, rule); return; }

  // (전년 1학년, 당해 2학년) 페어 계산
  const pairs = [];
  for (let i = 1; i < labels.length; i++) {
    const yr = labels[i];
    const prev = g1.self[i - 1];
    const curr = g2.self[i];
    if (prev == null || curr == null || prev === 0) continue;
    const diff = curr - prev;
    const rate = (curr - prev) / prev * 100;
    const inBand = rate >= -7 && rate <= 3;
    pairs.push({ yr, prev, curr, diff, rate, inBand, detected: rule.years.includes(yr) });
  }

  if (!pairs.length) { host.innerHTML = '<div class="ev-no-chart">학년 진급 데이터가 충분하지 않습니다.</div>'; return; }

  const items = pairs.map(p => {
    const rateCls = p.inBand ? 'in-band' : (p.detected ? 'out-band detected' : 'out-band');
    const arrowDir = p.diff < 0 ? 'down' : p.diff > 0 ? 'up' : 'flat';
    return `<div class="da-item ${p.detected ? 'detected' : ''}">
      <div class="da-yr">${p.yr - 1}년→${p.yr}년</div>
      <div class="da-flow">
        <div class="da-side">
          <div class="da-side-lb">전년 1학년</div>
          <div class="da-side-vl">${Number(p.prev).toLocaleString()}<span class="da-unit">명</span></div>
        </div>
        <div class="da-arrow ${arrowDir}">
          <div class="da-arrow-shaft"></div>
          <div class="da-arrow-tip"></div>
          <div class="da-delta ${rateCls}">${p.diff >= 0 ? '+' : ''}${p.diff}명 (${p.rate >= 0 ? '+' : ''}${p.rate.toFixed(1)}%)</div>
        </div>
        <div class="da-side">
          <div class="da-side-lb">당해 2학년</div>
          <div class="da-side-vl">${Number(p.curr).toLocaleString()}<span class="da-unit">명</span></div>
        </div>
      </div>
    </div>`;
  }).join('');

  host.innerHTML = `
    <div class="ev-comp ev-delta">
      <div class="ev-comp-h">학년 진급 인원 변화 <span class="ev-comp-h-sub">진급 비교</span></div>
      <div class="da-band">정상 범위: <b>-7% ~ +3%</b></div>
      <div class="da-list">${items}</div>
    </div>`;
}

// ── D2-2 / D2-1 — Peer Range Dot ──
// 동료군 분포(min/max/mean/median)에서 본교 점이 어디에 위치하는지 가로 막대로
function _renderPeerRangeDot(host, rule) {
  const cd = currentSchoolData.chart_data;
  const labels = cd.labels;
  const series = _seriesForRule(rule)[0];
  if (!series) { _renderLineChart(host, rule); return; }
  const s = series.payload;
  const colLabel = series.label;

  // 연도별 막대 — 본교 점 + 동료군 범위 + 평균 마크
  const rows = labels.map((yr, i) => {
    const self = s.self[i];
    const mn = s.peer_min[i];
    const mx = s.peer_max[i];
    const avg = s.peer_mean[i];
    if (self == null || mn == null || mx == null) {
      return `<div class="pr-row pr-row-empty"><div class="pr-yr">${yr}</div><div class="pr-bar"><span class="pr-empty">데이터 없음</span></div></div>`;
    }
    const range = mx - mn;
    const pct = range > 0 ? Math.max(0, Math.min(100, ((self - mn) / range) * 100)) : 50;
    const avgPct = range > 0 && avg != null ? Math.max(0, Math.min(100, ((avg - mn) / range) * 100)) : 50;
    const detected = rule.years.includes(yr);
    const dotCls = `pr-dot ${detected ? 'detected' : ''} ${pct < 5 ? 'extreme-low' : pct > 95 ? 'extreme-high' : ''}`;
    return `<div class="pr-row ${detected ? 'detected' : ''}">
      <div class="pr-yr">${yr}</div>
      <div class="pr-bar">
        <div class="pr-track">
          <div class="pr-avg" style="left:${avgPct}%" title="동료군 평균 ${avg != null ? avg : '—'}"></div>
          <div class="${dotCls}" style="left:${pct}%" title="본교 ${self}"></div>
        </div>
        <div class="pr-vals">
          <span class="pr-min">${Number.isInteger(mn) ? Number(mn).toLocaleString() : Number(mn).toFixed(1)}</span>
          <span class="pr-self">본교 <b>${Number.isInteger(self) ? Number(self).toLocaleString() : Number(self).toFixed(1)}</b></span>
          <span class="pr-max">${Number.isInteger(mx) ? Number(mx).toLocaleString() : Number(mx).toFixed(1)}</span>
        </div>
      </div>
    </div>`;
  }).join('');

  host.innerHTML = `
    <div class="ev-comp ev-peer">
      <div class="ev-comp-h">${colLabel} <span class="ev-comp-h-sub">유사학교 분포도</span></div>
      <div class="pr-list">${rows}</div>
      <div class="pr-legend">
        <span class="pr-lg-item"><span class="pr-lg-dot avg"></span> 동료군 평균</span>
        <span class="pr-lg-item"><span class="pr-lg-dot self"></span> 본교</span>
        <span class="pr-lg-item"><span class="pr-lg-bar"></span> 동료군 범위 (min~max)</span>
      </div>
    </div>`;
}

// (구) renderEvidenceCards — 마스터-디테일 구조로 대체됨. 호환을 위해 빈 함수 유지.
let evCardData = [];
function _legacyRenderEvidenceCards_unused(cards) {
  const root = document.getElementById('evidence-cards');
  if (!cards || !cards.length) { root.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:12px">표시할 검토 신호가 없습니다.</div>'; evCardData = []; return; }
  evCardData = cards;

  // 카테고리 헤더 + 룰 카드(각 룰의 연도별 신호를 묶음) 구조
  root.innerHTML = cards.map((cat, ci) => {
    // 룰별 그룹핑 (rules는 (룰ID, 연도) 단위)
    // severity: 내부 룰 메타(star 1~3)를 그대로 사용하되, 사용자 노출은 "우선/일반/참고" 라벨로만.
    const byRule = {};
    cat.rules.forEach(r => {
      if (!byRule[r.rule_id]) byRule[r.rule_id] = { rule_id: r.rule_id, rule_name_ko: r.rule_name_ko, years: [], details: [], col_labels: r.col_labels || [], severity: 0 };
      byRule[r.rule_id].years.push(r.year);
      byRule[r.rule_id].details.push({ year: r.year, detail: r.detail });
      byRule[r.rule_id].severity = Math.max(byRule[r.rule_id].severity, r.star || 0);
    });
    const ruleList = Object.values(byRule).sort((a, b) => b.severity - a.severity);

    const sectionHeader = `
      <div class="ev-section-h">
        <span>${cat.category_ko}</span>
        <span class="evs-code">${cat.cat_code || ''}</span>
        <span class="evs-cnt">세부 룰 ${ruleList.length}개 · 총 ${cat.rules.length}건</span>
      </div>`;

    const cardsHtml = ruleList.map((r, ri) => {
      const yearsTxt = Array.from(new Set(r.years)).sort().join(', ');
      const isRepeat = new Set(r.years).size >= 3;
      // 룰 단위 색상: 내부 severity(룰 메타) 기반. 카드의 라벨은 별 표기 없이 좌측 보더 색으로만 표현.
      const gradeCls = r.severity >= 3 ? 'priority' : r.severity >= 2 ? 'normal' : 'ref';
      const detailLines = r.details.sort((a, b) => a.year - b.year).map(x => `<div><strong>${x.year}년</strong> · ${x.detail}</div>`).join('');
      const aiId = `ev-ai-${ci}-${ri}`;
      const tableHtml = (() => {
        if (!cat.data_table || !cat.data_table.length) return '';
        // 룰 관련 컬럼만 보여주기
        const colLabels = new Set(r.col_labels || []);
        const rows = cat.data_table.filter(row => colLabels.size === 0 || colLabels.has(row['지표']));
        if (!rows.length) return '';
        const yrs = Object.keys(rows[0]).filter(k => /^\d{4}$/.test(k)).sort();
        const detYears = new Set(r.years.map(Number));
        let t = `<table class="cat-table" style="margin-top:6px"><thead><tr><th>지표</th>${yrs.map(y => `<th${detYears.has(+y) ? ' style="background:var(--cobalt-bg);color:var(--cobalt)"' : ''}>${y}</th>`).join('')}<th>동료군</th></tr></thead><tbody>`;
        rows.forEach(row => {
          t += '<tr><td>' + row['지표'] + '</td>';
          yrs.forEach(y => {
            const v = row[y];
            const fmt = v == null ? '-' : (typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1)) : v);
            t += `<td${detYears.has(+y) ? ' class="cat-detected"' : ''}>${fmt}</td>`;
          });
          const p = row['동료군'];
          t += `<td class="peer">${p != null ? (Number.isInteger(p) ? p.toLocaleString() : p.toFixed(1)) : '-'}</td></tr>`;
        });
        t += '</tbody></table>';
        return t;
      })();

      return `
        <div class="ev-card ${gradeCls}">
          <div class="ev-card-head">
            <div class="ev-name">${r.rule_name_ko}<span class="ev-rid">${r.rule_id}</span></div>
            <div class="ev-meta">
              <span class="ev-year">${yearsTxt}</span>
              ${isRepeat ? '<span class="ev-repeat">반복</span>' : ''}
            </div>
          </div>
          <div class="ev-detail">${detailLines}</div>
          <div class="ev-ai" id="${aiId}">
            <div class="ev-ai-row"><span class="ev-ai-lb">해석</span><span class="ev-ai-vl" style="color:var(--text-muted);font-style:italic">AI 보조 해석을 불러오는 중…</span></div>
          </div>
          <span class="ev-fold" onclick="this.parentElement.classList.toggle('open')">원자료/시계열 펼치기</span>
          <div class="ev-fold-body">${tableHtml || '<div style="font-size:11px;color:var(--text-muted);padding:6px">관련 시계열이 없습니다.</div>'}</div>
        </div>`;
    }).join('');

    return `<div class="ev-section">${sectionHeader}${cardsHtml}</div>`;
  }).join('');

  // 카테고리별 AI 해석 비동기 로드 — 룰 카드 첫 번째에 표시 (카테고리 단위 AI를 그대로 활용)
  if (currentSchoolCode) cards.forEach((cat, ci) => {
    fetch(API + `/api/school/${currentSchoolCode}/ai/${encodeURIComponent(cat.category_ko)}`).then(r => r.json()).then(a => {
      // 각 룰 카드의 첫 ai-row에 카테고리 해석을 공통 적용. (룰별 미세 해석은 차후 ① 6박스 메모 단계에서.)
      // 모든 룰 카드에 동일한 카테고리 해석을 표시.
      const ruleCount = Object.keys(cat.rules.reduce((m, r) => (m[r.rule_id] = 1, m), {})).length;
      for (let ri = 0; ri < ruleCount; ri++) {
        const el = document.getElementById(`ev-ai-${ci}-${ri}`);
        if (!el) continue;
        el.innerHTML = `
          ${a['해석'] ? `<div class="ev-ai-row"><span class="ev-ai-lb">해석</span><span class="ev-ai-vl">${a['해석']}</span></div>` : ''}
          ${a['정상사유'] ? `<div class="ev-ai-row"><span class="ev-ai-lb">정상 사유</span><span class="ev-ai-vl">${a['정상사유']}</span></div>` : ''}
          ${a['확인권장'] ? `<div class="ev-ai-row"><span class="ev-ai-lb">확인 권장</span><span class="ev-ai-vl">${a['확인권장']}</span></div>` : ''}`;
      }
    }).catch(() => {
      // 사용자 노출 문구는 서버와 톤 통일
      const ruleCount = Object.keys(cat.rules.reduce((m, r) => (m[r.rule_id] = 1, m), {})).length;
      for (let ri = 0; ri < ruleCount; ri++) {
        const el = document.getElementById(`ev-ai-${ci}-${ri}`);
        if (el) el.innerHTML = `<div class="ev-ai-row"><span class="ev-ai-lb">해석</span><span class="ev-ai-vl" style="color:var(--text-muted)">${FALLBACK_AI_TEXT}</span></div>`;
      }
    });
  });
}

// ===== SELF REPORT (자가진단 리포트 — 학교 상세 끝) =====
//  · 백엔드 self_report 객체를 받아 카드 그리드로 렌더링
//  · "출력 미리보기" 버튼은 window.print 사용 (실제 PDF 생성 아님, 사용자 요청대로 미리보기 수준)
function renderSelfReport(rep) {
  const host = document.getElementById('self-report-section');
  if (!host) return;
  if (!rep || !rep.school_name) {
    host.innerHTML = '<div class="sr-empty">자가진단 리포트 데이터를 불러오지 못했습니다.</div>';
    return;
  }
  const total = (dashboardData && dashboardData.total_schools) || allSchools.length || 0;
  // v4 점수체계 라벨로 강제 — 서버 grade_label은 구 임계 잔존 가능성 있음(임계 단일 출처는 indexLabel).
  const idxCls = indexCls(rep.score), idxLb = indexLabel(rep.score);

  // Top 신호 행
  const topRows = (rep.top_signals || []).map((s, i) => `
    <div class="sr-top-row">
      <div class="sr-top-rank">${i + 1}</div>
      <div class="sr-top-main">
        <div class="sr-top-name">${s.rule_name_ko} <span class="sr-top-rid">${s.rule_id}</span></div>
        <div class="sr-top-meta">${s.year}년 · ${s.category_ko}</div>
        <div class="sr-top-detail">${s.detail || ''}</div>
      </div>
    </div>`).join('');

  // 카테고리 요약
  const catRows = (rep.category_summary || []).map(c => `
    <div class="sr-cat-row ${c.is_repeat ? 'repeat' : ''}">
      <span class="sr-cat-name">${c.category_ko}</span>
      <span class="sr-cat-code">${c.cat_code || ''}</span>
      <span class="sr-cat-cnt">${c.total_detections}건 / ${c.rule_count}룰${c.is_repeat ? ' · 반복' : ''}</span>
    </div>`).join('');

  // 동료군 대비
  const peerRows = (rep.peer_summary || []).map(p => {
    const sign = p.diff_pct >= 0 ? '+' : '';
    const cls = Math.abs(p.diff_pct) >= 20 ? 'sig' : Math.abs(p.diff_pct) >= 10 ? 'mid' : '';
    return `<div class="sr-peer-row ${cls}">
      <span class="sr-peer-lb">${p.label}</span>
      <span class="sr-peer-vl">본교 <b>${Number.isInteger(p.self) ? Number(p.self).toLocaleString() : p.self.toFixed(1)}</b> · 동료군 ${Number.isInteger(p.peer) ? Number(p.peer).toLocaleString() : p.peer.toFixed(1)}<span class="sr-peer-unit">${p.unit}</span></span>
      <span class="sr-peer-diff">${sign}${p.diff_pct}%</span>
    </div>`;
  }).join('');

  // 확인 권장
  const recRows = (rep.recommends || []).map((r, i) => `
    <div class="sr-rec-row">
      <span class="sr-rec-n">${i + 1}</span>
      <span class="sr-rec-name">${r.rule_name_ko} <span class="sr-top-rid">${r.rule_id}</span></span>
      <span class="sr-rec-text">${r.text}</span>
    </div>`).join('');

  host.innerHTML = `
    <div class="sr-wrap">
      <div class="sr-head">
        <div class="sr-head-info">
          <div class="sr-head-name">${rep.school_name}</div>
          <div class="sr-head-meta">${rep.district || ''}구 · ${rep.school_type || ''} · ${rep.year_range || ''}년 공시 · 자가진단 리포트</div>
        </div>
        <div class="sr-head-actions">
          <button class="sr-print-btn" onclick="printSelfReport()">출력 미리보기</button>
        </div>
      </div>
      <div class="sr-summary-grid">
        <div class="sr-sum-card sr-lead">
          <div class="sr-sum-lb">검토 우선도 지수</div>
          <div class="sr-sum-vl">${fmtIndex(rep.score)}</div>
          <div class="sr-sum-sub"><span class="grade-badge ${idxCls}">${idxLb}</span> ${rep.rank}위 / ${total}교</div>
        </div>
        <div class="sr-sum-card">
          <div class="sr-sum-lb">검토 신호 수</div>
          <div class="sr-sum-vl">${rep.num_detections}<small>건</small></div>
          <div class="sr-sum-sub">${rep.num_rules}개 세부 룰</div>
        </div>
        <div class="sr-sum-card">
          <div class="sr-sum-lb">관련 카테고리</div>
          <div class="sr-sum-vl">${rep.num_categories}<small>개</small></div>
          <div class="sr-sum-sub">${rep.is_repeat ? '3년 반복 신호' : '복수·단년 신호'}</div>
        </div>
      </div>
      ${topRows ? `<div class="sr-section-h">주요 검토 신호 Top ${(rep.top_signals||[]).length}</div><div class="sr-top-list">${topRows}</div>` : ''}
      ${catRows ? `<div class="sr-section-h">카테고리 요약</div><div class="sr-cat-list">${catRows}</div>` : ''}
      ${peerRows ? `<div class="sr-section-h">동료군 대비 요약 (최근 연도)</div><div class="sr-peer-list">${peerRows}</div>` : ''}
      ${recRows ? `<div class="sr-section-h">확인 권장 사항</div><div class="sr-rec-list">${recRows}</div>` : ''}
      <div class="sr-foot">
        본 리포트는 본 도구가 추출한 검토 후보를 종합 요약한 것이며, 학교 평가가 아닙니다. 최종 판단·조치는 담당자가 수행합니다.<br>
        ${_sampleNoteText((dashboardData && dashboardData.data_basis) || {})}
      </div>
    </div>`;
}

function printSelfReport() {
  // 출력 미리보기 — window.print (실제 PDF 생성 아님, 미리보기 수준)
  showNotify('인쇄 미리보기를 엽니다');
  setTimeout(() => window.print(), 200);
}

// ===== DATA TABLE =====
function renderDataTable(table, targetId) {
  const el = document.getElementById(targetId || 'full-data-table');
  if (!el || !table || !table.length) { if (el) el.innerHTML = '<p style="padding:12px;color:var(--text-muted);font-size:12px">데이터 없음</p>'; return; }
  const yrs = Object.keys(table[0]).filter(k => /^\d{4}$/.test(k)).sort();
  let html = '<table class="data-table"><thead><tr><th>지표</th>' + yrs.map(y => `<th>${y}</th>`).join('') + '<th>동료군</th></tr></thead><tbody>';
  table.forEach(row => {
    html += '<tr><td>' + row['지표'] + '</td>';
    yrs.forEach(y => {
      const c = row[y]; if (!c || c.value == null) { html += '<td>-</td>'; return; }
      const cls = c.status === 'detected' ? 'cell-detected' : '';
      const v = typeof c.value === 'number' ? (Number.isInteger(c.value) ? c.value.toLocaleString() : c.value.toFixed(1)) : c.value;
      html += `<td class="${cls}">${v}</td>`;
    });
    const p = row['동료군평균'];
    html += `<td class="cell-peer">${p != null ? (Number.isInteger(p) ? p.toLocaleString() : p.toFixed(1)) : '-'}</td></tr>`;
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

// ===== CHARTS (레거시 — chart-main/chart-bullying 캔버스 미사용. 호환 위해 함수만 유지.) =====
const valLabelPlugin = { id: 'valLabel', afterDatasetsDraw(chart) {
  const ctx = chart.ctx; ctx.save();
  chart.data.datasets.forEach((ds, di) => {
    if (ds.borderDash) return;
    const meta = chart.getDatasetMeta(di);
    meta.data.forEach((pt, i) => {
      const val = ds.data[i]; if (val == null) return;
      ctx.fillStyle = ds.borderColor || '#333';
      ctx.font = 'bold 10px Pretendard Variable'; ctx.textAlign = 'center';
      ctx.fillText(Number.isInteger(val) ? val : val.toFixed(1), pt.x, pt.y - 8);
    });
  }); ctx.restore();
}};

function renderCharts(cd) {
  // 레거시: chart-main/chart-bullying 캔버스가 사라졌으므로 안전 가드.
  if (!cd || !cd.labels) return;
  if (!document.getElementById('chart-main')) return;
  if (chartMain) chartMain.destroy(); if (chartBully) chartBully.destroy();
  const labels = cd.labels, ff = 'Pretendard Variable';
  const opts = { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { font: { size: 11, family: ff }, usePointStyle: true, padding: 10 } } }, scales: { y: { beginAtZero: false, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } }, x: { grid: { display: false }, ticks: { font: { size: 11 } } } } };
  chartMain = new Chart(document.getElementById('chart-main'), { type: 'line', plugins: [valLabelPlugin], data: { labels, datasets: [
    { label: '학생수', data: cd['학생수'], borderColor: '#1D4ED8', backgroundColor: 'rgba(29,78,216,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 3, fill: true },
    { label: '교원수', data: cd['교원수'], borderColor: '#7C3AED', backgroundColor: 'rgba(124,58,237,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 3, fill: true },
    { label: '동료군 학생수', data: cd['동료군_학생수'], borderColor: '#1D4ED8', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5 },
    { label: '동료군 교원수', data: cd['동료군_교원수'], borderColor: '#7C3AED', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5 },
  ]}, options: { ...opts, plugins: { ...opts.plugins, title: { display: true, text: '학생수 · 교원수 (본교 실선 / 동료군 점선)', font: { size: 12, weight: 'bold', family: ff }}}}});
  chartBully = new Chart(document.getElementById('chart-bullying'), { type: 'line', plugins: [valLabelPlugin], data: { labels, datasets: [
    { label: '학폭 건수', data: cd['학폭건수'], borderColor: '#DC2626', backgroundColor: 'rgba(220,38,38,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 3, fill: true },
    { label: '피해학생', data: cd['피해학생수'], borderColor: '#F59E0B', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 3 },
    { label: '보호조치', data: cd['보호조치건수'], borderColor: '#16A34A', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 3 },
  ]}, options: { ...opts, scales: { ...opts.scales, y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 }}}}, plugins: { ...opts.plugins, title: { display: true, text: '학폭 · 보호조치', font: { size: 12, weight: 'bold', family: ff }}}}});
}

// (고급 탐색 기능은 nav에서 제거됨. /api/custom-analysis 백엔드는 추후 정리 위해 보존.)

// ── G1-1 — Drift Trend ──
// 본교 3년 시계열 + 추세선(같은 axis) + 우측에 누적 변화·기울기·R²·방향 메타 카드
function _renderDriftTrend(host, rule) {
  const cd = currentSchoolData.chart_data;
  const labels = cd.labels;
  const series = _seriesForRule(rule)[0];
  if (!series) { _renderLineChart(host, rule); return; }
  const vals = series.payload.self || [];
  const colLabel = series.label;

  // 룰 detection의 메타에서 추출 시도 — details에 R²·기울기 문자열이 들어 있지만 안정성 위해 직접 재계산
  let v0 = null, v1 = null, v2 = null;
  if (vals.length >= 3) { v0 = vals[vals.length - 3]; v1 = vals[vals.length - 2]; v2 = vals[vals.length - 1]; }
  let cumPct = null, slope = null, r2 = null, direction = '—';
  if (v0 != null && v1 != null && v2 != null && v0 !== 0) {
    cumPct = (v2 - v0) / v0 * 100;
    direction = cumPct < 0 ? '감소' : (cumPct > 0 ? '증가' : '변화 없음');
    const xs = [0, 1, 2], ys = [v0, v1, v2];
    const meanX = 1, meanY = (v0 + v1 + v2) / 3;
    let ssXY = 0, ssXX = 0;
    for (let i = 0; i < 3; i++) { ssXY += (xs[i] - meanX) * (ys[i] - meanY); ssXX += (xs[i] - meanX) ** 2; }
    slope = ssXX ? ssXY / ssXX : 0;
    const intercept = meanY - slope * meanX;
    let ssTot = 0, ssRes = 0;
    for (let i = 0; i < 3; i++) { ssTot += (ys[i] - meanY) ** 2; ssRes += (ys[i] - (slope * xs[i] + intercept)) ** 2; }
    r2 = ssTot ? Math.max(0, 1 - ssRes / ssTot) : 1;
  }

  // host 안에 canvas + meta 동시 배치
  // PDF 피드백 5: 3개 카드 좌측 컬러 라인 통일 (방향에 따라 down/up/neutral 모두 표시)
  // PDF 피드백 6: R² → "추세 적합도", "강한 단조" → "급격한 추세 변동"
  const fitLabel = r2 != null && r2 >= 0.9 ? '높음 (급격한 추세 변동)' : (r2 != null && r2 >= 0.5 ? '보통' : '낮음');
  const dirCls = direction === '감소' ? 'down' : direction === '증가' ? 'up' : 'neutral';
  host.innerHTML = `
    <div class="ev-comp ev-drift">
      <div class="ev-comp-h">${colLabel} <span class="ev-comp-h-sub">다년 추세</span></div>
      <div class="dr-grid">
        <div class="dr-chart-wrap"><canvas id="md-evidence-chart"></canvas></div>
        <div class="dr-stats">
          <div class="dr-stat ${dirCls}">
            <div class="dr-stat-lb">3년 누적 변화</div>
            <div class="dr-stat-vl">${cumPct != null ? (cumPct >= 0 ? '+' : '') + cumPct.toFixed(1) + '%' : '—'}</div>
            <div class="dr-stat-sub">${direction}</div>
          </div>
          <div class="dr-stat ${dirCls}">
            <div class="dr-stat-lb">연 평균 기울기</div>
            <div class="dr-stat-vl">${slope != null ? (slope >= 0 ? '+' : '') + slope.toFixed(2) : '—'}</div>
            <div class="dr-stat-sub">/년</div>
          </div>
          <div class="dr-stat ${r2 != null && r2 >= 0.9 ? 'strong' : 'neutral'}">
            <div class="dr-stat-lb">추세 적합도</div>
            <div class="dr-stat-vl">${r2 != null ? r2.toFixed(2) : '—'}</div>
            <div class="dr-stat-sub">${fitLabel}</div>
          </div>
        </div>
      </div>
    </div>`;

  // 본교 라인 + 회귀선 (Chart.js)
  const cvs = document.getElementById('md-evidence-chart');
  if (!cvs) return;
  if (mdChart) { mdChart.destroy(); mdChart = null; }
  const trendLine = (slope != null) ? labels.map((_, i) => {
    const intercept = ((v0 + v1 + v2) / 3) - slope * 1;   // mean_x=1
    return slope * i + intercept;
  }) : labels.map(() => null);

  mdChart = new Chart(cvs, {
    type: 'line',
    data: {
      labels,
      datasets: [
        // 본교 라인: 코발트(파랑), 굵게 + 채움. 추세선: 강조색(빨강), 점선, 굵기 ↑.
        // PDF 피드백 4: 본교 라인과 추세선이 거의 겹칠 때도 식별되게 색/굵기 대비 강화.
        { label: `${colLabel} (본교)`, data: vals, borderColor: '#1D4ED8', backgroundColor: 'rgba(29,78,216,0.10)', borderWidth: 3.5, pointRadius: 6, pointHoverRadius: 9, pointBackgroundColor: '#1D4ED8', pointBorderColor: '#fff', pointBorderWidth: 2, tension: 0, fill: true, order: 1 },
        { label: '단조 추세선', data: trendLine, borderColor: '#DC2626', borderDash: [8, 5], borderWidth: 2.5, pointRadius: 0, fill: false, order: 2 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 12, family: 'Pretendard Variable', weight: '600' }, usePointStyle: true, padding: 12 } },
        tooltip: { titleFont: { size: 13, weight: '700' }, bodyFont: { size: 12.5 }, padding: 10, callbacks: { title: items => `${items[0].label}년` } },
      },
      scales: {
        y: { beginAtZero: false, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 13 } } },
        x: { grid: { display: false }, ticks: { font: { size: 13, weight: '700' } } },
      },
    },
  });
}

function showNotify(msg) {
  const n = document.createElement('div'); n.className = 'extend-notify'; n.textContent = msg;
  document.body.appendChild(n);
  setTimeout(() => { n.classList.add('hide'); setTimeout(() => n.remove(), 300); }, 2500);
}

// ===== FLOATING CHATBOT =====
function bindChat() {
  const fab = document.getElementById('chat-fab');
  const panel = document.getElementById('chat-panel');
  const close = document.getElementById('chat-close');
  fab.onclick = () => {
    panel.classList.add('open'); panel.setAttribute('aria-hidden', 'false'); fab.classList.add('hidden');
    updateChatContext();
  };
  close.onclick = () => {
    panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true'); fab.classList.remove('hidden');
  };
  document.getElementById('chat-send').onclick = () => sendChat();
  // IME(한글) 조합 중 Enter는 무시 — 조합 종료 후 보낼 때만 sendChat 호출
  const chatInput = document.getElementById('chat-input');
  let _chatComposing = false;
  chatInput.addEventListener('compositionstart', () => { _chatComposing = true; });
  chatInput.addEventListener('compositionend', () => { _chatComposing = false; });
  chatInput.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    // 1) compositionstart/end 추적값으로 1차 차단
    // 2) Safari 등에서 isComposing이 정확한 경우의 보조 가드
    // 3) keyCode 229는 IME가 키 입력을 가로채는 경우의 보수적 가드
    if (_chatComposing || e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    sendChat();
  });
}

function updateChatContext() {
  const title = document.getElementById('chat-panel-title');
  const sub = document.getElementById('chat-panel-sub');
  const chips = document.getElementById('chat-chips');
  const intro = document.getElementById('chat-messages');
  if (chatContext === 'school' && currentSchoolCode) {
    const sname = allSchools.find(s => s.school_code === currentSchoolCode);
    title.textContent = `${sname ? sname.school_name : '이 학교'} · 후속 질문`;
    sub.textContent = '해당 학교 안에서 자연어로 추가 확인 질문을 보낼 수 있습니다. LLM은 판정하지 않고 확인을 돕습니다.';
    chips.innerHTML = ['이 학교 1분 브리핑 생성', '정상 예외 가능성을 더 설명해줘', '동료군 비교를 요약해줘', '리포트 문장으로 정리해줘']
      .map(t => `<button class="chip" onclick="sendChat('${t}')">${t}</button>`).join('');
    if (intro.dataset.schoolFor !== currentSchoolCode) {
      intro.innerHTML = `<div class="chat-msg system"><div class="msg-body">${sname ? sname.school_name : '이 학교'}에 대한 후속 질문을 보낼 수 있습니다.</div></div>`;
      intro.dataset.schoolFor = currentSchoolCode;
    }
  } else {
    title.textContent = '공시 데이터에 질문하기';
    sub.textContent = '전체 표본·룰·학교에 대해 자연어로 물어볼 수 있습니다. LLM은 판정하지 않고 확인을 돕습니다.';
    chips.innerHTML = ['강남구 검토 우선도 1위 학교는?', '우선 검토 신호만 요약해줘', '학생수가 급감한 학교는?', '학교폭력 조치 확인 신호가 있는 학교는?']
      .map(t => `<button class="chip" onclick="sendChat('${t}')">${t}</button>`).join('');
    if (intro.dataset.schoolFor !== '') {
      intro.innerHTML = `<div class="chat-msg system"><div class="msg-body">전체 공시 데이터에 자연어로 질문해보세요.</div></div>`;
      intro.dataset.schoolFor = '';
    }
  }
}

let _midSeq = 0;
function addMsg(t, h) { const id = 'm' + (++_midSeq); const d = document.createElement('div'); d.className = `chat-msg ${t}`; d.id = id; d.innerHTML = `<div class="msg-body">${h}</div>`; document.getElementById('chat-messages').appendChild(d); d.scrollIntoView({ behavior: 'smooth', block: 'end' }); return id; }
function removeMsg(id) { const el = document.getElementById(id); if (el) el.remove(); }

// §9: 챗봇 결과 카드 — 긴 헤더는 축약, 학교명·지수 강조
const _CHAT_HEADER_SHORT = {
  '검토 우선도 지수': '지수',
  '관련 카테고리 수': '카테고리',
  '대표 검토 신호': '대표 신호',
  '검토 신호 수': '신호',
  '지역구': '구',
};
function _chatHeaderShort(k) { return _CHAT_HEADER_SHORT[k] || k; }

function _chatMiniCard(row) {
  // 학교명·지수 우선 추출, 나머지는 row 리스트
  const nameKey = ['학교명', 'school_name', '학교'].find(k => k in row);
  const idxKey = ['검토 우선도 지수', 'score', '지수'].find(k => k in row);
  const name = nameKey ? row[nameKey] : '';
  const idx = idxKey ? row[idxKey] : '';
  const skipKeys = new Set([nameKey, idxKey].filter(Boolean));
  const rows = Object.entries(row).filter(([k]) => !skipKeys.has(k))
    .map(([k, v]) => `<div class="cmc-row"><span class="cmc-row-l">${_chatHeaderShort(k)}</span><span class="cmc-row-v">${v == null ? '-' : v}</span></div>`)
    .join('');
  return `<div class="chat-mini-card">
    <div class="cmc-head"><span class="cmc-name">${name || '결과'}</span>${idx !== '' ? `<span class="cmc-idx">지수 ${idx}</span>` : ''}</div>
    ${rows}
  </div>`;
}

let _chatPending = false;
async function sendChat(text) {
  // pending 중 추가 호출 무시 — Enter 연타·버튼 연속 클릭·예제 칩 중복 클릭 모두 가드
  if (_chatPending) return;
  const inputEl = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');
  // 입력 정규화 — 전송 전 trim. text 인자도 안전하게 string 변환.
  const raw = (text != null ? String(text) : (inputEl ? inputEl.value : ''));
  const query = raw.replace(/​/g, '').trim();
  if (!query) {
    // 빈 입력은 서버로 안 보내고 프론트에서 짧게 안내. 사용자 메시지(빈 말풍선)도 표시 안 함.
    addMsg('system', '<span style="color:var(--text-muted)">질문을 입력해 주세요.</span>');
    return;
  }
  _chatPending = true;
  if (inputEl) inputEl.value = '';
  if (sendBtn) sendBtn.disabled = true;
  addMsg('user', query);
  const lid = addMsg('system', '<span style="color:var(--text-muted)">분석 중…</span>');
  try {
    const data = await (await fetch(API + '/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, school_code: chatContext === 'school' && currentSchoolCode ? currentSchoolCode : '', conversation_id: currentSchoolCode || 'default', history: chatHistory.slice(-3) }) })).json();
    removeMsg(lid);
    let tableHtml = '';
    if (data.result_data && data.result_data.length) {
      const cols = Object.keys(data.result_data[0]).filter(k => !k.startsWith('_'));
      const prev = {};
      tableHtml = '<div class="chat-result-table"><table><thead><tr>' + cols.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>';
      data.result_data.slice(0, 8).forEach(row => {
        tableHtml += '<tr>' + cols.map(c => {
          let v = row[c], cls = '';
          if (typeof v === 'number') {
            if (prev[c] != null && prev[c] !== 0) {
              const chg = Math.abs((v - prev[c]) / prev[c] * 100);
              if (chg >= 10) cls = ' class="cat-detected"';
            }
            prev[c] = v;
            v = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
          }
          return `<td${cls}>${v == null ? '-' : v}</td>`;
        }).join('') + '</tr>';
      });
      tableHtml += '</tbody></table></div>';
    }
    const report = data.report || '', conf = data.confidence || '중간';
    const cc = conf === '높음' ? 'conf-high' : conf === '중간' ? 'conf-mid' : 'conf-low';

    // §9: 1~3건이면 카드형, 4건+면 표 (접기로 제공)
    let primary = '';
    const rd = data.result_data || [];
    if (rd.length > 0 && rd.length <= 3) {
      primary = `<div class="chat-cards">${rd.map(row => _chatMiniCard(row)).join('')}</div>`;
      if (tableHtml) primary += `<span class="chat-table-toggle" onclick="this.classList.toggle('open');const t=this.nextElementSibling;if(t)t.style.display=t.style.display==='none'?'':'none'">표 보기</span><div style="display:none;margin-top:4px">${tableHtml}</div>`;
    } else if (tableHtml) {
      primary = tableHtml;
    }

    let html = `<div class="chat-card">`;
    if (primary) html += `<div class="chat-card-header">분석 결과<span class="chat-confidence ${cc}" style="margin:0">${conf}</span></div><div class="chat-card-body">${primary}</div>`;
    // 학교+룰 컨텍스트가 명확하면 6박스 첨부 (서버가 sixbox 동봉)
    if (data.sixbox) {
      html += `<div class="chat-card-body" style="border-top:1px solid var(--border-light);padding-top:6px">${_renderSixBox(data.sixbox)}</div>`;
    }
    if (report) {
      let parsed = marked.parse(report);
      parsed = parsed.replace(/(\d+\.?\d*%[p]?)/g, '<span class="hl">$1</span>');
      parsed = parsed.replace(/(감소|증가|급변|급증|급감|0건)/g, '<span class="hl-danger">$1</span>');
      html += `<div class="chat-card-body" style="border-top:1px solid var(--border-light);font-size:12px;line-height:1.7">${parsed}</div>`;
    }
    html += `</div>`;
    html += `<div style="margin-top:4px"><span class="chat-confidence ${cc}">신뢰도: ${conf}</span></div>`;
    addMsg('system', html);
    if (data.follow_up_suggestions) document.getElementById('chat-chips').innerHTML = data.follow_up_suggestions.map(s => `<button class="chip" onclick="sendChat('${s.replace(/'/g, "\\'")}')">${s}</button>`).join('');
    chatHistory.push({ query, summary: tableHtml ? '데이터 응답' : '' });
  } catch (e) {
    removeMsg(lid);
    console.warn('[chat] 응답 실패', e);
    addMsg('system', `<span style="color:var(--text-sub)">${FALLBACK_AI_TEXT}</span>`);
  } finally {
    _chatPending = false;
    const sendBtn2 = document.getElementById('chat-send');
    if (sendBtn2) sendBtn2.disabled = false;
  }
}

// ===== ADVANCED PREVIEW (제안 2 LLM 자동 규칙 생성기 + 제안 4 이상 전파 추적) =====
// ※ 정적 예시 화면. 모든 학교명·수치는 룰셋 정의서/시나리오 카드의 예시.
//   실제 공시 데이터 분석 결과가 아님. 라벨로 명시.

// ===== RULE LAB (샌드박스 룰 생성기 — 3단 레이아웃) =====
function setRuleLabQuery(text) {
  document.getElementById('rulelab-input').value = text;
  sendRuleLabMsg();
}

async function sendRuleLabMsg() {
  const input = document.getElementById('rulelab-input');
  const query = input.value.trim();
  if (!query) return;

  // 로딩 상태
  document.getElementById('rulelab-empty').style.display = 'none';
  document.getElementById('rulelab-dashboard').style.display = 'block';
  document.getElementById('rl-interpret').innerHTML = '<span style="color:var(--text-muted)">AI가 조건을 해석하고 있습니다...</span>';
  document.getElementById('rl-summary').innerHTML = '';
  document.getElementById('rl-condition').innerHTML = '';
  document.getElementById('rl-overlap').innerHTML = '';
  document.getElementById('rl-code').textContent = '';
  document.getElementById('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">분석 중...</div>';
  document.getElementById('rl-indicators').innerHTML = '';
  document.getElementById('rl-columns').innerHTML = '';
  document.getElementById('rl-count').textContent = '';
  document.getElementById('rl-stats').innerHTML = '';

  try {
    const res = await fetch('/api/rulelab', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    });
    const data = await res.json();

    if (data.error) {
      document.getElementById('rl-interpret').innerHTML = `<span style="color:#991B1B">${data.error}</span>`;
      return;
    }

    // 좌측: 해석
    document.getElementById('rl-interpret').innerHTML = data.interpretation || '해석 없음';

    // 좌측: 사용 지표
    const cols = data.columns_used || [];
    document.getElementById('rl-indicators').innerHTML = cols.map(c =>
      `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;color:var(--navy);font-weight:600"><input type="checkbox" checked disabled style="accent-color:var(--cobalt)"> ${c}</div>`
    ).join('');

    // 좌측: 참고 컬럼
    document.getElementById('rl-columns').innerHTML = cols.map(c =>
      `<span style="font-family:var(--mono);font-size:10px;padding:2px 7px;border-radius:3px;background:var(--bg);border:1px solid var(--border-light);color:var(--text-sub)">${c}</span>`
    ).join('');

    // 가운데: 요약 카드
    const totalCount = data.results ? data.results.length : 0;
    document.getElementById('rl-summary').innerHTML = `
      <div style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:18px 16px;text-align:center;border-top:3px solid var(--cobalt)">
        <div style="font-size:32px;font-weight:900;color:var(--cobalt)">${totalCount}</div>
        <div style="font-size:11px;font-weight:700;color:var(--text-sub);margin-top:6px">탐지 학교</div>
      </div>
      <div style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:18px 16px;text-align:center;border-top:3px solid #6366F1">
        <div style="font-size:32px;font-weight:900;color:#6366F1">${cols.length}</div>
        <div style="font-size:11px;font-weight:700;color:var(--text-sub);margin-top:6px">사용 지표</div>
      </div>
      <div style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:18px 16px;text-align:center;border-top:3px solid #059669">
        <div style="font-size:32px;font-weight:900;color:#059669">210</div>
        <div style="font-size:11px;font-weight:700;color:var(--text-sub);margin-top:6px">분석 학교</div>
      </div>`;

    // 가운데: 조건 시각화
    document.getElementById('rl-condition').innerHTML = `
      <div style="font-size:13px;font-weight:800;color:var(--navy);margin-bottom:14px;display:flex;align-items:center;gap:8px">
        <span style="background:linear-gradient(135deg,#7B5EA0,#5B8BA0);color:#fff;padding:3px 10px;border-radius:10px;font-size:9.5px;font-weight:800">AI</span> 생성된 검증 조건
      </div>
      <div style="background:var(--cobalt-bg);border-radius:6px;padding:14px 18px;font-size:13px;font-weight:700;color:var(--cobalt);line-height:1.6">${query}</div>`;

    // 가운데: 코드
    document.getElementById('rl-code').textContent = data.code || '# 코드 없음';

    // 우측: 탐지 결과
    document.getElementById('rl-count').textContent = `${totalCount}교`;
    if (data.results && data.results.length > 0) {
      document.getElementById('rl-results').innerHTML = data.results.map((r, i) =>
        `<div style="padding:10px 16px;border-bottom:1px solid var(--border-light);display:flex;align-items:center;gap:10px">
          <span style="font-size:12px;font-weight:800;color:var(--cobalt);min-width:24px">${i+1}</span>
          <div style="flex:1">
            <div style="font-size:13px;font-weight:700;color:var(--text)">${r.school || ''}</div>
            <div style="font-size:11px;color:var(--text-sub)">${r.year || ''} · ${r.detail || ''}</div>
          </div>
        </div>`
      ).join('');
    } else {
      document.getElementById('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">조건에 해당하는 학교가 없습니다.</div>';
    }

    // 우측: 통계
    document.getElementById('rl-stats').innerHTML = data.message || '';

  } catch (e) {
    document.getElementById('rl-interpret').innerHTML = `<span style="color:#991B1B">응답을 불러오지 못했습니다: ${e.message}</span>`;
  }
}
