// ===== EduData Watch v6 =====
const API='';
let currentSchoolCode=null;
let chartMain=null,chartBully=null;

(async()=>{await loadSidebar()})();

// ===== SIDEBAR =====
async function loadSidebar(){
  try{
    const[dashRes,statsRes,schoolsRes]=await Promise.all([fetch(API+'/api/dashboard'),fetch(API+'/api/stats'),fetch(API+'/api/schools')]);
    const dash=await dashRes.json(),stats=await statsRes.json(),schools=await schoolsRes.json();
    document.getElementById('s-detections').textContent=stats.total_detections;
    document.getElementById('s-schools').textContent=stats.schools;
    document.getElementById('s-rules').textContent=stats.rules_triggered;
    const groups=[{label:'최우선 (21+)',min:21,max:999},{label:'우선 (16~20)',min:16,max:20},{label:'일반 (11~15)',min:11,max:15},{label:'참고 (0~10)',min:0,max:10}];
    let html='';
    for(const g of groups){const items=schools.filter(s=>s.score>=g.min&&s.score<=g.max);if(!items.length)continue;html+=`<div class="school-group-label">${g.label} · ${items.length}교</div>`;for(const s of items){html+=`<div class="school-item ${s.score>=21?'top':''}" data-code="${s.school_code}" onclick="selectSchool('${s.school_code}')"><span class="si-name">${s.school_name}</span><span class="si-score">${s.score}</span></div>`;}}
    document.getElementById('school-list').innerHTML=html;
    const dist=dash.distribution,mx=Math.max(...Object.values(dist),1);
    const dl={'21-25':'21+','16-20':'16-20','11-15':'11-15','6-10':'6-10','0':'0-5'};
    document.getElementById('sidebar-dist').innerHTML='<h4>점수 분포</h4>'+Object.entries(dist).map(([k,v])=>`<div class="dist-row"><span class="dist-label">${dl[k]||k}</span><div class="dist-bar-bg"><div class="dist-bar" style="width:${(v/mx)*100}%"></div></div><span class="dist-count">${v}</span></div>`).join('');
    document.getElementById('school-search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('.school-item').forEach(el=>{el.style.display=el.querySelector('.si-name').textContent.toLowerCase().includes(q)?'':'none'})});
    if(schools.length)selectSchool(schools[0].school_code);
  }catch(e){console.error(e)}
}

// ===== SELECT SCHOOL =====
async function selectSchool(code){
  currentSchoolCode=code;
  document.querySelectorAll('.school-item').forEach(el=>el.classList.toggle('active',el.dataset.code===code));
  document.getElementById('main-empty').style.display='none';
  document.getElementById('main-content').style.display='';
  try{const d=await(await fetch(API+`/api/school/${code}`)).json();renderSchool(d)}catch(e){console.error(e)}
}

// ===== RENDER =====
function renderSchool(d){
  document.getElementById('school-header').innerHTML=`<div class="sh-info"><h2>${d.school_name}</h2><p class="sh-meta">${d.district}구 · ${d.school_type} · 2023~2025</p></div><div class="sh-score"><span class="score-num">${d.score}</span><span class="score-label">${d.rank}위 / 42교</span></div>`;
  // AI Summary ①②③ 줄바꿈
  const aiSum=document.getElementById('ai-summary');
  if(d.llm_explanation&&!d.llm_explanation.startsWith('(')){
    let txt=d.llm_explanation.replace(/([②③])/g,'\n$1');
    aiSum.innerHTML=txt.split('\n').map(l=>l.trim()).filter(l=>l).map(l=>`<div style="margin-bottom:3px">${l}</div>`).join('');
    aiSum.style.display='';
  }else{aiSum.style.display='none'}
  const cats=d.summary.categories_ko||[];
  document.getElementById('summary-row').innerHTML=`<div class="summary-card"><div class="sc-num">${d.summary.detections}</div><div class="sc-label">탐지 건수</div></div><div class="summary-card"><div class="sc-num">${d.summary.num_categories}</div><div class="sc-label">카테고리</div><div class="sc-detail">${cats.join(' · ')}</div></div><div class="summary-card"><div class="sc-num">${d.is_repeat?'반복':'-'}</div><div class="sc-label">${d.is_repeat?'3년 구조적':'단발성'}</div></div>`;
  renderCategoryCards(d.detection_cards);
  renderCharts(d.chart_data);
  renderDataTable(d.data_table,'full-data-table');
  initCustomPanel();
  document.getElementById('custom-results').innerHTML='';
  document.getElementById('archive-title').style.display='none';
  document.getElementById('chat-messages').innerHTML=`<div class="chat-msg system"><div class="msg-body">${d.school_name}에 대해 궁금한 것을 물어보세요.</div></div>`;
  document.getElementById('chat-chips').innerHTML=['급식비 추이 분석','동료군 비교','학폭 패턴 분석','교원 변동 추이'].map(t=>`<button class="chip" onclick="sendChat('${t}')">${t}</button>`).join('');
  document.getElementById('main').scrollTop=0;
}

// ===== CATEGORY CARDS with + button =====
function renderCategoryCards(cards){
  if(!cards||!cards.length){document.getElementById('category-cards').innerHTML='';catCardData=[];return}
  catCardData=cards;
  document.getElementById('category-cards').innerHTML=cards.map((cat,i)=>{
    const isOpen=i<2,starCls=cat.max_star>=3?'star3':'star2',starBCls=cat.max_star>=3?'s3':'s2';
    const detCells=new Set();
    const ruleColMap={'학생↔학급 역방향 변동':['학생수','학급수'],'학급당학생수 급변':['학급당학생수'],'학생↔교원 불균형':['학생수','교원수'],'미조치 피해 (강력)':['피해학생수','보호조치건수'],'미조치 피해 (참고)':['피해학생수','보호조치건수'],'학생·교원 급변동':['학생수','교원수'],'진학률 급변동':['진학률(%)'],'학폭 심의 급증':['학폭 심의건수'],'급식비 변동':['급식비총액(천원)'],'급식비 강한 변동':['급식비총액(천원)'],'유사학교 대비 극단값':['학급당학생수'],'학생수 비정상 변동':['학생수'],'수치 3년 정체':['학급수','교원수']};
    cat.rules.forEach(r=>(ruleColMap[r.rule_name_ko]||[]).forEach(c=>detCells.add(`${r.year}_${c}`)));
    let tableHtml='';
    if(cat.data_table?.length){
      const yrs=Object.keys(cat.data_table[0]).filter(k=>/^\d{4}$/.test(k)).sort();
      tableHtml=`<table class="cat-table"><thead><tr><th>지표</th>${yrs.map(y=>`<th>${y}</th>`).join('')}<th>동료군</th></tr></thead><tbody>`;
      cat.data_table.forEach(row=>{
        tableHtml+='<tr>';tableHtml+=`<td>${row['지표']}</td>`;
        yrs.forEach(y=>{const v=row[y];const fmt=v==null?'-':(typeof v==='number'?(Number.isInteger(v)?v.toLocaleString():v.toFixed(1)):v);const isDet=detCells.has(`${y}_${row['지표']}`);tableHtml+=`<td${isDet?' class="cat-detected"':''}>${fmt}</td>`});
        const p=row['동료군'];tableHtml+=`<td class="peer">${p!=null?(Number.isInteger(p)?p.toLocaleString():p.toFixed(1)):'-'}</td></tr>`;
      });tableHtml+='</tbody></table>';
    }
    const rulesHtml=cat.rules.map(r=>`<div class="cat-item"><strong>${r.year}년</strong> ${r.detail}</div>`).join('');
    const aiId=`cat-ai-${i}`;
    return `<div class="cat-card ${starCls} ${isOpen?'open':''}" onclick="if(event.target.closest('.extend-panel,.col-chip'))return;this.classList.toggle('open')">
      <div class="cat-header"><h3>${cat.category_ko}</h3><div class="cat-badge">${cat.is_repeat?'<span class="cat-repeat">반복</span>':''}<span class="cat-star ${starBCls}">${'★'.repeat(cat.max_star)}</span><span class="cat-toggle">▾</span></div></div>
      <div class="cat-body">${rulesHtml}${tableHtml}
        <div class="cat-ai" id="${aiId}"><span style="color:var(--text-muted);font-size:11px">AI 해석 로드 중...</span></div>
        <div style="text-align:right;margin-top:6px"><button class="chip" onclick="event.stopPropagation();extendCategory(${i})" style="font-size:10px">+ 컬럼 추가 분석</button></div>
      </div></div>`;
  }).join('');
  // Async AI load
  if(currentSchoolCode){cards.forEach((cat,i)=>{fetch(API+`/api/school/${currentSchoolCode}/ai/${encodeURIComponent(cat.category_ko)}`).then(r=>r.json()).then(a=>{const el=document.getElementById(`cat-ai-${i}`);if(!el)return;el.innerHTML=`<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${a['해석']||''}</span></div>${a['정상사유']?`<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${a['정상사유']}</span></div>`:''}${a['확인권장']?`<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${a['확인권장']}</span></div>`:''}`}).catch(()=>{const el=document.getElementById(`cat-ai-${i}`);if(el)el.innerHTML=''})})}
}

// ===== DATA TABLE =====
function renderDataTable(table,targetId){
  const el=document.getElementById(targetId||'full-data-table');
  if(!el||!table?.length){if(el)el.innerHTML='<p style="padding:12px;color:var(--text-muted);font-size:12px">데이터 없음</p>';return}
  const yrs=Object.keys(table[0]).filter(k=>/^\d{4}$/.test(k)).sort();
  let html='<table class="data-table"><thead><tr><th>지표</th>'+yrs.map(y=>`<th>${y}</th>`).join('')+'<th>동료군</th></tr></thead><tbody>';
  table.forEach(row=>{html+='<tr>';html+=`<td>${row['지표']}</td>`;yrs.forEach(y=>{const c=row[y];if(!c||c.value==null){html+='<td>-</td>';return}const cls=c.status==='detected'?'cell-detected':c.status==='warning'?'cell-warning':c.status==='outlier'?'cell-outlier':c.status==='stale'?'cell-stale':'';const v=typeof c.value==='number'?(Number.isInteger(c.value)?c.value.toLocaleString():c.value.toFixed(1)):c.value;html+=`<td class="${cls}">${v}</td>`});const p=row['동료군평균'];html+=`<td class="cell-peer">${p!=null?(Number.isInteger(p)?p.toLocaleString():p.toFixed(1)):'-'}</td></tr>`});
  html+='</tbody></table>';el.innerHTML=html;
}

// ===== CHARTS =====
// 값 레이블 플러그인 (겹침 방지)
const valLabelPlugin={id:'valLabel',afterDatasetsDraw(chart){
  const ctx=chart.ctx;ctx.save();
  chart.data.datasets.forEach((ds,di)=>{
    if(ds.borderDash)return;// 점선(동료군)은 건너뜀
    const meta=chart.getDatasetMeta(di);
    meta.data.forEach((pt,i)=>{
      const val=ds.data[i];if(val==null)return;
      ctx.fillStyle=ds.borderColor||'#333';
      ctx.font='bold 10px Pretendard Variable';ctx.textAlign='center';
      ctx.fillText(Number.isInteger(val)?val:val.toFixed(1),pt.x,pt.y-8);
    });
  });ctx.restore();
}};

function renderCharts(cd){
  if(!cd?.labels)return;
  if(chartMain)chartMain.destroy();if(chartBully)chartBully.destroy();
  const labels=cd.labels,ff='Pretendard Variable';
  const opts={responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{font:{size:10,family:ff},usePointStyle:true,padding:8}}},scales:{y:{beginAtZero:false,grid:{color:'rgba(0,0,0,0.04)'},ticks:{font:{size:10}}},x:{grid:{display:false},ticks:{font:{size:10}}}}};
  chartMain=new Chart(document.getElementById('chart-main'),{type:'line',
    plugins:[valLabelPlugin],
    data:{labels,datasets:[
      {label:'학생수',data:cd['학생수'],borderColor:'#2563EB',backgroundColor:'rgba(37,99,235,0.06)',tension:.3,pointRadius:5,pointHoverRadius:7,borderWidth:2.5,fill:true},
      {label:'교원수',data:cd['교원수'],borderColor:'#7C3AED',backgroundColor:'rgba(124,58,237,0.06)',tension:.3,pointRadius:5,pointHoverRadius:7,borderWidth:2.5,fill:true},
      {label:'동료군 학생수',data:cd['동료군_학생수'],borderColor:'#2563EB',borderDash:[4,4],pointRadius:0,borderWidth:1},
      {label:'동료군 교원수',data:cd['동료군_교원수'],borderColor:'#7C3AED',borderDash:[4,4],pointRadius:0,borderWidth:1},
    ]},options:{...opts,plugins:{...opts.plugins,title:{display:true,text:'학생수 · 교원수',font:{size:12,weight:'bold',family:ff}}}}});
  chartBully=new Chart(document.getElementById('chart-bullying'),{type:'line',
    plugins:[valLabelPlugin],
    data:{labels,datasets:[
      {label:'학폭 건수',data:cd['학폭건수'],borderColor:'#DC2626',backgroundColor:'rgba(220,38,38,0.06)',tension:.3,pointRadius:5,pointHoverRadius:7,borderWidth:2.5,fill:true},
      {label:'피해학생',data:cd['피해학생수'],borderColor:'#F59E0B',tension:.3,pointRadius:5,pointHoverRadius:7,borderWidth:2.5},
      {label:'보호조치',data:cd['보호조치건수'],borderColor:'#16A34A',tension:.3,pointRadius:5,pointHoverRadius:7,borderWidth:2.5},
    ]},options:{...opts,scales:{...opts.scales,y:{beginAtZero:true,grid:{color:'rgba(0,0,0,0.04)'},ticks:{font:{size:10}}}},plugins:{...opts.plugins,title:{display:true,text:'학폭 · 보호조치',font:{size:12,weight:'bold',family:ff}}}}});
}

