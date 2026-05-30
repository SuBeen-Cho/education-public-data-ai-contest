// ===== RULE LAB — 샌드박스 룰 생성기 (데모 HTML 로직 통합 + Gemini AI) =====
// 메인 룰 엔진에 일절 영향 없음. 읽기 전용 데이터 사용.

const RL_DATA = [
  {
    query: "학생수는 줄었는데 교원은 그대로인 학교",
    interpret: "<b>학생수</b>가 전년 대비 <b>10% 이상 감소</b>했는데, <b>교원수</b>는 <b>5% 이내</b>로 거의 변동이 없는 학교를 찾습니다.",
    indicators: [
      {name:"학생수",col:"student_count",checked:true},
      {name:"교원수",col:"teacher_count",checked:true},
      {name:"학급수",col:"class_count",checked:false},
      {name:"급식비",col:"meal_cost_total",checked:false}
    ],
    thresholds: [
      {label:"학생 감소 기준",id:"rl-th0",min:-30,max:-5,val:-10,unit:"%"},
      {label:"교원 안정 범위",id:"rl-th1",min:1,max:15,val:5,unit:"%"}
    ],
    columns: ["student_count","teacher_count","student_yoy","teacher_yoy"],
    conditions: [
      {label:"주 조건",value:"학생수 전년 대비 10% 이상 감소",desc:"작년보다 학생이 얼마나 줄었는지",type:"primary",thIdx:0},
      {op:"AND"},
      {label:"보조 조건",value:"교원수 변동 5% 이내",desc:"교원수가 크게 안 변했는지",type:"secondary",thIdx:1}
    ],
    altResults: {
      "-15/5": [{rank:1,school:"수락고",year:2024,metrics:["학생 <b>-12.3%</b>","교원 <b>+1.2%</b>"],overlap:null},{rank:2,school:"불암고",year:2024,metrics:["학생 <b>-10.5%</b>","교원 <b>+3.4%</b>"],overlap:"B1-2"},{rank:3,school:"상계고",year:2025,metrics:["학생 <b>-11.8%</b>","교원 <b>-2.1%</b>"],overlap:"B1-2"},{rank:4,school:"월계고",year:2025,metrics:["학생 <b>-15.2%</b>","교원 <b>+0.8%</b>"],overlap:null}],
      "-5/5": [{rank:1,school:"수락고",year:2024,metrics:["학생 <b>-12.3%</b>","교원 <b>+1.2%</b>"],overlap:null},{rank:2,school:"상계고",year:2025,metrics:["학생 <b>-11.8%</b>","교원 <b>-2.1%</b>"],overlap:"B1-2"},{rank:3,school:"불암고",year:2024,metrics:["학생 <b>-10.5%</b>","교원 <b>+3.4%</b>"],overlap:"B1-2"},{rank:4,school:"노원고",year:2024,metrics:["학생 <b>-8.7%</b>","교원 <b>-1.5%</b>"],overlap:null},{rank:5,school:"혜성여고",year:2025,metrics:["학생 <b>-7.2%</b>","교원 <b>+2.8%</b>"],overlap:null},{rank:6,school:"청원여고",year:2024,metrics:["학생 <b>-6.1%</b>","교원 <b>-0.9%</b>"],overlap:null}],
      "-10/10": [{rank:1,school:"수락고",year:2024,metrics:["학생 <b>-12.3%</b>","교원 <b>+1.2%</b>"],overlap:null},{rank:2,school:"상계고",year:2025,metrics:["학생 <b>-11.8%</b>","교원 <b>-2.1%</b>"],overlap:"B1-2"},{rank:3,school:"불암고",year:2024,metrics:["학생 <b>-10.5%</b>","교원 <b>+3.4%</b>"],overlap:"B1-2"},{rank:4,school:"대진고",year:2025,metrics:["학생 <b>-10.2%</b>","교원 <b>+8.1%</b>"],overlap:null}]
    },
    code: `# AI 생성 룰: 학생 급감 + 교원 유지\ndef check_student_drop_teacher_stable(row, prev):\n    student_yoy = (row['student_count'] - prev['student_count']) \\\n                  / prev['student_count'] * 100\n    teacher_yoy = (row['teacher_count'] - prev['teacher_count']) \\\n                  / prev['teacher_count'] * 100\n    return student_yoy < -10 and abs(teacher_yoy) < 5`,
    results: [
      {rank:1,school:"수락고",year:2024,metrics:["학생 <b>-12.3%</b>","교원 <b>+1.2%</b>"],overlap:null},
      {rank:2,school:"상계고",year:2025,metrics:["학생 <b>-11.8%</b>","교원 <b>-2.1%</b>"],overlap:"B1-2"},
      {rank:3,school:"불암고",year:2024,metrics:["학생 <b>-10.5%</b>","교원 <b>+3.4%</b>"],overlap:"B1-2"}
    ],
    overlapRules: [{rule:"B1-2",name:"학생·학급·교원 급변동(단년)",cols:["student_count","student_yoy"],reason:"학생수 전년 대비 10%+ 변동을 동일 컬럼·동일 임계로 탐지",count:2}],
    pureNewDetail: [{school:"수락고",year:2024,why:"B1-2는 학생수만 보지만, 이 룰은 <b>교원수 안정</b>이라는 추가 조건이 있어 더 구체적인 패턴을 잡아냄"}],
    pureNew: 1, riskLevel: 3, riskName: "자원 배분",
    stats: { years:{2023:0,2024:2,2025:1}, districts:{"노원":2,"관악":1,"강남":0}, range:{metric:"학생수 변동률",min:"-12.3%",max:"-10.5%",avg:"-11.5%"} }
  },
  {
    query: "급식비가 전년 대비 30% 이상 오른 학교",
    interpret: "<b>급식비</b>가 전년 대비 <b>20% 이상 증가</b>했는데, <b>학생수</b>는 <b>5% 이내</b>로 안정적인 학교를 찾습니다.",
    indicators: [{name:"학생수",col:"student_count",checked:true},{name:"급식비",col:"meal_cost_total",checked:true},{name:"교원수",col:"teacher_count",checked:false},{name:"학급수",col:"class_count",checked:false}],
    thresholds: [{label:"급식비 증가 기준",id:"rl-th0",min:10,max:50,val:20,unit:"%"},{label:"학생 안정 범위",id:"rl-th1",min:1,max:15,val:5,unit:"%"}],
    columns: ["meal_cost_total","student_count","meal_yoy","student_yoy"],
    conditions: [{label:"주 조건",value:"급식비 전년 대비 20% 이상 증가",desc:"작년보다 급식비가 얼마나 올랐는지",type:"primary",thIdx:0},{op:"AND"},{label:"보조 조건",value:"학생수 변동 5% 이내",desc:"학생수가 크게 안 변했는지",type:"secondary",thIdx:1}],
    code: `# AI 생성 룰: 급식비 급등 + 학생 안정\ndef check_meal_surge(row, prev):\n    meal_yoy = (row['meal_cost_total'] - prev['meal_cost_total']) / prev['meal_cost_total'] * 100\n    student_yoy = (row['student_count'] - prev['student_count']) / prev['student_count'] * 100\n    return meal_yoy > 20 and abs(student_yoy) < 5`,
    results: [{rank:1,school:"대진여고",year:2025,metrics:["급식비 <b>+87.1%</b>","학생 <b>-3.2%</b>"],overlap:null},{rank:2,school:"영동고",year:2024,metrics:["급식비 <b>+34.2%</b>","학생 <b>+1.8%</b>"],overlap:null},{rank:3,school:"서울세종고",year:2025,metrics:["급식비 <b>+28.6%</b>","학생 <b>-0.5%</b>"],overlap:"C2-3"},{rank:4,school:"인헌고",year:2024,metrics:["급식비 <b>+22.1%</b>","학생 <b>+2.3%</b>"],overlap:"C2-3"}],
    overlapRules: [{rule:"C2-3",name:"급식비 변동",cols:["meal_cost_total","student_count"],reason:"급식비 전년 대비 10%+ 변동을 동일 컬럼으로 탐지",count:2}],
    pureNewDetail: [{school:"대진여고",year:2025,why:"급식비 +87.1%로 C2-3 임계를 훨씬 초과하지만, C2-3는 <b>학생수 안정 조건</b>이 없어 다른 맥락"},{school:"영동고",year:2024,why:"급식비 +34.2%이지만 C2-3의 탐지 범위 밖"}],
    pureNew: 2, riskLevel: 3, riskName: "재정 연동",
    stats: { years:{2023:0,2024:2,2025:2}, districts:{"노원":0,"관악":1,"강남":3}, range:{metric:"급식비 변동률",min:"+22.1%",max:"+87.1%",avg:"+43.0%"} }
  },
  {
    query: "3년 연속 학폭 심의건이 증가하는 학교",
    interpret: "모든 수치 항목에서 <b>3년 연속 정확히 동일한 수치</b>가 반복되는 경우를 찾습니다. 소규모 정수(0~5)와 비율값 100은 제외합니다.",
    indicators: [{name:"전체 수치 항목",col:"*",checked:true}],
    thresholds: [],
    columns: ["student_count","teacher_count","class_count","budget_revenue","meal_cost_total","graduation_rate"],
    conditions: [{label:"주 조건",value:"2023~2025년 값이 전부 같음",desc:"3년 연속 정확히 동일한 수치인지 확인",type:"primary"},{op:"AND"},{label:"필터",value:"5 이하 소규모 값 제외",desc:"1~5명 같은 소규모 정수는 자연스러운 정체이므로 제외",type:"secondary"}],
    code: `# AI 생성 룰: 3년 동일값 반복\ndef check_three_year_same(group, col):\n    vals = group[col].values[-3:]\n    if len(vals) < 3: return False\n    return vals[0] == vals[1] == vals[2] and vals[0] > 5`,
    results: [],
    overlapRules: [{rule:"E2-2",name:"3년 동일값 반복",cols:["student_count","teacher_count","budget_revenue","meal_cost_total"],reason:"동일한 조건으로 이미 14건 탐지 중",count:"전체"}],
    pureNewDetail: [], pureNew: 0, riskLevel: 2, riskName: "미갱신 의심", stats: null,
    fullOverlapMsg: "기존 E2-2(3년 동일값 반복)와 조건이 완전히 동일합니다. 이미 14건을 탐지 중이므로 신규 룰 등록이 불필요합니다."
  }
];

