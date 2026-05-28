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

// ── 검토 우선도 지수 임계치 (라벨용) ──
const INDEX_THRESHOLD = { PRIORITY: 16, NORMAL: 11 };
function indexLabel(score) {
  const s = Number(score) || 0;
  if (s >= INDEX_THRESHOLD.PRIORITY) return '우선 검토';
  if (s >= INDEX_THRESHOLD.NORMAL) return '일반 검토';
  return '참고';
}
function indexCls(score) {
  const s = Number(score) || 0;
  if (s >= INDEX_THRESHOLD.PRIORITY) return 'grade-priority';
  if (s >= INDEX_THRESHOLD.NORMAL) return 'grade-normal';
  return 'grade-ref';
}
function fmtIndex(score) {
  if (score == null || isNaN(Number(score))) return '—';
  return Number(score).toFixed(1);
}

// ── 활성 필터 상태 ──
const activeFilters = {
  bin: new Set(),        // 지수 구간: 'priority' / 'normal' / 'ref'
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
  document.querySelector('[data-view="explorer"]').onclick = (e) => { e.preventDefault(); showView('explorer'); initExplorer(); };
  const ns = document.getElementById('nav-school');
  ns.onclick = (e) => { e.preventDefault(); if (currentSchoolCode) showView('school'); };
  document.querySelector('[data-view="advanced"]').onclick = (e) => { e.preventDefault(); showView('advanced'); initAdvanced(); };
  document.getElementById('back-to-national').onclick = (e) => { e.preventDefault(); showView('national'); };
  document.getElementById('back-to-dashboard').onclick = (e) => { e.preventDefault(); showView('dashboard'); loadDashboard(); };
  document.getElementById('nav-search').addEventListener('input', () => { applyFilterAndRender(); });
  const advBack = document.getElementById('adv-back');
  if (advBack) advBack.onclick = (e) => { e.preventDefault(); showView('dashboard'); loadDashboard(); };
  const linkExp = document.getElementById('link-explorer');
  if (linkExp) linkExp.onclick = (e) => { e.preventDefault(); showView('explorer'); initExplorer(); };
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
function renderNational(dash) {
  const grid = document.getElementById('region-grid');
  if (!grid) return;

  const top1 = dash && dash.top3 && dash.top3[0];
  const totalDetections = dash ? dash.total_detections : null;
  const totalSchools = dash ? dash.total_schools : null;
  const db = dash ? (dash.data_basis || {}) : {};

  grid.innerHTML = REGIONS.map(r => {
    if (r.active) {
      return `
        <div class="region-card active" onclick="goToRegion('${r.code}')">
          <div class="region-card-inner">
            <div class="region-name">${r.name}</div>
            <div class="region-districts">노원·강남·관악구 고등학교</div>
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
  document.getElementById('sample-note').innerHTML =
    `<b>프로토타입 표본 N=${db.schools || 42}</b> · ${(db.districts || []).join('·')} 일반고 · ${db.year_range || ''}년 공시 · 확장 시 전국 11,000+`;

  renderTop3(dash.top3 || []);
  renderFilterPanel(dash);
  renderDistBars(dash.distribution || {});
  renderCatDist(dash.category_distribution || []);
  bindSort();
  applyFilterAndRender();
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

  // 룰 accordion 구조 — 카테고리별 묶음
  const ruleByCat = {};
  ruleDist.forEach(r => {
    if (!ruleByCat[r.category_code]) ruleByCat[r.category_code] = { ko: r.category_ko, rules: [], total: 0 };
    ruleByCat[r.category_code].rules.push(r);
    ruleByCat[r.category_code].total += r.count;
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

    <!-- 검토 우선도 구간 -->
    <div class="filter-section">
      <div class="filter-section-h">검토 우선도</div>
      <div class="fpc">
        <span class="fp-chip" data-filter="bin" data-val="priority">우선 검토 <span class="fp-chip-cnt">(지수 16+)</span></span>
        <span class="fp-chip" data-filter="bin" data-val="normal">일반 검토 <span class="fp-chip-cnt">(11~15)</span></span>
        <span class="fp-chip" data-filter="bin" data-val="ref">참고 <span class="fp-chip-cnt">(0~10)</span></span>
      </div>
    </div>

    <!-- 룰/카테고리 accordion -->
    <div class="filter-section">
      <div class="filter-section-h">룰 / 카테고리 <span class="fsh-cnt">${ruleDist.reduce((a, r) => a + r.count, 0)}건</span></div>
      <div id="rule-acc-wrap">
        ${catOrder.filter(c => ruleByCat[c]).map(c => {
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
                ${grp.rules.map(r => `
                  <div class="rule-item" data-rule="${r.rule_id}" onclick="toggleRuleFilter('${r.rule_id}')">
                    <span class="rule-item-id">${r.rule_id}</span>
                    <span class="rule-item-name" title="${r.rule_name_ko}">${r.rule_name_ko}</span>
                    <span class="rule-item-cnt">${r.count}</span>
                  </div>`).join('')}
              </div>
            </div>`;
        }).join('')}
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
      const sc = Number(s.score) || 0;
      const bin = sc >= INDEX_THRESHOLD.PRIORITY ? 'priority' : sc >= INDEX_THRESHOLD.NORMAL ? 'normal' : 'ref';
      if (!f.bin.has(bin)) return false;
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
    f.bin.forEach(v => chips.push({ f: 'bin', v, label: v === 'priority' ? '우선 검토' : v === 'normal' ? '일반 검토' : '참고' }));
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

// ===== DISTRIBUTION (우측 패널) =====
function renderDistBars(dist) {
  const order = [
    ['21-25', '우선 21+', 'priority'],
    ['16-20', '우선 16~20', 'priority'],
    ['11-15', '일반 11~15', 'normal'],
    ['6-10', '참고 6~10', 'normal'],
    ['0', '0~5', 'normal'],
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
    ? `<b>${schools.length}</b>교 표시 / 전체 ${total}교`
    : `<b>${schools.length}</b>교`;
  document.getElementById('list-count').innerHTML = countText;
  const side = document.getElementById('list-count-side');
  if (side) side.textContent = `정렬: ${sortLabel(currentSort)}`;

  if (!schools.length) {
    tbl.innerHTML = `<tbody><tr><td style="padding:36px;text-align:center;color:var(--text-muted)">조건에 맞는 학교가 없습니다. 좌측 필터를 조정해 보세요.</td></tr></tbody>`;
    return;
  }

  tbl.innerHTML = `
    <thead><tr><th>순위</th><th>학교</th><th>구·유형</th><th>탐지 카테고리</th><th style="text-align:right">검토 우선도 지수</th></tr></thead>
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
    <td class="school-cell">${s.school_name}</td>
    <td class="dist-cell">${s.district || ''} · ${s.school_type || ''}</td>
    <td class="cats-cell">${cats}</td>
    <td class="score-cell" style="text-align:right">
      <span class="grade-badge ${gc}" style="margin-right:6px">${gl}</span>
      <span class="idx-pill">${fmtIndex(s.score)}</span>
    </td>
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

function renderSchool(d) {
  const total = (dashboardData && dashboardData.total_schools) || allSchools.length || 42;
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

  // AI 보조 요약 — 라벨 명시
  const aiWrap = document.getElementById('ai-summary-wrap');
  const aiSum = document.getElementById('ai-summary');
  if (d.llm_explanation && !d.llm_explanation.startsWith('(')) {
    let txt = d.llm_explanation.replace(/([②③])/g, '\n$1');
    aiSum.innerHTML = txt.split('\n').map(l => l.trim()).filter(Boolean).map(l => `<div style="margin-bottom:3px">${l}</div>`).join('');
    aiWrap.style.display = '';
  } else {
    aiWrap.style.display = 'none';
  }

  // 상단 요약 카드 — 5박스 (지수 / 신호 수 / 세부 룰 수 / 반복 / 주요 카테고리)
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

  // 차트 캡션 — 왜 잡혔는지 한 줄
  let cap = '본 화면의 추이는 본교 실선 · 동료군(같은 구) 점선으로 표시됩니다.';
  if (cats.length) cap += ` 주요 확인 영역: ${cats.join(' · ')}.`;
  document.getElementById('chart-caption').textContent = cap;

  renderEvidenceCards(d.detection_cards);
  renderCharts(d.chart_data);
  renderDataTable(d.data_table, 'full-data-table');
  updateChatContext();
}

// ===== EVIDENCE CARDS (룰 단위) =====
let evCardData = [];
function renderEvidenceCards(cards) {
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

// ===== CHARTS =====
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
  if (!cd || !cd.labels) return;
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

// ===== EXPLORER (고급 탐색 — 학교 상세의 커스텀 분석 이식) =====
const ALL_COLUMNS = [
  { key: 'student_count', label: '학생수' }, { key: 'class_count', label: '학급수' }, { key: 'teacher_count', label: '교원수' },
  { key: 'students_per_class', label: '학급당학생수' }, { key: 'students_per_teacher', label: '교원1인당학생수' },
  { key: 'bullying_cases', label: '학폭건수' }, { key: 'bullying_victims', label: '피해학생수' }, { key: 'bullying_protection', label: '보호조치' }, { key: 'bullying_perpetrators', label: '가해학생수' },
  { key: 'graduation_rate', label: '진학률(%)' }, { key: 'meal_cost_total', label: '급식비총액' }, { key: 'meal_cost_per_student', label: '1인당급식비' },
];
const EXP_DISTRICTS = ['전체', '노원', '강남', '관악'];
const expState = { cols: new Set(['student_count', 'teacher_count']), district: '전체', schoolCode: '' };
let expInitDone = false;

function initExplorer() {
  if (expInitDone) return;
  const root = document.getElementById('explorer-config');
  root.innerHTML = `
    <div class="exp-section">
      <div class="exp-section-h">분석 범위 · 구</div>
      <div class="fpc" id="exp-district">
        ${EXP_DISTRICTS.map(d => `<span class="fp-chip ${expState.district === d ? 'active' : ''}" data-district="${d}">${d === '전체' ? '전체' : d + '구'}</span>`).join('')}
      </div>
    </div>
    <div class="exp-section">
      <div class="exp-section-h">대상 학교 (선택)</div>
      <select id="exp-school" class="sort-select" style="width:100%">
        <option value="">전체 학교 비교</option>
      </select>
    </div>
    <div class="exp-section">
      <div class="exp-section-h">대상 지표 (다중 선택)</div>
      <div class="col-selector" id="exp-cols">
        ${ALL_COLUMNS.map(c => `<span class="col-chip ${expState.cols.has(c.key) ? 'selected' : ''}" data-key="${c.key}">${c.label}</span>`).join('')}
      </div>
      <div style="font-size:10.5px;color:var(--text-muted);margin-top:6px" id="exp-hint">선택한 컬럼 간 패턴 분석</div>
    </div>
    <div class="exp-section">
      <div class="exp-section-h">조건 / 질문</div>
      <div class="exp-input-row">
        <input type="text" id="exp-query" placeholder="예: 학생수가 -5% 미만으로 감소한 학교">
        <button id="exp-run">분석</button>
      </div>
    </div>`;

  // 학교 셀렉트 채우기
  const sel = document.getElementById('exp-school');
  allSchools.slice().sort((a, b) => (a.school_name || '').localeCompare(b.school_name || '', 'ko')).forEach(s => {
    const o = document.createElement('option');
    o.value = s.school_code; o.textContent = `${s.school_name} (${s.district || ''}구)`;
    sel.appendChild(o);
  });
  sel.onchange = () => { expState.schoolCode = sel.value; };

  // 칩 바인딩
  root.querySelectorAll('#exp-district .fp-chip').forEach(el => {
    el.onclick = () => {
      root.querySelectorAll('#exp-district .fp-chip').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
      expState.district = el.dataset.district;
    };
  });
  root.querySelectorAll('#exp-cols .col-chip').forEach(el => {
    el.onclick = () => {
      const k = el.dataset.key;
      if (expState.cols.has(k)) expState.cols.delete(k); else expState.cols.add(k);
      el.classList.toggle('selected');
      updateExpHint();
    };
  });
  document.getElementById('exp-run').onclick = runExplorer;
  document.getElementById('exp-query').onkeydown = e => { if (e.key === 'Enter') runExplorer(); };
  updateExpHint();
  expInitDone = true;
}

const EXP_HINTS = {
  'student_count,class_count': '학생수↔학급수 연동 점검',
  'student_count,teacher_count': '학생수↔교원수 불균형 탐지',
  'student_count,meal_cost_total': '학생수↔급식비 연동 점검',
  'bullying_victims,bullying_protection': '미조치 피해 점검',
};
function updateExpHint() {
  let hint = '';
  for (const [combo, desc] of Object.entries(EXP_HINTS)) {
    if (combo.split(',').every(p => expState.cols.has(p))) hint = desc;
  }
  if (!hint && expState.cols.size >= 2) hint = '선택한 컬럼 간 패턴 분석';
  if (!hint) hint = '컬럼을 2개 이상 선택해 보세요';
  const h = document.getElementById('exp-hint'); if (h) h.textContent = hint;
}

async function runExplorer() {
  const query = document.getElementById('exp-query').value.trim();
  const cols = Array.from(expState.cols);
  const labels = ALL_COLUMNS.filter(c => expState.cols.has(c.key)).map(c => c.label);
  const area = document.getElementById('explorer-results-area');
  area.insertAdjacentHTML('afterbegin', '<div id="exp-loading" style="padding:12px;color:var(--text-muted);font-size:12px">분석 중…</div>');
  try {
    const data = await (await fetch(API + '/api/custom-analysis', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ school_code: expState.schoolCode, columns: cols, district_filter: expState.district, question: query || '검토 신호 분석' })
    })).json();
    const ld = document.getElementById('exp-loading'); if (ld) ld.remove();

    const hlSet = new Set((data.highlight_cells || []).map(h => `${h.row}_${h.col}`));
    let tableHtml = '';
    if (data.result_data && data.result_data.length) {
      const ks = Object.keys(data.result_data[0]);
      tableHtml = `<table class="cat-table"><thead><tr>${ks.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>`;
      data.result_data.slice(0, 12).forEach((row, ri) => {
        tableHtml += '<tr>' + ks.map(c => {
          let v = row[c]; if (typeof v === 'number') v = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
          const isHl = hlSet.has(`${ri}_${c}`);
          return `<td${isHl ? ' class="cat-detected"' : ''}>${v == null ? '-' : v}</td>`;
        }).join('') + '</tr>';
      });
      tableHtml += '</tbody></table>';
    }
    const ai = data.ai || {}, conf = data.confidence || '중간';
    const cc = conf === '높음' ? 'conf-high' : 'conf-mid';
    const anomalyText = data.anomalies && data.anomalies.length
      ? `<div style="margin:4px 0;font-size:11px;color:var(--cobalt);font-weight:600">변동 신호: ${data.anomalies.join(', ')}</div>` : '';

    const cardHtml = `
      <div class="cat-card custom open" style="margin-bottom:10px">
        <button class="card-delete" onclick="event.stopPropagation();this.parentElement.remove()" title="삭제">×</button>
        <div class="cat-header" onclick="this.parentElement.classList.toggle('open')">
          <h3>${labels.join(' · ')}${data.school_name ? ' (' + data.school_name + ')' : ''} <span class="cat-code-tag-sm">${expState.district === '전체' ? '전체' : expState.district + '구'}</span></h3>
          <div class="cat-badge"><span class="chat-confidence ${cc}" style="margin:0">${conf}</span><span class="cat-toggle">▾</span></div>
        </div>
        <div class="cat-body">
          ${anomalyText}${tableHtml}
          <div class="cat-ai">
            ${ai['해석'] ? `<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${ai['해석']}</span></div>` : ''}
            ${ai['정상사유'] ? `<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${ai['정상사유']}</span></div>` : ''}
            ${ai['확인권장'] ? `<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${ai['확인권장']}</span></div>` : ''}
          </div>
        </div>
      </div>`;

    // 빈 안내 제거
    const empty = area.querySelector('.exp-results-empty'); if (empty) empty.remove();
    area.insertAdjacentHTML('afterbegin', cardHtml);
    showNotify('분석 결과가 추가되었습니다');
  } catch (e) {
    const ld = document.getElementById('exp-loading'); if (ld) ld.remove();
    console.warn('[explorer] 분석 실패', e);
    area.insertAdjacentHTML('afterbegin', `<div style="color:var(--text-sub);padding:10px 12px;font-size:12px;background:var(--gray-50);border:1px solid var(--border);border-radius:6px;margin-bottom:10px">${FALLBACK_AI_TEXT}</div>`);
  }
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
  document.getElementById('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
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
    chips.innerHTML = ['정상 예외 가능성을 더 설명해줘', '동료군 비교를 요약해줘', '급식비 추이도 같이 보고 싶어', '리포트 문장으로 정리해줘']
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

async function sendChat(text) {
  const query = text || document.getElementById('chat-input').value.trim();
  if (!query) return;
  document.getElementById('chat-input').value = '';
  document.getElementById('chat-send').disabled = true;
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
    let html = `<div class="chat-card">`;
    if (tableHtml) html += `<div class="chat-card-header">분석 결과<span class="chat-confidence ${cc}" style="margin:0">${conf}</span></div><div class="chat-card-body">${tableHtml}</div>`;
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
  }
  document.getElementById('chat-send').disabled = false;
}

// ===== ADVANCED PREVIEW (제안 2 LLM 자동 규칙 생성기 + 제안 4 이상 전파 추적) =====
// ※ 정적 예시 화면. 모든 학교명·수치는 룰셋 정의서/시나리오 카드의 예시.
//   실제 공시 데이터 분석 결과가 아님. 라벨로 명시.

let advInitDone = false;
function initAdvanced() {
  if (advInitDone) return;
  document.querySelectorAll('.adv-subtab').forEach(btn => {
    btn.onclick = () => {
      const sub = btn.dataset.sub;
      document.querySelectorAll('.adv-subtab').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.adv-page').forEach(p => p.classList.toggle('active', p.id === 'adv-' + sub));
    };
  });
  advLoadScenario();
  advInitDone = true;
}

const ADV_RULE_EXAMPLES = {
  1: {
    input: "사립학교인데 학교회계 세입이 30% 넘게 줄었으면 확인해야 하지 않나?",
    name: "사립학교 학교회계 세입 급감",
    cols: "school_type, budget_revenue, year",
    cond: "school_type='사립' AND 세입 전년대비 ≤ -30%",
    code: `df_priv = df[df['school_type'] == '사립']
df_priv = df_priv.sort_values(['school_code','year'])
df_priv['rev_yoy'] = df_priv.groupby('school_code')['budget_revenue'].pct_change() * 100
result = df_priv[df_priv['rev_yoy'] <= -30]`,
    similar: "B1-3 (±30%), B1-4 (±50%)",
    grade: "일반 검토",
    gradeCls: "grade-normal",
    results: [
      { s: "영락고", v: "-49.9%", y: 2024 },
      { s: "풍문고", v: "-44.4%", y: 2024 },
    ],
  },
  2: {
    input: "학폭이 늘었는데 교원수도 같이 줄어든 학교 있어?",
    name: "학폭 증가 + 교원 감소 동시",
    cols: "bullying_cases, teacher_count, year",
    cond: "학폭 전년대비 +50% AND 교원수 -5%",
    code: `df_s = df.sort_values(['school_code','year'])
df_s['b_yoy'] = df_s.groupby('school_code')['bullying_cases'].pct_change()*100
df_s['t_yoy'] = df_s.groupby('school_code')['teacher_count'].pct_change()*100
result = df_s[(df_s['b_yoy']>=50) & (df_s['t_yoy']<=-5)]`,
    similar: "MC-1 (학교 분위기 악화 종합 신호)",
    grade: "우선 검토",
    gradeCls: "grade-priority",
    results: [
      { s: "서라벌고", v: "학폭 +200%, 교원 -8.2%", y: 2025 },
      { s: "노원고", v: "학폭 +100%, 교원 -7.4%", y: 2024 },
    ],
  },
  3: {
    input: "급식비 1인당 단가가 유사학교 평균의 절반 이하인 학교",
    name: "급식비 1인당 유사학교 대비 극저",
    cols: "meal_cost_per_student, district, year",
    cond: "급식비 1인당 < 같은 구 평균의 50%",
    code: `df['peer_avg'] = df.groupby(['district','year'])['meal_cost_per_student'].transform('mean')
result = df[df['meal_cost_per_student'] < df['peer_avg'] * 0.5]`,
    similar: "D2-2 (IQR 극단값)",
    grade: "우선 검토",
    gradeCls: "grade-priority",
    results: [
      { s: "은광여고", v: "1인당 3,200원 (평균 6,500원의 49%)", y: 2023 },
    ],
  },
};

function advLoadExample(n) {
  document.getElementById('adv-rule-input').value = ADV_RULE_EXAMPLES[n].input;
  document.getElementById('adv-gen-area').innerHTML = '';
}

function advGenRule() {
  const v = document.getElementById('adv-rule-input').value.trim();
  let ex = ADV_RULE_EXAMPLES[1];
  for (const k in ADV_RULE_EXAMPLES) {
    if (ADV_RULE_EXAMPLES[k].input === v) { ex = ADV_RULE_EXAMPLES[k]; break; }
  }
  const area = document.getElementById('adv-gen-area');
  area.innerHTML = '<div class="adv-loading"><span class="ai-tag">AI 분석 중…</span><p>LLM이 자연어를 분석하여 Rule Card를 생성합니다 (예시 시연)</p></div>';
  setTimeout(() => {
    area.innerHTML = `
      <div class="adv-rule-card">
        <div class="adv-rule-head">
          <div><span class="ai-tag">AI 생성</span> <b>Rule Card</b></div>
          <span class="grade-badge ${ex.gradeCls}">${ex.grade}</span>
        </div>
        <div class="adv-rule-row"><div class="adv-rule-lb">규칙명</div><div class="adv-rule-vl strong">${ex.name}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">대상 컬럼</div><div class="adv-rule-vl">${ex.cols}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">조건</div><div class="adv-rule-vl">${ex.cond}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">생성된 Python 코드</div><pre class="adv-code-block">${ex.code}</pre></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">유사 기존 룰</div><div class="adv-rule-vl">${ex.similar}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">추천 검토 우선도</div><div class="adv-rule-vl"><span class="grade-badge ${ex.gradeCls}">${ex.grade}</span></div></div>
      </div>
      <div class="adv-warn">샌드박스 실행 중… <code class="adv-code">safe_executor.py</code>: 화이트리스트 + 5초 타임아웃 + df 읽기 전용</div>
      <div class="adv-res">
        <div class="adv-res-head">실행 결과: ${ex.results.length}건 탐지</div>
        ${ex.results.map(r => `<div class="adv-res-row"><b>${r.s}</b> (${r.y}): ${r.v}</div>`).join('')}
      </div>
      <div class="adv-flex" style="margin-top:14px">
        <button class="adv-btn ok" onclick="advNotify('규칙이 룰셋에 추가되었습니다 (예시 시연)')">채택</button>
        <button class="adv-btn ghost" onclick="advNotify('조건 수정 화면 (예시 시연)')">수정</button>
        <button class="adv-btn danger sm" onclick="document.getElementById('adv-gen-area').innerHTML=''">폐기</button>
      </div>`;
  }, 700);
}

const ADV_SCENARIOS = {
  1: {
    title: "학급수 파싱 오류",
    sub: '"28(3)" 형태에서 28이 아닌 3을 추출',
    root: { l: "학급수", v: "3", a: "28", d: "괄호 안 특수학급수(3)를 총 학급수로 잘못 추출" },
    l1: [
      { l: "학급당학생수", v: "234.7명/학급", a: "25.1명/학급", f: "704 / 3 = 234.7" },
      { l: "학급수 변동률", v: "-89.3%", a: "+3.6%", f: "(3-28)/28 = -89.3%" },
    ],
    l2: [
      { r: "C1-8", n: "학급당학생수 급변", grade: "우선 검토", gc: "grade-priority", d: "+209.6명/학급 (임계 1.5)" },
      { r: "D2-2", n: "학급당학생수 IQR 극단", grade: "우선 검토", gc: "grade-priority", d: "234.7 (범위: 18~30)" },
      { r: "C1-1", n: "학생↔학급 역방향", grade: "우선 검토", gc: "grade-priority", d: "학생 -1.7% / 학급 -89.3%" },
    ],
    imp: "검토 우선도 지수 15 — 우선 검토 대상 상위 진입",
    fix: "학급수 원본값 '28(3)'의 파싱 정확성을 확인해 주세요. 괄호 앞 숫자(28)가 총 학급수입니다.",
    detail: [
      { l: "학생수", v: "704명", s: "정상" },
      { l: "학급수(파싱)", v: "3", s: "확인 필요 (실제 28)" },
      { l: "학급당학생수", v: "234.7", s: "부풀림 (실제 25.1)" },
      { l: "학급수 YoY", v: "-89.3%", s: "부풀림 (실제 +3.6%)" },
      { l: "검토 우선도 지수", v: "15", s: "부풀림 (실제 ~6)" },
    ],
  },
  2: {
    title: "급식비 입력단위 혼동",
    sub: "천원 단위를 원 단위로 입력 (1,000배 차이)",
    root: { l: "급식비 총액", v: "3,692,063,000원", a: "3,692,063천원", d: "천원 단위를 원 단위로 입력" },
    l1: [
      { l: "급식비 변동률", v: "+99,900%", a: "+2.3%", f: "(3.69B - 3.69M) / 3.69M" },
      { l: "1인당 급식비", v: "4,989,274원", a: "4,989원", f: "3.69B / 740명" },
    ],
    l2: [
      { r: "C2-3+", n: "급식비 강한 변동", grade: "우선 검토", gc: "grade-priority", d: "+99,900% (학생수 +4.7%)" },
      { r: "D2-2", n: "1인당급식비 IQR 극단", grade: "우선 검토", gc: "grade-priority", d: "4,989,274원 (범위: 3,000~8,000)" },
    ],
    imp: "검토 우선도 지수 13 — 상위 30% 진입",
    fix: "급식비 입력단위가 '천원'인지 확인해 주세요 (매뉴얼 p39 Q1).",
    detail: [
      { l: "학생수", v: "740명", s: "정상" },
      { l: "급식비 총액", v: "3,692,063,000원", s: "확인 필요 (실제 3,692,063천원)" },
      { l: "급식비 YoY", v: "+99,900%", s: "부풀림 (실제 +2.3%)" },
      { l: "1인당 급식비", v: "4,989,274원", s: "부풀림 (실제 4,989원)" },
      { l: "검토 우선도 지수", v: "13", s: "부풀림" },
    ],
  },
  3: {
    title: "교원수 강사 포함 오류",
    sub: "학교알리미 교원 총계(강사 포함)를 KESS 비교에 사용",
    root: { l: "교원수 비교 기준", v: "강사 포함 82명", a: "강사 제외 72명", d: "학교알리미 총계에 강사(10명) 포함, KESS는 미포함" },
    l1: [
      { l: "교원수 교차 차이", v: "10명 불일치", a: "0명 (보정 후)", f: "82 - 72 = 10" },
      { l: "교원수 변동률", v: "+14.3%", a: "+2.8%", f: "강사 포함으로 과대 산출" },
    ],
    l2: [
      { r: "F1'-1", n: "교원수 교차 불일치", grade: "일반 검토", gc: "grade-normal", d: "학교알리미 82 vs KESS 72 (차이 10)" },
      { r: "B1-1", n: "교원수 급변동", grade: "일반 검토", gc: "grade-normal", d: "교원수 +14.3% (임계 10%)" },
      { r: "C1-3", n: "학생↔교원 불균형", grade: "일반 검토", gc: "grade-normal", d: "학생 +1.2% / 교원 +14.3%" },
    ],
    imp: "탐지 영역 3개(F1+B1+C1) — 복합성 가산으로 검토 우선도 지수 부풀림",
    fix: "교원수 비교 시 강사를 제외해 주세요 (학교알리미 총계 - 강사(계) = KESS 비교용).",
    detail: [
      { l: "학교알리미 교원총계", v: "82명(강사 10명 포함)", s: "정의 차이" },
      { l: "KESS 교원수", v: "72명(강사 미포함)", s: "정상" },
      { l: "교차 차이", v: "10명", s: "확인 필요 (보정 후 0)" },
      { l: "교원수 YoY", v: "+14.3%", s: "과대 (실제 +2.8%)" },
      { l: "탐지 영역", v: "3개 (F1+B1+C1)", s: "과대 (실제 0~1개)" },
    ],
  },
};

function advLoadScenario() {
  const sel = document.getElementById('adv-scenario-select');
  if (!sel) return;
  const n = sel.value;
  const s = ADV_SCENARIOS[n];
  const area = document.getElementById('adv-scenario-area');
  area.innerHTML = `
    <div class="adv-prop">
      <div class="adv-prop-head">
        <div>
          <b class="adv-prop-title">${s.title}</b>
          <p class="adv-prop-sub">${s.sub}</p>
        </div>
        <button class="adv-btn ok sm" id="adv-resolve-btn" onclick="advResolve()">원인 정상 확인 → 일괄 해소</button>
      </div>

      <div class="adv-scenario-detail">
        <h4>수치 상세</h4>
        ${s.detail.map(d => `<div class="adv-metric"><span class="adv-metric-lb">${d.l}</span><span class="adv-metric-vl">${d.v} <span class="adv-metric-st">(${d.s})</span></span></div>`).join('')}
      </div>

      <div class="adv-stage-h adv-stage-root">원인 노드</div>
      <div class="adv-node n-root" id="adv-root-node">${s.root.l}: ${s.root.v} <span class="adv-node-real">(실제: ${s.root.a})</span></div>
      <div class="adv-node-desc">${s.root.d}</div>

      <div class="adv-tree">
        <div class="adv-stage-h adv-stage-l1">1차 전파 (파생 지표)</div>
        ${s.l1.map(l => `
          <div class="adv-tree-item adv-l1-item">
            <div class="adv-node n-l1">${l.l}: ${l.v} <span class="adv-node-real">(실제: ${l.a})</span></div>
            <div class="adv-node-desc">산출: ${l.f}</div>
          </div>`).join('')}

        <div class="adv-tree adv-tree-nested">
          <div class="adv-stage-h adv-stage-l2">2차 전파 (거짓 탐지)</div>
          ${s.l2.map(l => `
            <div class="adv-tree-item adv-l2-item">
              <div class="adv-node n-l2">${l.r} ${l.n} <span class="grade-badge ${l.gc}" style="font-size:9px;padding:1px 6px;margin-left:4px">${l.grade}</span></div>
              <div class="adv-node-desc">${l.d} — <span class="adv-false">거짓 양성</span></div>
            </div>`).join('')}

          <div class="adv-tree adv-tree-nested">
            <div class="adv-stage-h adv-stage-l3">3차 영향 (검토 우선도)</div>
            <div class="adv-tree-item adv-imp-item">
              <div class="adv-node n-imp">${s.imp}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="adv-ai-box">
        <div class="adv-ai-h"><span class="ai-tag">AI</span> <b>원인 분석 요약</b></div>
        <p>${s.root.l} 1개 값이 ${s.l2.length}개 룰 탐지 + 검토 우선도 상승으로 전파되었습니다.</p>
        <p class="adv-ai-rec">권장: ${s.fix}</p>
      </div>
    </div>

    <div class="adv-resolved" id="adv-resolved" style="display:none">
      <h4>원인 확인 완료 — ${s.l2.length}건 일괄 해소</h4>
      <p>"${s.root.l}" 값이 정상 확인되어 전파된 ${s.l2.length}개 탐지가 자동 해소되었습니다. 검토 우선도가 재계산됩니다.</p>
    </div>`;
}

function advResolve() {
  document.querySelectorAll('.adv-l1-item .adv-node, .adv-l2-item .adv-node, .adv-imp-item .adv-node, #adv-root-node').forEach(n => n.classList.add('resolved'));
  document.getElementById('adv-resolved').style.display = 'block';
  const b = document.getElementById('adv-resolve-btn');
  if (b) { b.disabled = true; b.textContent = '해소 완료'; b.classList.add('disabled'); }
}

function advNotify(msg) { showNotify(msg); }