// ===== CHAT =====
const chatMessages=document.getElementById('chat-messages');
document.getElementById('chat-send').addEventListener('click',()=>sendChat());
document.getElementById('chat-input').addEventListener('keydown',e=>{if(e.key==='Enter')sendChat()});
let chatHistory=[];

async function sendChat(text){
  const query=text||document.getElementById('chat-input').value.trim();
  if(!query)return;
  document.getElementById('chat-input').value='';
  document.getElementById('chat-send').disabled=true;
  addMsg('user',query);
  const lid=addMsg('system','<span style="color:var(--text-muted)">분석 중...</span>');
  try{
    const data=await(await fetch(API+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,school_code:currentSchoolCode||'',conversation_id:currentSchoolCode||'default',history:chatHistory.slice(-3)})})).json();
    removeMsg(lid);
    let tableHtml='';
    if(data.result_data?.length){
      const cols=Object.keys(data.result_data[0]).filter(k=>!k.startsWith('_'));
      // 이전 행 값 저장 (10%+ 변동 하이라이트용)
      const prevVals={};
      tableHtml='<div class="chat-result-table"><table><thead><tr>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr></thead><tbody>';
      data.result_data.slice(0,8).forEach((row,ri)=>{
        tableHtml+='<tr>'+cols.map(c=>{
          let v=row[c];
          let cls='';
          if(typeof v==='number'){
            // 10%+ 변동 감지
            if(prevVals[c]!=null&&prevVals[c]!==0){
              const chg=Math.abs((v-prevVals[c])/prevVals[c]*100);
              if(chg>=10)cls=' class="cat-detected"';
            }
            prevVals[c]=v;
            v=Number.isInteger(v)?v.toLocaleString():v.toFixed(1);
          }
          return`<td${cls}>${v??'-'}</td>`;
        }).join('')+'</tr>';
      });
      tableHtml+='</tbody></table></div>';
    }
    const report=data.report||'',conf=data.confidence||'중간';
    const cc=conf==='높음'?'conf-high':conf==='중간'?'conf-mid':'conf-low';
    // 카드형 답변
    let html=`<div class="chat-card">`;
    if(tableHtml)html+=`<div class="chat-card-header">분석 결과<span class="chat-confidence ${cc}" style="margin:0">${conf}</span></div><div class="chat-card-body">${tableHtml}</div>`;
    if(report){
      // 형광펜: 숫자+% 패턴, 학교명 강조
      let parsed=marked.parse(report);
      parsed=parsed.replace(/(\d+\.?\d*%[p]?)/g,'<span class="hl">$1</span>');
      parsed=parsed.replace(/(감소|증가|급변|급증|급감|이상|0건)/g,'<span class="hl-danger">$1</span>');
      html+=`<div class="chat-card-body" style="border-top:1px solid var(--border-light);font-size:12px;line-height:1.7">${parsed}</div>`;
    }
    html+=`</div>`;
    html+=`<div style="margin-top:4px"><span class="chat-confidence ${cc}">신뢰도: ${conf}</span> <button class="chip" style="margin-left:4px" onclick="archiveChat('${query.replace(/'/g,"\\'")}')">아카이브 저장</button></div>`;
    addMsg('system',html);
    if(data.follow_up_suggestions)document.getElementById('chat-chips').innerHTML=data.follow_up_suggestions.map(s=>`<button class="chip" onclick="sendChat('${s.replace(/'/g,"\\'")}')">${s}</button>`).join('');
    chatHistory.push({query,tableHtml,report,conf});
  }catch(e){removeMsg(lid);addMsg('system',`<span style="color:var(--red)">오류: ${e.message}</span>`)}
  document.getElementById('chat-send').disabled=false;
}