let rlCurrent = -1;
let rlLastAiCode = '';       // AI가 생성한 원본 코드
let rlLastAiCols = [];       // AI가 사용한 컬럼
let rlLastAiThresholds = []; // AI가 반환한 임계값 정보

function setRuleLabQuery(text) {
  document.getElementById('rulelab-input').value = text;
  sendRuleLabMsg();
}

async function sendRuleLabMsg() {
  const q = document.getElementById('rulelab-input').value.trim();
  if (!q) return;

  // 하드코딩 매칭 (정확한 쿼리만)
  const idx = RL_DATA.findIndex(d => q === d.query);
  if (idx >= 0) {
    rlRender(idx);
    return;
  }

  // AI 자유 입력 — Gemini API
  rlCurrent = -1;  // 하드코딩 인덱스 리셋
  document.getElementById('rulelab-empty').style.display = 'none';
  document.getElementById('rulelab-dashboard').style.display = 'block';
  _rl('rl-interpret').innerHTML = '<span style="color:var(--text-muted)">AI가 조건을 해석하고 있습니다...</span>';
  _rl('rl-summary').innerHTML = '';
  _rl('rl-condition').innerHTML = '';
  _rl('rl-overlap').innerHTML = '';
  _rl('rl-code').textContent = '';
  _rl('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">분석 중...</div>';
  _rl('rl-indicators').innerHTML = '';
  _rl('rl-columns').innerHTML = '';
  _rl('rl-count').textContent = '';
  _rl('rl-stats').innerHTML = '';
  _rl('rl-thresholds').innerHTML = '';

  try {
    const res = await fetch('/api/rulelab', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:q}) });
    const data = await res.json();
    if (data.error) { _rl('rl-interpret').innerHTML = `<span style="color:#991B1B">${data.error}</span>`; return; }

    // 좌측: 해석
    _rl('rl-interpret').innerHTML = data.interpretation || '해석 없음';

    // 좌측: 지표 (체크박스)
    const indicators = data.indicators || (data.columns_used || []).map(c => ({name:c, col:c, checked:true}));
    _rl('rl-indicators').innerHTML = indicators.map((ind, i) =>
      `<label class="cb-item ${ind.checked?'checked':''}"><input type="checkbox" ${ind.checked?'checked':''} style="accent-color:var(--cobalt)"> ${ind.name} <code style="font-family:var(--mono);font-size:10px;color:var(--cobalt);background:var(--cobalt-bg);padding:0 4px;border-radius:2px;margin-left:4px">${ind.col}</code></label>`
    ).join('');

    // 좌측: 컬럼
    const cols = data.columns_used || indicators.filter(i=>i.checked).map(i=>i.col);
    _rl('rl-columns').innerHTML = cols.map(c => `<span class="col-tag">${c}</span>`).join('');

    // 좌측: 임계값 (AI가 반환하면 슬라이더 표시)
    const thresholds = data.thresholds || [];
    if (thresholds.length > 0) {
      _rl('rl-thresholds').innerHTML = thresholds.map((t, i) =>
        `<div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="font-size:11px;font-weight:700;color:var(--text-sub)">${t.label}</span></div>
          <div style="display:flex;align-items:center;gap:6px">
            <input type="range" min="${t.min}" max="${t.max}" value="${t.val}" id="rl-ai-th${i}" oninput="document.getElementById('rl-ai-thv${i}').textContent=this.value+'${t.unit}'" style="flex:1;accent-color:var(--cobalt)">
            <span id="rl-ai-thv${i}" style="font-size:12px;font-weight:800;color:var(--cobalt);background:var(--cobalt-bg);padding:1px 7px;border-radius:3px;min-width:40px;text-align:center">${t.val}${t.unit}</span>
          </div>
        </div>`
      ).join('');
      // 적용 버튼 표시
      const applyEl = document.getElementById('rl-apply');
      if (applyEl) applyEl.style.display = 'block';
    } else {
      _rl('rl-thresholds').innerHTML = '<div style="font-size:11px;color:var(--text-muted)">AI가 자동 설정한 조건입니다.</div>';
      const applyEl = document.getElementById('rl-apply');
      if (applyEl) applyEl.style.display = 'none';
    }

    // 가운데: 요약 카드
    const total = data.results ? data.results.length : 0;
    _rl('rl-summary').innerHTML = `
      <div class="sc sc-total"><div class="sc-num">${total}</div><div class="sc-label">전체 탐지 학교</div><div class="sc-detail">210교에 AI 조건 적용</div></div>
      <div class="sc sc-dup"><div class="sc-num">${cols.length}</div><div class="sc-label">사용 지표</div><div class="sc-detail">${cols.join(', ')}</div></div>
      <div class="sc sc-new"><div class="sc-num">210</div><div class="sc-label">분석 대상</div><div class="sc-detail">서울 일반고 전체</div></div>`;

    // 가운데: 조건 플로우 시각화
    const pc = data.primary_condition || { label:'주 조건', value: q, desc:'' };
    const sc = data.secondary_condition;
    const rl = data.risk_level || 2;
    const rn = data.risk_name || '';
    let condHtml = `<div style="font-size:13px;font-weight:800;color:var(--navy);margin-bottom:14px;display:flex;align-items:center;gap:8px"><span class="ai-tag">AI</span> 생성된 검증 조건 <span style="font-size:11px;color:var(--text-muted);font-weight:600;margin-left:auto">위험도 ${rl} · ${rn}</span></div>`;
    condHtml += '<div class="condition-flow">';
    condHtml += `<div class="cond-block primary"><div class="cond-label">${pc.label}</div><div class="cond-value">${pc.value}</div><div class="cond-desc">${pc.desc||''}</div></div>`;
    if (sc) {
      condHtml += '<div class="cond-op">AND</div>';
      condHtml += `<div class="cond-block secondary"><div class="cond-label">${sc.label}</div><div class="cond-value">${sc.value}</div><div class="cond-desc">${sc.desc||''}</div></div>`;
    }
    condHtml += '</div>';
    _rl('rl-condition').innerHTML = condHtml;

    // 가운데: 중복 체크 (AI는 중복 정보 없으므로 안내)
    _rl('rl-overlap').innerHTML = '<div style="font-size:12px;font-weight:800;color:var(--navy);margin-bottom:8px;display:flex;align-items:center;gap:6px"><span style="width:3px;height:11px;background:var(--cobalt);border-radius:2px;display:inline-block"></span>기존 룰 중복 체크</div><div style="font-size:11.5px;color:var(--text-muted);line-height:1.6">AI 생성 조건은 기존 룰과의 중복을 자동으로 확인할 수 없습니다. 하드코딩 시나리오를 선택하면 중복 체크가 표시됩니다.</div>';

    // 가운데: 코드 (저장 — 재실행용)
    rlLastAiCode = data.code || '';
    rlLastAiCols = cols;
    rlLastAiThresholds = thresholds;
    _rl('rl-code').textContent = rlLastAiCode || '# 코드 없음';

    // 우측: 탐지 학교
    _rl('rl-count').textContent = `${total}건`;
    if (data.results && data.results.length > 0) {
      _rl('rl-results').innerHTML = data.results.map((r,i) =>
        `<div class="rp-item"><div class="rp-rank"><div class="rp-num">${i+1}</div><span class="rp-school">${r.school||''}</span><span class="rp-year">${r.year||''}</span></div><div class="rp-detail">${r.detail||''}</div></div>`
      ).join('');
    } else {
      _rl('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">조건에 해당하는 학교가 없습니다.</div>';
    }

    // 우측: 하단 요약
    _rl('rl-footer').innerHTML = `<div style="display:flex;gap:16px;font-size:11px"><span>전체 <b style="color:var(--cobalt)">${total}</b>건</span><span>분석 <b>210</b>교</span></div>`;
    _rl('rl-stats').innerHTML = data.message || '';
  } catch(e) {
    _rl('rl-interpret').innerHTML = `<span style="color:#991B1B">오류: ${e.message}</span>`;
  }
}

