// ===== EduData Watch v7 — Dashboard landing + floating chat =====
const API = '';
let currentSchoolCode = null;
let chartMain = null, chartBully = null;
let allSchools = [];
let dashboardData = null;
let activeFilters = { star: 'all', category: 'all', district: 'all' };
let chatHistory = [];
let chatContext = 'dashboard';

(async () => {
  bindNav();
  bindChat();
  await loadDashboard();
})();

// ===== NAV =====
function bindNav() {
  document.querySelector('[data-view="dashboard"]').onclick = (e) => { e.preventDefault(); showView('dashboard'); };
  const ns = document.getElementById('nav-school');
  ns.onclick = (e) => { e.preventDefault(); if (currentSchoolCode) showView('school'); };
  document.querySelector('[data-view="advanced"]').onclick = (e) => { e.preventDefault(); showView('advanced'); initAdvanced(); };
  document.getElementById('back-to-dashboard').onclick = (e) => { e.preventDefault(); showView('dashboard'); };
  document.getElementById('nav-search').addEventListener('input', filterSchoolList);
  // advanced 내 "대시보드" 링크
  const advBack = document.getElementById('adv-back');
  if (advBack) advBack.onclick = (e) => { e.preventDefault(); showView('dashboard'); };
}

function showView(view) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + view).classList.add('active');
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  chatContext = view;
  updateChatContext();
  window.scrollTo(0, 0);
}

// ===== DASHBOARD =====
async function loadDashboard() {
  try {
    const [dash, schools] = await Promise.all([
      fetch(API + '/api/dashboard').then(r => r.json()),
      fetch(API + '/api/schools').then(r => r.json()),
    ]);
    dashboardData = dash;
    allSchools = schools;

    const db = dash.data_basis || {};
    document.getElementById('nav-period').textContent = (db.year_range || '—') + ' 공시 기준';
    document.getElementById('data-basis-line').textContent =
      `${db.source || '공시 데이터'} · ${db.year_range || ''}년 · 전체 ${db.schools || schools.length}교 · 검토 후보 ${dash.total_detections}건`;
    document.getElementById('sample-note').innerHTML =
      `<b>프로토타입 표본 N=${db.schools || 42}</b> · ${(db.districts || []).join('·')} 일반고 · ${db.year_range || ''}년 공시 · 확장 시 전국 11,000+`;

    renderTop3(dash.top3 || []);
    renderFilters(dash.category_distribution || []);
    renderDistBars(dash.distribution || {});
    renderCatDist(dash.category_distribution || []);
    renderSchoolList(schools);
  } catch (e) {
    console.error('대시보드 로드 실패', e);
    document.getElementById('top3-grid').innerHTML = '<div class="loading">데이터 로드 실패</div>';
  }
}