function archiveChat(query){
  const last=chatHistory[chatHistory.length-1];if(!last)return;
  const el=document.getElementById('custom-results');
  const card=document.createElement('div');card.className='cat-card custom open';
  const cc=last.conf==='높음'?'conf-high':last.conf==='중간'?'conf-mid':'conf-low';
  card.innerHTML=`<button class="card-delete" onclick="event.stopPropagation();this.parentElement.remove()" title="삭제">✕</button><div class="cat-header" onclick="this.parentElement.classList.toggle('open')"><h3>${query.substring(0,30)}${query.length>30?'...':''}</h3><div class="cat-badge"><span class="chat-confidence ${cc}" style="margin:0">${last.conf}</span><span class="cat-toggle">▾</span></div></div><div class="cat-body">${last.tableHtml||''}<div class="cat-ai">${last.report?marked.parse(last.report):''}</div></div>`;
  el.prepend(card);
  document.getElementById('archive-title').style.display='';
  showNotify('아카이브에 저장되었습니다');
}

let mid=0;
function addMsg(t,h){const id='m'+(++mid);const d=document.createElement('div');d.className=`chat-msg ${t}`;d.id=id;d.innerHTML=`<div class="msg-body">${h}</div>`;chatMessages.appendChild(d);chatMessages.scrollTop=chatMessages.scrollHeight;return id}
function removeMsg(id){document.getElementById(id)?.remove()}