function _rl(id) {
  const el = document.getElementById(id);
  if (!el) {
    console.warn('[RuleLab] ID not found:', id);
    return { innerHTML:'', textContent:'', style:{display:''}, set innerHTML(v){}, set textContent(v){} };
  }
  return el;
}

function rlRender(idx) {
  rlCurrent = idx;
  const d = RL_DATA[idx];
  document.getElementById('rulelab-empty').style.display = 'none';
  document.getElementById('rulelab-dashboard').style.display = 'block';

  // 좌측
  _rl('rl-interpret').innerHTML = d.interpret;

  let cbHtml = '';
  d.indicators.forEach((ind,i) => {
    cbHtml += `<label class="cb-item ${ind.checked?'checked':''}"><input type="checkbox" ${ind.checked?'checked':''} onchange="rlToggleIndicator(${i},this.checked)" style="accent-color:var(--cobalt)"> ${ind.name} <code style="font-family:var(--mono);font-size:10px;color:var(--cobalt);background:var(--cobalt-bg);padding:0 4px;border-radius:2px;margin-left:4px">${ind.col}</code></label>`;
  });
  _rl('rl-indicators').innerHTML = cbHtml;

  let thHtml = '';
  d.thresholds.forEach(t => {
    thHtml += `<div style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-size:11px;font-weight:700;color:var(--text-sub)">${t.label}</span></div>
      <div style="display:flex;align-items:center;gap:6px">
        <input type="range" min="${t.min}" max="${t.max}" value="${t.val}" id="${t.id}" oninput="rlSyncSlider('${t.id}',this.value)" style="flex:1;accent-color:var(--cobalt)">
        <input type="text" id="${t.id}t" value="${t.val}${t.unit}" onchange="rlSyncText('${t.id}',this.value,'${t.unit}',${t.min},${t.max})" style="width:52px;padding:4px 6px;border:1px solid var(--border);border-radius:4px;font-size:12px;text-align:center;color:var(--cobalt);font-weight:700">
      </div></div>`;
  });
  if (!d.thresholds.length) thHtml = '<div style="font-size:11px;color:var(--text-muted)">임계값 조정 없이 동작합니다.</div>';
  _rl('rl-thresholds').innerHTML = thHtml;

  let colHtml = '';
  d.columns.forEach(c => { colHtml += `<span class="col-tag">${c}</span>`; });
  _rl('rl-columns').innerHTML = colHtml;

  // 가운데: 요약
  const total = d.results.length;
  const overlapCount = d.results.filter(r=>r.overlap).length;
  rlRenderSummary(total, overlapCount, d);

  // 가운데: 조건
  rlRenderConditions(d);

  // 가운데: 중복
  rlRenderOverlap(d);

  // 가운데: 코드
  _rl('rl-code').textContent = d.code;

  // 우측: 결과
  rlRenderResults(d.results, d);

  // 적용 버튼
  const applyArea = _rl('rl-apply');
  if (d.thresholds.length > 0) {
    applyArea.style.display = 'block';
  } else {
    applyArea.style.display = 'none';
  }
}