// ===== TOP 3 =====
function renderTop3(top3) {
  const el = document.getElementById('top3-grid');
  if (!top3.length) { el.innerHTML = '<div class="loading">데이터 없음</div>'; return; }
  el.innerHTML = top3.map((s, i) => {
    const rank = i + 1;
    const stars = '★'.repeat(s.max_star);
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
            <span class="stars-lg" title="검토 등급">${stars}</span>
          </div>
          <span class="score-mini" title="우선순위 점수">점수 ${s.score}</span>
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

// ===== FILTERS =====
function renderFilters(catDist) {
  const el = document.getElementById('filter-row');
  const cats = catDist.map(c => `<span class="fchip" data-filter="category" data-val="${c.code}" title="${c.code}">${c.ko}</span>`).join('');
  el.innerHTML = `
    <div class="filter-group">
      <span class="filter-label">검토 등급</span>
      <div class="filter-chips">
        <span class="fchip active" data-filter="star" data-val="all">전체</span>
        <span class="fchip" data-filter="star" data-val="3">★★★</span>
        <span class="fchip" data-filter="star" data-val="2">★★</span>
      </div>
    </div>
    <div class="filter-group">
      <span class="filter-label">카테고리</span>
      <div class="filter-chips">
        <span class="fchip active" data-filter="category" data-val="all">전체</span>
        ${cats}
      </div>
    </div>
    <div class="filter-group">
      <span class="filter-label">구</span>
      <div class="filter-chips">
        <span class="fchip active" data-filter="district" data-val="all">전체</span>
        <span class="fchip" data-filter="district" data-val="노원">노원</span>
        <span class="fchip" data-filter="district" data-val="강남">강남</span>
        <span class="fchip" data-filter="district" data-val="관악">관악</span>
      </div>
    </div>`;
  el.querySelectorAll('.fchip').forEach(chip => {
    chip.onclick = () => {
      const f = chip.dataset.filter, v = chip.dataset.val;
      activeFilters[f] = v;
      el.querySelectorAll(`.fchip[data-filter="${f}"]`).forEach(c => c.classList.toggle('active', c.dataset.val === v));
      filterSchoolList();
    };
  });
}

// ===== DISTRIBUTION =====
function renderDistBars(dist) {
  const order = [
    ['21-25', '최우선 21+', 'priority'],
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
function renderSchoolList(schools) {
  const tbl = document.getElementById('school-list-table');
  document.getElementById('list-count').textContent = `${schools.length}교 · 점수 내림차순`;
  tbl.innerHTML = `
    <thead><tr><th>순위</th><th>학교</th><th>구·유형</th><th>탐지 카테고리</th><th>등급</th><th style="text-align:right">점수</th></tr></thead>
    <tbody>${schools.map(s => rowHtml(s)).join('')}</tbody>`;
  tbl.querySelectorAll('tbody tr[data-code]').forEach(tr => {
    tr.onclick = () => goToSchool(tr.dataset.code);
  });
}

function rowHtml(s) {
  const stars = '★'.repeat(s.max_star);
  const cats = (s.categories_ko || []).slice(0, 5).map(c =>
    `<span class="cat-mini" title="${c.code}">${c.ko}</span>`).join('');
  return `<tr data-code="${s.school_code}">
    <td class="rank-cell">${s.rank}</td>
    <td class="school-cell">${s.school_name}</td>
    <td class="dist-cell">${s.district || ''} · ${s.school_type || ''}</td>
    <td class="cats-cell">${cats}</td>
    <td class="stars-cell">${stars}</td>
    <td class="score-cell">${s.score}</td>
  </tr>`;
}

function filterSchoolList() {
  const q = (document.getElementById('nav-search').value || '').toLowerCase().trim();
  const { star, category, district } = activeFilters;
  const filtered = allSchools.filter(s => {
    if (q && !s.school_name.toLowerCase().includes(q)) return false;
    if (star !== 'all' && s.max_star !== parseInt(star)) return false;
    if (category !== 'all' && !(s.categories_ko || []).some(c => c.code === category)) return false;
    if (district !== 'all' && s.district !== district) return false;
    return true;
  });
  renderSchoolList(filtered);
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
  document.getElementById('school-header').innerHTML = `
    <div class="sh-info">
      <h2>${d.school_name}</h2>
      <p class="sh-meta">${d.district}구 · ${d.school_type} · 2023~2025년 공시</p>
    </div>
    <div class="sh-score">
      <span class="score-num">${d.score}</span>
      <span class="score-label">${d.rank}위 / 42교</span>
    </div>`;
  const aiSum = document.getElementById('ai-summary');
  if (d.llm_explanation && !d.llm_explanation.startsWith('(')) {
    let txt = d.llm_explanation.replace(/([②③])/g, '\n$1');
    aiSum.innerHTML = txt.split('\n').map(l => l.trim()).filter(Boolean).map(l => `<div style="margin-bottom:3px">${l}</div>`).join('');
    aiSum.style.display = '';
  } else {
    aiSum.style.display = 'none';
  }
  const cats = d.summary.categories_ko || [];
  document.getElementById('summary-row').innerHTML = `
    <div class="summary-card"><div class="sc-num">${d.summary.detections}</div><div class="sc-label">탐지 건수</div></div>
    <div class="summary-card"><div class="sc-num">${d.summary.num_categories}</div><div class="sc-label">카테고리</div><div class="sc-detail">${cats.join(' · ')}</div></div>
    <div class="summary-card"><div class="sc-num">${d.is_repeat ? '반복' : '-'}</div><div class="sc-label">${d.is_repeat ? '3년 구조적' : '단발성'}</div></div>`;
  renderCategoryCards(d.detection_cards);
  renderCharts(d.chart_data);
  renderDataTable(d.data_table, 'full-data-table');
  initCustomPanel();
  document.getElementById('custom-results').innerHTML = '';
  document.getElementById('archive-title').style.display = 'none';
  updateChatContext();
}

// ===== CATEGORY CARDS =====
let catCardData = [];
function renderCategoryCards(cards) {
  if (!cards || !cards.length) { document.getElementById('category-cards').innerHTML = ''; catCardData = []; return; }
  catCardData = cards;
  document.getElementById('category-cards').innerHTML = cards.map((cat, i) => {
    const isOpen = i < 2, starCls = cat.max_star >= 3 ? 'star3' : 'star2', starBCls = cat.max_star >= 3 ? 's3' : 's2';
    const detCells = new Set();
    // 백엔드 RULE_COLUMNS가 rule_id 기준으로 내려주는 col_labels 사용 (룰명 문자열 의존 제거)
    cat.rules.forEach(r => (r.col_labels || []).forEach(c => detCells.add(`${r.year}_${c}`)));
    let tableHtml = '';
    if (cat.data_table && cat.data_table.length) {
      const yrs = Object.keys(cat.data_table[0]).filter(k => /^\d{4}$/.test(k)).sort();
      tableHtml = `<table class="cat-table"><thead><tr><th>지표</th>${yrs.map(y => `<th>${y}</th>`).join('')}<th>동료군</th></tr></thead><tbody>`;
      cat.data_table.forEach(row => {
        tableHtml += '<tr><td>' + row['지표'] + '</td>';
        yrs.forEach(y => {
          const v = row[y];
          const fmt = v == null ? '-' : (typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1)) : v);
          const isDet = detCells.has(`${y}_${row['지표']}`);
          tableHtml += `<td${isDet ? ' class="cat-detected"' : ''}>${fmt}</td>`;
        });
        const p = row['동료군'];
        tableHtml += `<td class="peer">${p != null ? (Number.isInteger(p) ? p.toLocaleString() : p.toFixed(1)) : '-'}</td></tr>`;
      });
      tableHtml += '</tbody></table>';
    }
    const rulesHtml = cat.rules.map(r =>
      `<div class="cat-item"><strong>${r.year}년</strong> <span class="rule-id-small">${r.rule_id}</span> ${r.detail}</div>`
    ).join('');
    const aiId = `cat-ai-${i}`;
    return `<div class="cat-card ${starCls} ${isOpen ? 'open' : ''}" onclick="if(event.target.closest('.extend-panel,.col-chip'))return;this.classList.toggle('open')">
      <div class="cat-header">
        <h3>${cat.category_ko} <span class="cat-code-tag-sm">${cat.cat_code || ''}</span></h3>
        <div class="cat-badge">${cat.is_repeat ? '<span class="cat-repeat">반복</span>' : ''}<span class="cat-star ${starBCls}">${'★'.repeat(cat.max_star)}</span><span class="cat-toggle">▾</span></div>
      </div>
      <div class="cat-body">${rulesHtml}${tableHtml}
        <div class="cat-ai" id="${aiId}"><span style="color:var(--text-muted);font-size:11px">AI 해석 로드 중…</span></div>
        <div style="text-align:right;margin-top:6px"><button class="chip" onclick="event.stopPropagation();extendCategory(${i})" style="font-size:10px">+ 컬럼 추가 분석</button></div>
      </div></div>`;
  }).join('');
  if (currentSchoolCode) cards.forEach((cat, i) => {
    fetch(API + `/api/school/${currentSchoolCode}/ai/${encodeURIComponent(cat.category_ko)}`).then(r => r.json()).then(a => {
      const el = document.getElementById(`cat-ai-${i}`); if (!el) return;
      el.innerHTML = `<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${a['해석'] || ''}</span></div>${a['정상사유'] ? `<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${a['정상사유']}</span></div>` : ''}${a['확인권장'] ? `<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${a['확인권장']}</span></div>` : ''}`;
    }).catch(() => { const el = document.getElementById(`cat-ai-${i}`); if (el) el.innerHTML = ''; });
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
  const opts = { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { font: { size: 10, family: ff }, usePointStyle: true, padding: 8 } } }, scales: { y: { beginAtZero: false, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 10 } } }, x: { grid: { display: false }, ticks: { font: { size: 10 } } } } };
  chartMain = new Chart(document.getElementById('chart-main'), { type: 'line', plugins: [valLabelPlugin], data: { labels, datasets: [
    { label: '학생수', data: cd['학생수'], borderColor: '#1D4ED8', backgroundColor: 'rgba(29,78,216,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 2.5, fill: true },
    { label: '교원수', data: cd['교원수'], borderColor: '#7C3AED', backgroundColor: 'rgba(124,58,237,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 2.5, fill: true },
    { label: '동료군 학생수', data: cd['동료군_학생수'], borderColor: '#1D4ED8', borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
    { label: '동료군 교원수', data: cd['동료군_교원수'], borderColor: '#7C3AED', borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
  ]}, options: { ...opts, plugins: { ...opts.plugins, title: { display: true, text: '학생수 · 교원수', font: { size: 12, weight: 'bold', family: ff }}}}});
  chartBully = new Chart(document.getElementById('chart-bullying'), { type: 'line', plugins: [valLabelPlugin], data: { labels, datasets: [
    { label: '학폭 건수', data: cd['학폭건수'], borderColor: '#DC2626', backgroundColor: 'rgba(220,38,38,0.06)', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 2.5, fill: true },
    { label: '피해학생', data: cd['피해학생수'], borderColor: '#F59E0B', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 2.5 },
    { label: '보호조치', data: cd['보호조치건수'], borderColor: '#16A34A', tension: .3, pointRadius: 5, pointHoverRadius: 7, borderWidth: 2.5 },
  ]}, options: { ...opts, scales: { ...opts.scales, y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 10 }}}}, plugins: { ...opts.plugins, title: { display: true, text: '학폭 · 보호조치', font: { size: 12, weight: 'bold', family: ff }}}}});
}

// ===== CUSTOM ANALYSIS =====
const ALL_COLUMNS = [
  { key: 'student_count', label: '학생수' }, { key: 'class_count', label: '학급수' }, { key: 'teacher_count', label: '교원수' },
  { key: 'students_per_class', label: '학급당학생수' }, { key: 'students_per_teacher', label: '교원1인당학생수' },
  { key: 'bullying_cases', label: '학폭건수' }, { key: 'bullying_victims', label: '피해학생수' }, { key: 'bullying_protection', label: '보호조치' }, { key: 'bullying_perpetrators', label: '가해학생수' },
  { key: 'graduation_rate', label: '진학률(%)' }, { key: 'meal_cost_total', label: '급식비총액' }, { key: 'meal_cost_per_student', label: '1인당급식비' },
];
const DISTRICTS = ['전체', '노원', '강남', '관악'];
let selectedCols = new Set(['student_count', 'teacher_count']), selectedDistrict = '전체';
const COL_HINTS = {
  'student_count,class_count': '학생수↔학급수 연동 점검',
  'student_count,teacher_count': '학생수↔교원수 불균형 탐지',
  'student_count,meal_cost_total': '학생수↔급식비 연동 점검',
  'bullying_victims,bullying_protection': '미조치 피해 점검',
  'student_count,class_count,teacher_count': '학생·학급·교원 종합 연동',
};

function initCustomPanel() {
  document.getElementById('col-selector').innerHTML = ALL_COLUMNS.map(c => `<span class="col-chip ${selectedCols.has(c.key) ? 'selected' : ''}" onclick="toggleCol(this,'${c.key}')">${c.label}</span>`).join('');
  document.getElementById('district-filter').innerHTML = DISTRICTS.map(d => `<span class="dist-chip ${selectedDistrict === d ? 'selected' : ''}" onclick="selectDist(this,'${d}')">${d === '전체' ? '전체' : d + '구'}</span>`).join('');
  document.getElementById('custom-send').onclick = () => runCustomAnalysis();
  document.getElementById('custom-query').onkeydown = e => { if (e.key === 'Enter') runCustomAnalysis(); };
  updateColHint();
}
function toggleCol(el, key) { selectedCols.has(key) ? selectedCols.delete(key) : selectedCols.add(key); el.classList.toggle('selected'); updateColHint(); }
function selectDist(el, dist) { selectedDistrict = dist; document.querySelectorAll('.dist-chip').forEach(e => e.classList.remove('selected')); el.classList.add('selected'); }
function updateColHint() { let hint = ''; for (const [combo, desc] of Object.entries(COL_HINTS)) { if (combo.split(',').every(p => selectedCols.has(p))) hint = desc; } if (!hint && selectedCols.size >= 2) hint = '선택한 컬럼 간 패턴 분석'; document.getElementById('custom-query').placeholder = hint || '분석 질문 입력…'; }

async function runCustomAnalysis() {
  const query = document.getElementById('custom-query').value.trim();
  const colKeys = Array.from(selectedCols);
  const colLabels = ALL_COLUMNS.filter(c => selectedCols.has(c.key)).map(c => c.label);
  const resultsEl = document.getElementById('custom-results');
  resultsEl.insertAdjacentHTML('afterbegin', '<div id="custom-loading" style="padding:12px;color:var(--text-muted);font-size:12px">분석 중…</div>');
  try {
    const data = await (await fetch(API + '/api/custom-analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ school_code: currentSchoolCode || '', columns: colKeys, district_filter: selectedDistrict, question: query || '검토 신호 분석' }) })).json();
    const ld = document.getElementById('custom-loading'); if (ld) ld.remove();
    const hlSet = new Set((data.highlight_cells || []).map(h => `${h.row}_${h.col}`));
    let tableHtml = '';
    if (data.result_data && data.result_data.length) {
      const cols = Object.keys(data.result_data[0]);
      tableHtml = `<table class="cat-table"><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>`;
      data.result_data.slice(0, 10).forEach((row, ri) => {
        tableHtml += '<tr>' + cols.map(c => {
          let v = row[c]; if (typeof v === 'number') v = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
          const isHl = hlSet.has(`${ri}_${c}`);
          return `<td${isHl ? ' class="cat-detected"' : ''}>${v == null ? '-' : v}</td>`;
        }).join('') + '</tr>';
      });
      tableHtml += '</tbody></table>';
    }
    const ai = data.ai || {}, conf = data.confidence || '중간', cc = conf === '높음' ? 'conf-high' : 'conf-mid';
    const anomalyText = data.anomalies && data.anomalies.length ? `<div style="margin:4px 0;font-size:11px;color:var(--cobalt);font-weight:600">변동 신호: ${data.anomalies.join(', ')}</div>` : '';
    const cardHtml = `<div class="cat-card custom open"><button class="card-delete" onclick="event.stopPropagation();this.parentElement.remove()" title="삭제">×</button><div class="cat-header" onclick="this.parentElement.classList.toggle('open')"><h3>커스텀: ${colLabels.join(' · ')}${data.school_name ? ' (' + data.school_name + ')' : ''}</h3><div class="cat-badge"><span class="chat-confidence ${cc}" style="margin:0">${conf}</span><span class="cat-toggle">▾</span></div></div><div class="cat-body">${anomalyText}${tableHtml}<div class="cat-ai">${ai['해석'] ? `<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${ai['해석']}</span></div>` : ''}${ai['정상사유'] ? `<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${ai['정상사유']}</span></div>` : ''}${ai['확인권장'] ? `<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${ai['확인권장']}</span></div>` : ''}</div></div></div>`;
    resultsEl.insertAdjacentHTML('afterbegin', cardHtml);
    document.getElementById('archive-title').style.display = '';
    showNotify('커스텀 분석 카드가 생성되었습니다');
  } catch (e) {
    const ld = document.getElementById('custom-loading'); if (ld) ld.remove();
    resultsEl.insertAdjacentHTML('afterbegin', `<div style="color:var(--red);padding:8px;font-size:12px">분석 실패: ${e.message}</div>`);
  }
}

// ===== EXTEND CATEGORY (+) =====
function extendCategory(idx) {
  const cards = document.querySelectorAll('.cat-card:not(.custom)');
  const card = cards[idx]; if (!card) return;
  let panel = card.querySelector('.extend-panel');
  if (panel) { panel.style.display = panel.style.display === 'none' ? '' : 'none'; return; }
  const existing = catCardData[idx] && catCardData[idx].col_keys || [];
  const existingLabels = ALL_COLUMNS.filter(c => existing.includes(c.key)).map(c => c.label);
  panel = document.createElement('div'); panel.className = 'extend-panel';
  panel.style.cssText = 'padding:8px 0;border-top:1px dashed var(--border);margin-top:8px';
  panel.innerHTML = `<div style="font-size:10px;color:var(--text-sub);margin-bottom:2px"><strong>기존:</strong> ${existingLabels.join(', ') || '없음'}</div><div style="font-size:10px;font-weight:600;color:var(--text-sub);margin-bottom:4px">추가 컬럼 선택 (기존 + 추가 융합 분석)</div><div class="col-selector" style="margin-bottom:6px">${ALL_COLUMNS.filter(c => !existing.includes(c.key)).map(c => `<span class="col-chip" onclick="event.stopPropagation();this.classList.toggle('selected')" data-key="${c.key}">${c.label}</span>`).join('')}</div><button class="chip" style="background:var(--cobalt);color:#fff;border-color:var(--cobalt)" onclick="event.stopPropagation();runExtend(${idx},this)">확장 분석</button><div class="extend-results"></div>`;
  card.querySelector('.cat-body').appendChild(panel);
}

async function runExtend(idx, btn) {
  const card = document.querySelectorAll('.cat-card:not(.custom)')[idx];
  const panel = card.querySelector('.extend-panel');
  const resultsArea = panel.querySelector('.extend-results');
  const addCols = Array.from(panel.querySelectorAll('.col-chip.selected')).map(e => e.dataset.key);
  if (!addCols.length) { alert('컬럼을 선택해주세요'); return; }
  const existing = catCardData[idx] && catCardData[idx].col_keys || [];
  const allCols = [...new Set([...existing, ...addCols])];
  const allLabels = ALL_COLUMNS.filter(c => allCols.includes(c.key)).map(c => c.label);
  btn.textContent = '분석 중…'; btn.disabled = true;
  try {
    const data = await (await fetch(API + '/api/custom-analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ school_code: currentSchoolCode || '', columns: allCols, district_filter: '전체', question: '기존 검토 후보 컬럼과 추가 컬럼 융합 분석' }) })).json();
    let tableHtml = '';
    if (data.result_data && data.result_data.length) {
      const cols = Object.keys(data.result_data[0]);
      tableHtml = `<table class="cat-table"><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>`;
      data.result_data.slice(0, 6).forEach(row => {
        tableHtml += '<tr>' + cols.map(c => { let v = row[c]; if (typeof v === 'number') v = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1); return `<td>${v == null ? '-' : v}</td>`; }).join('') + '</tr>';
      });
      tableHtml += '</tbody></table>';
    }
    const ai = data.ai || {};
    const aiHtml = `<div class="cat-ai" style="margin-top:6px">${ai['해석'] ? `<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${ai['해석']}</span></div>` : ''}${ai['정상사유'] ? `<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${ai['정상사유']}</span></div>` : ''}${ai['확인권장'] ? `<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${ai['확인권장']}</span></div>` : ''}</div>`;
    const subCard = document.createElement('div');
    subCard.className = 'extend-subcard';
    subCard.innerHTML = `<div style="font-size:11px;font-weight:700;color:var(--cobalt);margin-bottom:4px">확장: ${allLabels.join(' · ')}</div>${tableHtml}${aiHtml}`;
    resultsArea.appendChild(subCard);
    btn.textContent = '완료'; btn.disabled = false;
    showNotify('확장 분석이 카드에 추가되었습니다');
  } catch (e) { btn.textContent = '실패'; btn.disabled = false; }
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
    chips.innerHTML = ['강남구 검토 후보만 보여줘', '★★★ 항목만 요약해줘', '학생수가 급감한 학교는?', '학교폭력 조치 확인 신호가 있는 학교는?']
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
    addMsg('system', `<span style="color:var(--red)">분석 실패: ${e.message}</span>`);
  }
  document.getElementById('chat-send').disabled = false;
}

// ===== ADVANCED PREVIEW (제안 2 LLM 자동 규칙 생성기 + 제안 4 이상 전파 추적) =====
// ※ 정적 예시 화면. 모든 학교명·수치는 룰셋 정의서/시나리오 카드의 예시.
//   실제 공시 데이터 분석 결과가 아님. 라벨로 명시.

let advInitDone = false;
function initAdvanced() {
  if (advInitDone) return;
  // 서브탭 바인딩
  document.querySelectorAll('.adv-subtab').forEach(btn => {
    btn.onclick = () => {
      const sub = btn.dataset.sub;
      document.querySelectorAll('.adv-subtab').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.adv-page').forEach(p => p.classList.toggle('active', p.id === 'adv-' + sub));
    };
  });
  advLoadScenario(); // 시나리오 1 초기 렌더
  advInitDone = true;
}

// ── 제안 2: 예시 입력별 Rule Card 데이터 (정적) ──
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
    star: 2,
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
    star: 3,
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
    star: 3,
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
    const stars = '★'.repeat(ex.star);
    area.innerHTML = `
      <div class="adv-rule-card">
        <div class="adv-rule-head">
          <div><span class="ai-tag">AI 생성</span> <b>Rule Card</b></div>
          <span class="adv-stars">${stars}</span>
        </div>
        <div class="adv-rule-row"><div class="adv-rule-lb">규칙명</div><div class="adv-rule-vl strong">${ex.name}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">대상 컬럼</div><div class="adv-rule-vl">${ex.cols}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">조건</div><div class="adv-rule-vl">${ex.cond}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">생성된 Python 코드</div><pre class="adv-code-block">${ex.code}</pre></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">유사 기존 룰</div><div class="adv-rule-vl">${ex.similar}</div></div>
        <div class="adv-rule-row"><div class="adv-rule-lb">추천 등급</div><div class="adv-rule-vl"><span class="adv-stars">${stars}</span></div></div>
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

// ── 제안 4: 시나리오 데이터 (정적) ──
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
      { r: "C1-8", n: "학급당학생수 급변", s: 3, d: "+209.6명/학급 (임계 1.5)" },
      { r: "D2-2", n: "학급당학생수 IQR 극단", s: 3, d: "234.7 (범위: 18~30)" },
      { r: "C1-1", n: "학생↔학급 역방향", s: 3, d: "학생 -1.7% / 학급 -89.3%" },
    ],
    imp: "우선순위 점수 15점 — 우선 검토 대상 상위 진입",
    fix: "학급수 원본값 '28(3)'의 파싱 정확성을 확인해 주세요. 괄호 앞 숫자(28)가 총 학급수입니다.",
    detail: [
      { l: "학생수", v: "704명", s: "정상" },
      { l: "학급수(파싱)", v: "3", s: "검토 필요 (실제 28)" },
      { l: "학급당학생수", v: "234.7", s: "부풀림 (실제 25.1)" },
      { l: "학급수 YoY", v: "-89.3%", s: "부풀림 (실제 +3.6%)" },
      { l: "우선순위 점수", v: "15점", s: "부풀림 (실제 ~6점)" },
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
      { r: "C2-3+", n: "급식비 강한 변동", s: 3, d: "+99,900% (학생수 +4.7%)" },
      { r: "D2-2", n: "1인당급식비 IQR 극단", s: 3, d: "4,989,274원 (범위: 3,000~8,000)" },
    ],
    imp: "우선순위 점수 13점 — 상위 30% 진입",
    fix: "급식비 입력단위가 '천원'인지 확인해 주세요 (매뉴얼 p39 Q1).",
    detail: [
      { l: "학생수", v: "740명", s: "정상" },
      { l: "급식비 총액", v: "3,692,063,000원", s: "검토 필요 (실제 3,692,063천원)" },
      { l: "급식비 YoY", v: "+99,900%", s: "부풀림 (실제 +2.3%)" },
      { l: "1인당 급식비", v: "4,989,274원", s: "부풀림 (실제 4,989원)" },
      { l: "우선순위 점수", v: "13점", s: "부풀림" },
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
      { r: "F1'-1", n: "교원수 교차 불일치", s: 2, d: "학교알리미 82 vs KESS 72 (차이 10)" },
      { r: "B1-1", n: "교원수 급변동", s: 2, d: "교원수 +14.3% (임계 10%)" },
      { r: "C1-3", n: "학생↔교원 불균형", s: 2, d: "학생 +1.2% / 교원 +14.3%" },
    ],
    imp: "탐지 영역 3개(F1+B1+C1) — 복합성 가산으로 점수 부풀림",
    fix: "교원수 비교 시 강사를 제외해 주세요 (학교알리미 총계 - 강사(계) = KESS 비교용).",
    detail: [
      { l: "학교알리미 교원총계", v: "82명(강사 10명 포함)", s: "정의 차이" },
      { l: "KESS 교원수", v: "72명(강사 미포함)", s: "정상" },
      { l: "교차 차이", v: "10명", s: "검토 필요 (보정 후 0)" },
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
              <div class="adv-node n-l2">${l.r} ${l.n} <span class="adv-det-tag dt${l.s}">${'★'.repeat(l.s)}</span></div>
              <div class="adv-node-desc">${l.d} — <span class="adv-false">거짓 양성</span></div>
            </div>`).join('')}

          <div class="adv-tree adv-tree-nested">
            <div class="adv-stage-h adv-stage-l3">3차 영향 (점수)</div>
            <div class="adv-tree-item adv-imp-item">
              <div class="adv-node n-imp">${s.imp}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="adv-ai-box">
        <div class="adv-ai-h"><span class="ai-tag">AI</span> <b>원인 분석 요약</b></div>
        <p>${s.root.l} 1개 값이 ${s.l2.length}개 룰 탐지 + 우선순위 점수 상승으로 전파되었습니다.</p>
        <p class="adv-ai-rec">권장: ${s.fix}</p>
      </div>
    </div>

    <div class="adv-resolved" id="adv-resolved" style="display:none">
      <h4>원인 확인 완료 — ${s.l2.length}건 일괄 해소</h4>
      <p>"${s.root.l}" 값이 정상 확인되어 전파된 ${s.l2.length}개 탐지가 자동 해소되었습니다. 우선순위 점수가 재계산됩니다.</p>
    </div>`;
}

function advResolve() {
  document.querySelectorAll('.adv-l1-item .adv-node, .adv-l2-item .adv-node, .adv-imp-item .adv-node, #adv-root-node').forEach(n => n.classList.add('resolved'));
  document.getElementById('adv-resolved').style.display = 'block';
  const b = document.getElementById('adv-resolve-btn');
  if (b) { b.disabled = true; b.textContent = '해소 완료'; b.classList.add('disabled'); }
}

function advNotify(msg) { showNotify(msg); }