// ===== CUSTOM ANALYSIS =====
const ALL_COLUMNS=[{key:'student_count',label:'학생수'},{key:'class_count',label:'학급수'},{key:'teacher_count',label:'교원수'},{key:'students_per_class',label:'학급당학생수'},{key:'students_per_teacher',label:'교원1인당학생수'},{key:'bullying_cases',label:'학폭건수'},{key:'bullying_victims',label:'피해학생수'},{key:'bullying_protection',label:'보호조치'},{key:'bullying_perpetrators',label:'가해학생수'},{key:'graduation_rate',label:'진학률(%)'},{key:'meal_cost_total',label:'급식비총액'},{key:'meal_cost_per_student',label:'1인당급식비'}];
const DISTRICTS=['전체','노원','강남','관악'];
let selectedCols=new Set(['student_count','teacher_count']),selectedDistrict='전체';
const COL_HINTS={'student_count,class_count':'학생수↔학급수 연동 점검','student_count,teacher_count':'학생수↔교원수 불균형 탐지','student_count,meal_cost_total':'학생수↔급식비 연동 점검','bullying_victims,bullying_protection':'미조치 피해 점검','student_count,class_count,teacher_count':'학생·학급·교원 종합 연동'};

function initCustomPanel(){
  document.getElementById('col-selector').innerHTML=ALL_COLUMNS.map(c=>`<span class="col-chip ${selectedCols.has(c.key)?'selected':''}" onclick="toggleCol(this,'${c.key}')">${c.label}</span>`).join('');
  document.getElementById('district-filter').innerHTML=DISTRICTS.map(d=>`<span class="dist-chip ${selectedDistrict===d?'selected':''}" onclick="selectDist(this,'${d}')">${d==='전체'?'전체':d+'구'}</span>`).join('');
  document.getElementById('custom-send').onclick=()=>runCustomAnalysis();
  document.getElementById('custom-query').onkeydown=e=>{if(e.key==='Enter')runCustomAnalysis()};
  updateColHint();
}
function toggleCol(el,key){selectedCols.has(key)?selectedCols.delete(key):selectedCols.add(key);el.classList.toggle('selected');updateColHint()}
function selectDist(el,dist){selectedDistrict=dist;document.querySelectorAll('.dist-chip').forEach(e=>e.classList.remove('selected'));el.classList.add('selected')}
function updateColHint(){let hint='';for(const[combo,desc]of Object.entries(COL_HINTS)){if(combo.split(',').every(p=>selectedCols.has(p)))hint=desc}if(!hint&&selectedCols.size>=2)hint='선택한 컬럼 간 이상 패턴 분석';document.getElementById('custom-query').placeholder=hint||'분석 질문 입력...'}