function rlRenderSummary(total, overlapCount, d) {
  const pureNew = total - overlapCount;
  const dupDisplay = d.fullOverlapMsg ? '전체' : overlapCount;
  _rl('rl-summary').innerHTML = `
    <div class="sc sc-total"><div class="sc-num">${total}</div><div class="sc-label">전체 탐지 학교</div><div class="sc-detail">210교에 조건 적용</div></div>
    <div class="sc sc-dup"><div class="sc-num">${dupDisplay}</div><div class="sc-label">기존 룰 중복</div><div class="sc-detail">${d.overlapRules.map(r=>r.name).join(', ')||'-'}</div></div>
    <div class="sc sc-new"><div class="sc-num">${d.fullOverlapMsg?0:pureNew}</div><div class="sc-label">순수 신규</div><div class="sc-detail">기존 룰로 잡을 수 없던 것</div></div>`;
}

function rlRenderConditions(d, thOverrides) {
  let html = '';
  d.conditions.forEach(c => {
    if (c.op) { html += `<div class="cond-op">${c.op}</div>`; }
    else {
      let val = c.value;
      if (thOverrides && c.thIdx !== undefined && d.thresholds[c.thIdx]) {
        const nv = thOverrides[c.thIdx];
        if (c.thIdx===0) { if(nv<0) val=val.replace(/-?\d+%/,nv+'%'); else val=val.replace(/\+?\d+%/,'+'+nv+'%'); }
        else { val=val.replace(/\d+%/,Math.abs(nv)+'%'); }
      }
      html += `<div class="cond-block ${c.type}"><div class="cond-label">${c.label}</div><div class="cond-value">${val}</div><div class="cond-desc">${c.desc}</div></div>`;
    }
  });
  _rl('rl-condition').innerHTML = `<div style="font-size:13px;font-weight:800;color:var(--navy);margin-bottom:14px;display:flex;align-items:center;gap:8px"><span class="ai-tag" style="background:linear-gradient(135deg,#7B5EA0,#5B8BA0);color:#fff;padding:3px 10px;border-radius:10px;font-size:9.5px;font-weight:800">AI</span> 생성된 검증 조건 <span style="font-size:11px;color:var(--text-muted);font-weight:600;margin-left:auto">위험도 ${d.riskLevel} · ${d.riskName}</span></div><div class="condition-flow">${html}</div>`;
}

