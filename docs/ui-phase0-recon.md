# Phase 0 — 읽기 전용 정찰 (UI 심사 임팩트 라운드)

> 코드 변경 0. 다음 작업(배치 1 심각도 색·배치 2 전국현황+방향·배치 3 비활성 버튼)의 분기점 자료.
> 작성 시점: 2026-06-01. 기준 브랜치: subeen-test-prototype.

---

## 1. CSS 변수 정의 위치 + 심각도/방향 하드코딩 색 인벤토리

### 1-1. 변수 정의 — 유일한 출처
[static/style.css:5-23](static/style.css#L5-L23) `:root{}` 블록.

| 변수 | 값 | 용도 |
|---|---|---|
| `--navy` / `--navy-light` | `#0F2A4A` / `#1E3A5F` | 본문·헤더 |
| `--cobalt` / `--cobalt-hover` / `--cobalt-light` / `--cobalt-bg` | `#1D4ED8` / `#1E40AF` / `#DBEAFE` / `#EFF6FF` | 주 강조 (검토 우선) |
| `--red` / `--red-bg` | `#B91C1C` / `#FEF2F2` | 단일 강조 (현재 cell-danger 용도 외 미사용) |
| `--green` / `--green-bg` | `#15803D` / `#F0FDF4` | 단일 강조 |
| `--amber` / `--amber-bg` | `#B45309` / `#FFFBEB` | 단일 강조 (현재 sample-note, repeat row) |
| `--cell-danger` / `--cell-warning` / `--cell-outlier` / `--cell-stale` | navy/amber/indigo/dark-amber | 표 셀 강조 (현재는 셀 색 다 지운 상태이므로 사실상 미사용) |

→ **심각도(severity) 4단계 토큰은 아직 없음.** 현재 단일 색 alias만 정의됨.
→ 새 토큰은 이 `:root` 블록에 추가하면 됨. 하드코딩 hex 금지 규칙은 지킬 수 있음.

### 1-2. 심각도/방향에 쓰인 하드코딩 hex 인벤토리
`:root` 블록 바깥에서 직접 hex를 박은 곳:

| 파일·줄 | 셀렉터 | hex | 분류 | 비고 |
|---|---|---|---|---|
| [style.css:842](static/style.css#L842) | `.sb-card.sb-numbers` | `#0F2A4A` | 학교상세 5박스 좌측보더 | navy 토큰 중복(치환 가능) |
| [style.css:1122](static/style.css#L1122) | `.sb-card.sb-numbers .sb-h::before` | `#0F2A4A` | 위와 동일 | navy 토큰 중복(치환 가능) |
| [style.css:1759-1776](static/style.css#L1759-L1776) | `.sr-peer-row.peer-pos-1~4`, `.peer-neg-1~4` (배경/보더/텍스트) | Tailwind 8단계 (`#F0FDF4`, `#DCFCE7`, `#BBF7D0`, `#86EFAC`, `#FEF2F2`, `#FEE2E2`, `#FECACA`, `#FCA5A5` 등) | 자가진단 peer 비교 행 그라데이션 | **방향(±)+ 강도(5/10/20/30%) 매핑 — 배치 2 Phase 4에서 "초록↑/빨강↓" 제거 요구 대상** |
| [style.css:1805](static/style.css#L1805) | repeat callout 텍스트 | `#B45309` | amber 토큰 중복(치환 가능) | |

→ 배치 2 Phase 4에서 **peer 그라데이션 8단계의 초록·빨강을 중립으로 치환**해야 함. 그 외 hex는 토큰 alias 중복이라 무해.
→ **분포막대(`.dist-bar`)는 hex 없음**. `linear-gradient(var(--cobalt), var(--cobalt-hover))`와 `var(--gray-200)`만 씀.

---

## 2. 심각도 4단계 CSS 훅 판정 (배지 / 표 / 분포막대)

### 2-1. **배지 (`.grade-badge`)** — ❌ 4단계 훅 없음
- DOM: [app.js:331](static/app.js#L331), [app.js:684](static/app.js#L684), [app.js:715](static/app.js#L715), [app.js:948](static/app.js#L948), [app.js:1499](static/app.js#L1499)에서 `<span class="grade-badge ${gc}">${gl}</span>` 형태.
- `gc`(grade class)는 [app.js:47-53](static/app.js#L47-L53) `indexCls()`가 산출 — **3-class로 collapse**: `critical|major → grade-priority`, `minor → grade-normal`, `warning → grade-ref`.
- `gl`(label)은 [app.js:33-39](static/app.js#L33-L39) `indexLabel()`이 4-label로 산출: 즉시/우선/일반/참고.
- 현재 CSS: [style.css:573](static/style.css#L573) `.grade-priority{background:var(--cobalt);color:#fff}` 단 1개. `grade-normal`/`grade-ref`는 별도 색 없이 기본 회색톤.

→ **4단계 색 적용 = `indexCls()` 반환을 4-class로 바꿔야 함.** `indexBin()`의 결과(`critical|major|minor|warning`)를 그대로 mirror해서 클래스명으로 쓰면 가장 안전 — bin은 이미 score → tier 단일 매핑이라 라벨과 100% 일치.

### 2-2. **표 (`tr`/`td`)** — ❌ 4단계 훅 없음
- DOM: [app.js:679-686](static/app.js#L679-L686) `rowHtml()` — `<tr data-code="${s.school_code}">`. **tr에 심각도 class·data-attr 없음**.
- td는 6개로 분리됨: `rank-cell, school-cell, dist-cell, cats-cell, badge-cell, score-cell` — 행 자체나 score-cell에도 심각도 마커 없음.

→ 4단계 색 적용 = 행 단위 강조(좌측 보더·옅은 틴트)나 score-cell 강조를 하려면 **`rowHtml()` 한 줄 수정**해서 tr에 `class="sev-${bin}"` 추가 필요. 또는 score-cell만 표시하면 td에 mirror.

### 2-3. **분포막대 (`.dist-bar`)** — ❌ 4단계 훅 없음, 색 인라인 X
- DOM: [app.js:627-633](static/app.js#L627-L633) `renderDistBars()`. 4행 모두 `.dist-bar` 클래스 + `priority|normal` 2-class.
- **인라인 스타일은 `width:${(v/mx)*100}%`(막대 길이)뿐 — 색은 CSS class.** ✅ 색 토큰화에 장애물 없음.
- 현재 매핑: critical+major → `priority`(코발트), minor+warning → `normal`(회색).
- CSS: [style.css:124-126](static/style.css#L124-L126).

→ 4단계 색 적용 = [app.js:620-625](static/app.js#L620-L625) `order` 배열의 3번째 element를 `cls`로 쓰는데, 거기에 `critical|major|minor|warning` 그대로 mirror하도록 바꾸고, CSS에 `.dist-bar.critical~.warning` 4개 정의.

→ **막대 길이 로직(`width` 인라인)은 안 건드림.** 색만 토큰화. 사용자 지침 "막대 길이/스케일은 손대지 마"와 충돌 없음.

---

## 3. 방향(+/-%) 색 훅 판정

### 3-1. **다년 추세 카드 (`.dr-stat.up/.down`)** — ✅ 훅 있음
- DOM: [app.js:1653](static/app.js#L1653) `dirCls = direction === '감소' ? 'down' : direction === '증가' ? 'up' : 'neutral'`. [app.js:1660,1665](static/app.js#L1660)의 `<div class="dr-stat ${dirCls}">`에 적용.
- CSS: [style.css:813-816](static/style.css#L813-L816) `.ev-drift .dr-stat.down{border-left:3px solid var(--red);color:var(--red)}`, `.dr-stat.up{border-left:3px solid var(--green);color:var(--green)}`.

→ **순수 CSS 1줄로 .up과 .down을 동일 중립색(예: navy)으로 통일 가능.** JS 무수정.

### 3-2. **자가진단 peer 비교 (`.peer-pos-1~4`/`.peer-neg-1~4`)** — ✅ 훅 있음 (방향+강도 8단계)
- DOM: [app.js:1463-1467](static/app.js#L1463-L1467)에서 `lvl = a >= 30 ? 4 : a >= 20 ? 3 : a >= 10 ? 2 : a >= 5 ? 1 : 0`, `dir = p.diff_pct >= 0 ? 'pos' : 'neg'`, `cls = lvl > 0 ? peer-${dir}-${lvl} : peer-flat`.
- CSS: [style.css:1759-1776](static/style.css#L1759-L1776).

→ **여기가 배치 2 Phase 4의 주 타깃**. 사용자 지침: "초록=상승/빨강=하락 제거, 중립색. 숫자 자체를 굵게/크게 강조." → 8개 CSS 룰을 동일 중립 배경+강도별 보더 굵기·텍스트 굵기로 바꾸고, peer-pos/neg 분리 색 제거. JS는 그대로 둠 — class는 lvl·dir 정보 유지(추후 다른 시각화 여지). CSS만 중립화.

→ ※부호·수치(+/-, %)는 JS가 텍스트로 박고 있으니 그대로 보임. **색만 중립**.

### 3-3. 기타
- [style.css:775](static/style.css#L775) `.ev-delta .da-arrow.down`, `.da-arrow.up` — 빈 룰. 미사용으로 보임. 무해.

---

## 4. 화면별 마크업 출처 (정적 vs JS 생성)

| 화면 | 컨테이너 (index.html) | 내부 컨텐츠 | 비고 |
|---|---|---|---|
| 전국현황 (`#view-national`) | `.page-title`, `.stype-tabs`, `#region-grid`, `#national-footnote` | **region-grid 내부는 JS 주입** ([app.js:217-243](static/app.js#L217-L243) `renderNational`) | `stype-tabs`는 [index.html:50-54](static/index.html#L50-L54) 정적 — "중학교 준비중", "초등학교 준비중" 칩 이미 있음 |
| 학교 목록 (`#view-dashboard`) | `.top3-grid`, `.dash-grid` 골격 | **filter-panel·school-list-table·dist-bars·cat-dist 모두 JS 주입** | top3-grid는 [app.js](static/app.js)에서 |
| 학교 상세 (`#view-school`) | `.school-header`, `.sd-summary-grid`, `.md-grid`, `#self-report-section`, `.full-table-details` | **5박스·md-master·md-detail·self-report 모두 JS 주입** | `<details><summary>원자료 테이블…</summary>`은 정적 |
| 룰 생성기 (`#view-rulelab`) | **인라인 스타일이 박힌 정적 마크업 다수** ([index.html:190-290](static/index.html#L190-L290)) | rulelab.js가 데이터만 주입 | **"룰 등록 (비활성)" 버튼은 [index.html:280](static/index.html#L280) 정적 + 인라인 스타일** |
| 챗봇 (`#chat-fab`, `#chat-panel`) | [index.html:299-319](static/index.html#L299-L319) 정적 | 메시지·칩만 JS 주입 | |

→ **JS-rendered가 대부분.** 배치 1·2는 거의 CSS만으로 해결 가능, 단 "심각도 4단계 mirror"는 app.js 1줄 + dist-bar 매핑 배열 4줄 수정이 거의 필수.

---

## 5. ★ 보존 셀렉터 목록 (마크업 작업 시 절대 변경 금지)

### 5-1. ID — `getElementById` 58개 (app.js + rulelab.js)
```
active-chips, ai-summary, ai-summary-wrap, back-to-dashboard, back-to-national,
cat-dist, chart-bullying, chart-main,
chat-chips, chat-close, chat-fab, chat-input, chat-messages, chat-panel,
chat-panel-sub, chat-panel-title, chat-send,
data-basis-line, dist-bars, evidence-cards,
filter-panel, filter-reset,
list-count, md-chart-wrap-container, md-detail, md-evidence-chart,
md-master-cnt, md-rule-list,
national-footnote, national-subtitle, nav-period, nav-school,
nav-search, nav-search-dropdown,
region-grid, rl-code, rl-columns, rl-condition, rl-count, rl-indicators,
rl-interpret, rl-overlap, rl-results, rl-stats, rl-summary,
rule-status-note, rulelab-dashboard, rulelab-empty, rulelab-input,
rl-apply, rl-th0, rl-th1,    (※ rulelab.js 동적 ID: rl-ai-thv${i})
sample-note, school-header, school-list-table, sd-summary-grid,
self-report-section, sort-select, top3-grid, view-dashboard
```

### 5-2. Class — querySelector(All)에서 잡는 클래스
```
.list-area .fp-chip.active .md-rule .nav-tab .rule-item .rule-item.active .view
.sc-total .sc-num
tbody tr[data-code]
```

### 5-3. data-attr
```
data-code  (tr 식별 — 학교코드)
data-filter / data-val  (필터 칩 — bin / category / district / type 등)
data-view  (nav-tab — 뷰 전환)
```

### 5-4. DOM 중첩 — `rowHtml()`의 6 td 순서
```
<tr data-code>
  <td.rank-cell>
  <td.school-cell>
  <td.dist-cell>
  <td.cats-cell>
  <td.badge-cell>  ← grade-badge 내부
  <td.score-cell>  ← idx-pill 내부
```
→ td 분리·순서·이름 모두 보존.

### 5-5. 챗봇·필터 동작 의존 셀렉터
- `#chat-panel`, `#chat-fab`, `#chat-input`, `#chat-send`, `#chat-close`, `#chat-messages`, `#chat-chips`, `#chat-panel-title`, `#chat-panel-sub`
- `.fp-chip[data-filter="bin"][data-val="critical|major|minor|warning"]` — 필터 칩 (이미 4단계 data-val 있음 ✅)
- `#filter-panel`, `#filter-reset`, `.fp-chip.active` (필터 active 토글)

→ 위 셀렉터는 **이름·위치·중첩 그대로 유지**, 추가 class·data-attr만 가산.

---

## 6. 분기 요약 (다음 단계 의사결정용)

### 배치 1 Phase 1 — 심각도 토큰 정의 + 배지
- **토큰 정의**: ✅ 순수 CSS — `:root`에 4단계 변수(예: `--sev-immediate`, `--sev-priority`, `--sev-routine`, `--sev-ref`) 추가.
- **배지 색 적용**:
  - 훅 ❌ 이므로 **`indexCls()` 1함수만 수정**해서 `'sev-${bin}'`을 반환 (또는 기존 grade-priority/normal/ref와 병기). app.js 4~7줄 수정 예상.
  - 단일 출처: `indexBin()`이 score → tier 산출 → `indexLabel()`과 `indexCls()` 모두 동일 bin 참조. 경계값 (50·70) 일치 보장됨.
  - **점수 임계 재계산 X**. 그냥 `indexBin()` 결과를 class로 mirror.

### 배치 1 Phase 2 — 표 + 분포막대
- **표**: 행 강조하려면 `rowHtml()` 한 줄에 `class="sev-${bin}"` 추가 (또는 score-cell 강조만 CSS로). 사용자에게 어느 쪽을 원하는지 물어야 함 (행 전체 vs 점수 셀만).
- **분포막대**: [app.js:620-625](static/app.js#L620-L625) `order` 배열의 cls 컬럼 4개를 `priority/normal` → `critical/major/minor/warning`으로 바꾸고, CSS에 4-class 추가. JS 4줄 수정.

### 배치 2 Phase 3 — 전국현황 히어로 + 강등
- **JS 무수정 가능**: [app.js:217-243](static/app.js#L217-L243) `renderNational()`이 이미 `region-card.active`(서울) vs `region-card.disabled`(나머지 16교) 분리해 렌더 중. **CSS만으로 히어로/스트립 분기 가능**.
- 빈 박스 안 "회색 입력창 같은 요소" 정체는 추가 확인 필요 (스크린샷 못 봄, 추측 안 함). 단순 카드 빈 영역이라면 CSS 패딩·테두리만 손보면 됨.

### 배치 2 Phase 4 — 자가진단 방향색 제거
- **순수 CSS** — [style.css:1759-1776](static/style.css#L1759-L1776) 8개 룰의 색을 중립화 + `.sr-peer-diff` 굵기·크기 강조. JS 무수정.

### 배치 3 Phase 5 — 비활성 버튼
- 타깃: [index.html:280](static/index.html#L280) 인라인 스타일의 `룰 등록 (비활성)` 버튼.
- 정적 마크업. 텍스트 + 클래스 변경만, JS 핸들러(`onclick="alert(...)"` 그대로) + `disabled` 속성 그대로.

---

## 7. 위험 요인 (배치별 사전 확인)

1. **배치 1 Phase 1**: `indexCls()` 수정 시 [app.js:48](static/app.js#L48) 주석("기존 CSS grade-priority/normal/ref 재활용")이 깨짐. → 옵션: ①4-class만 반환 (구 CSS 룰 정리), ②6-class 동시 반환(`grade-priority sev-critical` 등)로 후방호환. **②가 안전** — 챗봇 응답에서 grade-priority 문자열을 직접 쓰는 곳은 없지만 만일에 대비.
2. **배치 1 Phase 2 표 행 강조**: 행 전체에 옅은 틴트가 들어가면 "color is not the only channel"이지만 시각적 노이즈가 늘 수 있음. **좌측 보더 + score-cell 색**만으로 한정하는 게 안전. 사용자 의견 묻기.
3. **배치 2 Phase 4**: peer 비교 행 자체에서 색이 사라지면 "강도" 시각 신호가 약해짐. → JS의 `lvl` 정보는 class로 그대로 두고, CSS에서 **중립 배경 + lvl-1~4별 보더 굵기·텍스트 굵기·폰트 크기 차등**으로 강도 표현 가능.
4. **챗봇 회귀**: 배치 1·2의 JS 수정 후 멀티턴, 학교 상세(유사학교), 룰생성기 동작 확인 필수 (사용자 지침).
5. **카피 톤**: 새 라벨 추가 시 금지어("오류·비리·이상·1위·최악") 안 쓰기. 현재 라벨(즉시/우선/일반/참고)은 안전.

---

## 8. 보고 결론

- ✅ **순수 CSS만으로 가능**: 배치 1 Phase 1(토큰 정의), 배치 2 Phase 3(전국 히어로), Phase 4(방향색 제거), 배치 3(비활성 버튼 표현).
- 🟡 **JS 최소 수정 필요** (사용자 사전 승인 절차 적용):
  - 배치 1 Phase 1 배지: `indexCls()` 1함수, 4~7줄.
  - 배치 1 Phase 2 분포막대: `renderDistBars()` `order` 배열의 cls 컬럼 4줄.
  - 배치 1 Phase 2 표 행 강조 (선택사항): `rowHtml()` tr 1줄.
- 🚫 **이번 라운드 범위 밖**: 챗봇/safe_executor/룰 엔진/점수 산출/IA/표 레이아웃/챗봇 위치(이미 수정 완료).
- ⚠ **사용자 의사결정 필요**:
  - 표 4단계 색 → 행 전체 vs score-cell만?
  - 분포막대 4단계 → CSS만 추가? 아니면 색 의미를 텍스트로도 보강?
  - `indexCls()` 4-class만 반환 vs 6-class 병기?