async function runCustomAnalysis(){
  const query=document.getElementById('custom-query').value.trim();
  const colKeys=Array.from(selectedCols);
  const colLabels=ALL_COLUMNS.filter(c=>selectedCols.has(c.key)).map(c=>c.label);
  const resultsEl=document.getElementById('custom-results');
  resultsEl.insertAdjacentHTML('afterbegin','<div id="custom-loading" style="padding:12px;color:var(--text-muted);font-size:12px">분석 중...</div>');
  try{
    const data=await(await fetch(API+'/api/custom-analysis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({school_code:currentSchoolCode||'',columns:colKeys,district_filter:selectedDistrict,question:query||'이상 패턴 분석'})})).json();
    document.getElementById('custom-loading')?.remove();
    // 하이라이트 셀 집합
    const hlSet=new Set((data.highlight_cells||[]).map(h=>`${h.row}_${h.col}`));
    let tableHtml='';
    if(data.result_data?.length){
      const cols=Object.keys(data.result_data[0]);
      tableHtml=`<table class="cat-table"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>`;
      data.result_data.slice(0,10).forEach((row,ri)=>{
        tableHtml+='<tr>'+cols.map(c=>{
          let v=row[c];
          if(typeof v==='number')v=Number.isInteger(v)?v.toLocaleString():v.toFixed(1);
          const isHl=hlSet.has(`${ri}_${c}`);
          return`<td${isHl?' class="cat-detected"':''}>${v??'-'}</td>`;
        }).join('')+'</tr>';
      });
      tableHtml+='</tbody></table>';
    }
    const ai=data.ai||{},conf=data.confidence||'중간',cc=conf==='높음'?'conf-high':'conf-mid';
    const anomalyText=data.anomalies?.length?`<div style="margin:4px 0;font-size:11px;color:var(--blue-dark);font-weight:600">변동률 이상: ${data.anomalies.join(', ')}</div>`:'';
    const cardHtml=`<div class="cat-card custom open"><button class="card-delete" onclick="event.stopPropagation();this.parentElement.remove()" title="삭제">✕</button><div class="cat-header" onclick="this.parentElement.classList.toggle('open')"><h3>커스텀: ${colLabels.join(' · ')}${data.school_name?' ('+data.school_name+')':''}</h3><div class="cat-badge"><span class="chat-confidence ${cc}" style="margin:0">${conf}</span><span class="cat-toggle">▾</span></div></div><div class="cat-body">${anomalyText}${tableHtml}<div class="cat-ai">${ai['해석']?`<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${ai['해석']}</span></div>`:''}${ai['정상사유']?`<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${ai['정상사유']}</span></div>`:''}${ai['확인권장']?`<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${ai['확인권장']}</span></div>`:''}</div></div></div>`;
    resultsEl.insertAdjacentHTML('afterbegin',cardHtml);
    document.getElementById('archive-title').style.display='';
    showNotify('커스텀 분석 카드가 생성되었습니다');
  }catch(e){document.getElementById('custom-loading')?.remove();resultsEl.insertAdjacentHTML('afterbegin',`<div style="color:var(--red);padding:8px;font-size:12px">오류: ${e.message}</div>`)}
}

// ===== EXTEND CATEGORY (+버튼) — 카드 안에 서브카드 =====
let catCardData=[];

function extendCategory(idx){
  const cards=document.querySelectorAll('.cat-card:not(.custom)');
  const card=cards[idx];if(!card)return;
  let panel=card.querySelector('.extend-panel');
  if(panel){panel.style.display=panel.style.display==='none'?'':'none';return}
  const existing=catCardData[idx]?.col_keys||[];
  const existingLabels=ALL_COLUMNS.filter(c=>existing.includes(c.key)).map(c=>c.label);
  panel=document.createElement('div');panel.className='extend-panel';
  panel.style.cssText='padding:8px 0;border-top:1px dashed var(--border);margin-top:8px';
  panel.innerHTML=`<div style="font-size:10px;color:var(--text-sub);margin-bottom:2px"><strong>기존:</strong> ${existingLabels.join(', ')||'없음'}</div><div style="font-size:10px;font-weight:600;color:var(--text-sub);margin-bottom:4px">추가 컬럼 선택 (기존 + 추가 융합 분석)</div><div class="col-selector" style="margin-bottom:6px">${ALL_COLUMNS.filter(c=>!existing.includes(c.key)).map(c=>`<span class="col-chip" onclick="event.stopPropagation();this.classList.toggle('selected')" data-key="${c.key}">${c.label}</span>`).join('')}</div><button class="chip" style="background:var(--blue);color:#fff;border-color:var(--blue)" onclick="event.stopPropagation();runExtend(${idx},this)">확장 분석</button><div class="extend-results"></div>`;
  card.querySelector('.cat-body').appendChild(panel);
}

async function runExtend(idx,btn){
  const card=document.querySelectorAll('.cat-card:not(.custom)')[idx];
  const panel=card.querySelector('.extend-panel');
  const resultsArea=panel.querySelector('.extend-results');
  const addCols=Array.from(panel.querySelectorAll('.col-chip.selected')).map(e=>e.dataset.key);
  if(!addCols.length){alert('컬럼을 선택해주세요');return}
  const existing=catCardData[idx]?.col_keys||[];
  const allCols=[...new Set([...existing,...addCols])];
  const allLabels=ALL_COLUMNS.filter(c=>allCols.includes(c.key)).map(c=>c.label);
  btn.textContent='분석 중...';btn.disabled=true;
  try{
    const data=await(await fetch(API+'/api/custom-analysis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({school_code:currentSchoolCode||'',columns:allCols,district_filter:'전체',question:'기존 검토 후보 컬럼과 추가 컬럼 융합 분석'})})).json();
    const colLabels=allLabels;

    // 테이블
    let tableHtml='';
    if(data.result_data?.length){
      const cols=Object.keys(data.result_data[0]);
      tableHtml=`<table class="cat-table"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>`;
      data.result_data.slice(0,6).forEach(row=>{
        tableHtml+='<tr>'+cols.map(c=>{let v=row[c];if(typeof v==='number')v=Number.isInteger(v)?v.toLocaleString():v.toFixed(1);return`<td>${v??'-'}</td>`}).join('')+'</tr>';
      });
      tableHtml+='</tbody></table>';
    }

    // Sparkline 미니 차트 (캔버스)
    const sparkId='spark-'+idx+'-'+Date.now();
    let sparkHtml='';
    if(data.result_data?.length>=2){
      // 학교 데이터만 (동료군 평균 행 제외)
      const schoolRows=data.result_data.filter(r=>(r['학교명']||'').indexOf('동료군')===-1);
      if(schoolRows.length>=2){
        sparkHtml=`<div class="spark-wrap"><canvas id="${sparkId}" width="200" height="200"></canvas></div>`;
      }
    }

    // AI 해석
    const ai=data.ai||{};
    const aiHtml=`<div class="cat-ai" style="margin-top:6px">${ai['해석']?`<div class="cat-ai-row"><span class="cat-ai-label">해석 </span><span class="cat-ai-value">${ai['해석']}</span></div>`:''}${ai['정상사유']?`<div class="cat-ai-row"><span class="cat-ai-label">정상 사유 </span><span class="cat-ai-value">${ai['정상사유']}</span></div>`:''}${ai['확인권장']?`<div class="cat-ai-row"><span class="cat-ai-label">확인 권장 </span><span class="cat-ai-value">${ai['확인권장']}</span></div>`:''}</div>`;

    // 서브카드 (카드 안의 카드)
    const subCard=document.createElement('div');
    subCard.className='extend-subcard';
    subCard.innerHTML=`<div style="font-size:11px;font-weight:700;color:var(--blue);margin-bottom:4px">확장: ${colLabels.join(' · ')}</div>${tableHtml}${sparkHtml}${aiHtml}`;
    resultsArea.appendChild(subCard);
    btn.textContent='완료';btn.disabled=false;
    showNotify('확장 분석이 카드에 추가되었습니다');

    // Sparkline 렌더
    if(sparkHtml&&data.result_data?.length>=2){
      setTimeout(()=>{
        const canvas=document.getElementById(sparkId);
        if(!canvas)return;
        const schoolRows=data.result_data.filter(r=>(r['학교명']||'').indexOf('동료군')===-1);
        const labels=schoolRows.map(r=>r['연도']||'');
        const datasets=[];
        const colors=['#2563EB','#7C3AED','#DC2626','#F59E0B','#16A34A'];
        let ci=0;
        for(const key of Object.keys(schoolRows[0])){
          if(key==='학교명'||key==='연도'||key==='학교코드'||key==='설립유형'||key==='지역구')continue;
          const vals=schoolRows.map(r=>typeof r[key]==='number'?r[key]:null);
          if(vals.every(v=>v===null))continue;
          datasets.push({label:key,data:vals,borderColor:colors[ci%colors.length],tension:.3,pointRadius:2,borderWidth:1.5});
          ci++;
        }
        new Chart(canvas,{type:'line',data:{labels,datasets},options:{responsive:false,plugins:{legend:{position:'bottom',labels:{font:{size:9},usePointStyle:true,padding:4}}},scales:{y:{ticks:{font:{size:9}}},x:{ticks:{font:{size:9}}}}}});
      },100);
    }
  }catch(e){btn.textContent='오류';btn.disabled=false}
}

function showNotify(msg){
  const n=document.createElement('div');n.className='extend-notify';n.textContent=msg;
  document.body.appendChild(n);
  setTimeout(()=>{n.classList.add('hide');setTimeout(()=>n.remove(),300)},2500);
}