function rlRenderOverlap(d) {
  let html = '<div style="font-size:12px;font-weight:800;color:var(--navy);margin-bottom:12px;display:flex;align-items:center;gap:6px"><span style="width:3px;height:11px;background:var(--cobalt);border-radius:2px;display:inline-block"></span>기존 룰 중복 체크</div>';

  if (d.fullOverlapMsg) {
    html += `<div style="padding:12px 16px;background:#F5F3FF;border:1px solid #E0E0F7;border-left:3px solid #6366F1;border-radius:0 6px 6px 0;font-size:12px;color:#4338CA;font-weight:700;line-height:1.6">${d.fullOverlapMsg}</div>`;
  } else {
    if (d.overlapRules.length && d.overlapRules[0].count > 0) {
      html += '<div style="font-size:10px;font-weight:800;text-transform:uppercase;color:#6366F1;margin-bottom:6px">중복 탐지</div>';
      d.overlapRules.forEach(r => {
        html += `<div style="background:#F5F3FF;border:1px solid #E0E0F7;border-radius:6px;padding:10px 14px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="font-size:12px;font-weight:800;color:#4338CA">${r.name} <code style="font-family:var(--mono);font-size:10px;background:#EDE9FE;padding:1px 5px;border-radius:3px;color:#6366F1;margin-left:4px">${r.rule}</code></span><span style="font-size:11px;font-weight:700;color:#6366F1;background:#EDE9FE;padding:2px 8px;border-radius:10px">${r.count}건</span></div><div style="font-size:11.5px;color:var(--text-sub);line-height:1.55">${r.reason}</div></div>`;
      });
    }
    if (d.pureNewDetail && d.pureNewDetail.length > 0) {
      html += '<div style="font-size:10px;font-weight:800;text-transform:uppercase;color:var(--cobalt);margin:8px 0 6px">순수 신규 탐지</div>';
      d.pureNewDetail.forEach(p => {
        html += `<div style="background:var(--cobalt-bg);border:1px solid var(--cobalt-light);border-radius:6px;padding:10px 14px;margin-bottom:6px"><div style="font-size:12px;font-weight:800;color:var(--cobalt);margin-bottom:4px">${p.school} <code style="font-family:var(--mono);font-size:10px;background:#DBEAFE;padding:1px 5px;border-radius:3px;color:var(--cobalt);margin-left:4px">${p.year}</code></div><div style="font-size:11.5px;color:var(--text-sub);line-height:1.55">${p.why}</div></div>`;
      });
    }
  }
  _rl('rl-overlap').innerHTML = html;
}

