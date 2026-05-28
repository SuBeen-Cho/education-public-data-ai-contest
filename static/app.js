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
  document.getElementById('back-to-dashboard').onclick = (e) => { e.preventDefault(); showView('dashboard'); };
  document.getElementById('nav-search').addEventListener('input', filterSchoolList);
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