function rlRenderResults(results, d) {
  const total = results.length;
  const overlapCount = results.filter(r=>r.overlap).length;
  _rl('rl-count').textContent = `${total}건`;

  if (total === 0) {
    _rl('rl-results').innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted);font-size:12px">탐지 학교 없음<br><span style="font-size:11px">${d.fullOverlapMsg||''}</span></div>`;
  } else {
    _rl('rl-results').innerHTML = results.map(r =>
      `<div class="rp-item ${r.overlap?'overlap':''}" style="padding:10px 16px;border-bottom:1px solid var(--border-light)">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
          <span style="font-size:12px;font-weight:800;color:var(--cobalt);min-width:20px">${r.rank}</span>
          <span style="font-size:13px;font-weight:700;${r.overlap?'text-decoration:line-through;color:var(--text-muted)':'color:var(--text)'}">${r.school}</span>
          <span style="font-size:11px;color:var(--text-muted)">${r.year}</span>
          ${r.overlap?`<span style="font-size:9px;font-weight:800;background:#EDE9FE;color:#6366F1;padding:1px 6px;border-radius:3px">${r.overlap} 중복</span>`:''}
        </div>
        <div style="font-size:11.5px;color:var(--text-sub)">${r.metrics.join(' / ')}</div>
      </div>`
    ).join('');
  }

  // 통계
  if (d.stats && total > 0) {
    const s = d.stats;
    let statsHtml = '<div style="font-size:10px;font-weight:800;text-transform:uppercase;color:var(--text-sub);margin-bottom:6px">연도 분포</div>';
    statsHtml += '<div style="display:flex;gap:8px;margin-bottom:8px">';
    Object.entries(s.years).forEach(([yr,cnt]) => { statsHtml += `<div style="text-align:center"><div style="font-size:16px;font-weight:800;color:${cnt?'var(--cobalt)':'var(--text-muted)'}">${cnt}</div><div style="font-size:10px;color:var(--text-muted)">${yr}</div></div>`; });
    statsHtml += '</div>';
    statsHtml += `<div style="font-size:10px;font-weight:800;text-transform:uppercase;color:var(--text-sub);margin:8px 0 4px">${s.range.metric}</div>`;
    statsHtml += `<div style="font-size:11px;color:var(--text-sub)">최소 ${s.range.min} · 평균 ${s.range.avg} · 최대 ${s.range.max}</div>`;
    _rl('rl-stats').innerHTML = statsHtml;
  } else {
    _rl('rl-stats').innerHTML = '';
  }

  // 하단
  _rl('rl-footer').innerHTML = `<div style="display:flex;gap:16px;font-size:11px"><span>전체 <b style="color:var(--cobalt)">${total}</b>건</span><span>중복 <b style="color:var(--amber)">${overlapCount||(d.fullOverlapMsg?total:0)}</b>건</span><span>신규 <b style="color:var(--green)">${total-overlapCount}</b>건</span></div>`;
}

async function rlApplyFilters() {
  // 하드코딩 시나리오
  if (rlCurrent >= 0) {
    const d = RL_DATA[rlCurrent];
    if (!d.thresholds.length) return;

    const th0 = parseInt(document.getElementById('rl-th0')?.value || d.thresholds[0]?.val);
    const th1 = parseInt(document.getElementById('rl-th1')?.value || d.thresholds[1]?.val);
    const key = th0+'/'+th1;

    rlRenderConditions(d, [th0, th1]);

    let newResults = d.results;
    if (d.altResults) {
      if (d.altResults[key]) { newResults = d.altResults[key]; }
      else {
        let bestKey=null, bestDist=Infinity;
        Object.keys(d.altResults).forEach(k => { const [a,b]=k.split('/').map(Number); const dist=Math.abs(a-th0)+Math.abs(b-th1); if(dist<bestDist){bestDist=dist;bestKey=k;} });
        if (bestKey) newResults = d.altResults[bestKey];
      }
    }

    const total = newResults.length;
    const overlapCount = newResults.filter(r=>r.overlap).length;
    rlRenderSummary(total, overlapCount, d);
    rlRenderResults(newResults, d);
    return;
  }

  // AI 자유 입력 — THRESHOLD_N 변수를 슬라이더 값으로 교체 후 재실행
  if (!rlLastAiCode) return;

  let modifiedCode = rlLastAiCode;
  rlLastAiThresholds.forEach((t, i) => {
    const slider = document.getElementById('rl-ai-th' + i);
    if (slider) {
      const newVal = slider.value;
      // THRESHOLD_0 = 숫자 → THRESHOLD_0 = 새값
      const regex = new RegExp('THRESHOLD_' + i + '\\s*=\\s*[\\-]?[\\d.]+', 'g');
      modifiedCode = modifiedCode.replace(regex, 'THRESHOLD_' + i + ' = ' + newVal);
    }
  });

  _rl('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">재탐지 중...</div>';

  try {
    const res = await fetch('/api/rulelab/rerun', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ code: modifiedCode, columns_used: rlLastAiCols })
    });
    const data = await res.json();

    const total = data.results ? data.results.length : 0;
    _rl('rl-count').textContent = `${total}건`;
    _rl('rl-summary').querySelector('.sc-total .sc-num').textContent = total;

    if (data.results && data.results.length > 0) {
      _rl('rl-results').innerHTML = data.results.map((r,i) =>
        `<div class="rp-item"><div class="rp-rank"><div class="rp-num">${i+1}</div><span class="rp-school">${r.school||''}</span><span class="rp-year">${r.year||''}</span></div><div class="rp-detail">${r.detail||''}</div></div>`
      ).join('');
    } else {
      _rl('rl-results').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">조건에 해당하는 학교가 없습니다.</div>';
    }
    _rl('rl-footer').innerHTML = `<div style="display:flex;gap:16px;font-size:11px"><span>전체 <b style="color:var(--cobalt)">${total}</b>건</span><span>분석 <b>210</b>교</span></div>`;
    _rl('rl-stats').innerHTML = data.message || '';

    // 수정된 코드 표시
    _rl('rl-code').textContent = modifiedCode;
  } catch(e) {
    _rl('rl-results').innerHTML = `<div style="padding:20px;text-align:center;color:#991B1B">재실행 오류: ${e.message}</div>`;
  }
}

function rlToggleIndicator(idx, checked) {
  if (rlCurrent < 0) return;
  RL_DATA[rlCurrent].indicators[idx].checked = checked;
}

function rlSyncSlider(id, val) {
  const th = RL_DATA[rlCurrent]?.thresholds.find(t=>t.id===id);
  document.getElementById(id+'t').value = val + (th?.unit||'');
}
function rlSyncText(id, raw, unit, min, max) {
  const num = parseInt(raw.replace(/[^-\d]/g,''));
  if (isNaN(num)) return;
  const clamped = Math.max(min, Math.min(max, num));
  document.getElementById(id).value = clamped;
  document.getElementById(id+'t').value = clamped + unit;
}

function rlReset() {
  rlCurrent = -1;
  document.getElementById('rulelab-input').value = '';
  document.getElementById('rulelab-dashboard').style.display = 'none';
  document.getElementById('rulelab-empty').style.display = 'block';
}
