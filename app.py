"""
app.py — FastAPI 메인 서버
EduData Watch 프로토타입 백엔드
LLM: Google Gemini 2.5 flash-lite (.env에서 키 로드)
"""
from __future__ import annotations  # Python 3.9 호환: str | None 류 PEP 604 표기 허용

import os
import re
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from data_loader import load_and_merge_all, get_column_descriptions
from rule_engine import RuleEngine, RULE_META
from priority_scorer import (
    calculate_priority_scores, get_top_n, get_score_distribution,
    enrich_with_s_r, filter_active, label_for, LABEL_THRESHOLDS,
)
from safe_executor import safe_execute, SecurityError

# .env 로드
load_dotenv(Path(__file__).parent / ".env")

# Gemini SDK
try:
    from google import genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("[WARN] google-genai 미설치. pip install google-genai")

# 전역 상태
app_state = {}

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

SYSTEM_PROMPT = """당신은 교육 정보공시 데이터 품질 분석 전문 어시스턴트입니다.

[도메인 지식]
- 데이터: 서울 42개 일반고(노원 17, 강남 13, 관악 12), 2023~2025년
- 소스: 학교알리미(KERIS), KESS(KEDI), NEIS(교육부)
- 핵심 관계: 학생수→학급수→교원수 (인과 방향)
- 기준일: 학생수=4/1, 교육통계=상반기4/1·하반기10/1
- 교원수 비교 시 강사 제외 (학교알리미 총계에 강사 포함, KESS에는 미포함)

[정상 예외 주요 항목]
- 신설/폐교/통폐합 시 모든 지표 급변은 정상
- 학폭 공시연도 ≠ 사안 발생 연도 (전학년도 기준)
- 급식비 입력단위 '천원' 미준수 가능성
- 교육청별 증감기준 자체 설정 가능 (기본 10%)
- 가해학생 조치 미실시 시 가해학생수 미포함

[출력 규칙]
- 절대 판정하지 마세요. 금지 표현: "오류다", "잘못이다", "이상하다", "이상치", "비정상", "수상하다"
- 권장 표현만 사용: "검토 후보", "검토 신호", "확인 필요", "확인 권장"
- 학교명은 result_data·입력 데이터의 실제 학교명을 그대로 사용. "OO고등학교", "[학교명]", "A고등학교", "B고", "가명" 같은 플레이스홀더·임의 학교명 절대 금지
- 비교 시 동료군(같은 구, 같은 설립유형) 자동 적용
- 결과에 항상 신뢰도(높음/중간/낮음) 표시
- 한국어로 응답하세요
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 데이터 로드"""
    print("[INFO] 데이터 로드 중...")
    try:
        df = load_and_merge_all()
        engine = RuleEngine(df)
        detections = engine.run_all()
        # 점수 계산 입력 정제 — 비활성 룰(예: C1-5) 제외 + s_r 부여(대표 신호·정렬 단일 출처)
        detections = filter_active(detections)
        detections = enrich_with_s_r(detections)
        scores = calculate_priority_scores(detections)

        # 42교 통일: 검토 신호가 0건인 학교도 0점으로 목록·분포에 포함
        school_meta = df[["school_code", "school_name"]].drop_duplicates(subset=["school_code"])
        existing_codes = set(scores["school_code"]) if not scores.empty else set()
        missing = school_meta[~school_meta["school_code"].astype(str).isin(existing_codes)]
        if not missing.empty:
            zero_rows = pd.DataFrame([{
                "school_code": str(row["school_code"]),
                "school_name": str(row["school_name"]),
                "score": 0, "max_star": 0, "num_categories": 0,
                "categories": "", "cat_weight_sum": 0,
                "is_repeat": False, "num_detections": 0,
            } for _, row in missing.iterrows()])
            scores = pd.concat([scores, zero_rows], ignore_index=True)
            scores = scores.sort_values("score", ascending=False).reset_index(drop=True)
            scores["rank"] = range(1, len(scores) + 1)
            print(f"[INFO] 검토 신호 없는 학교 {len(missing)}교를 0점으로 보강")

        app_state["df"] = df
        app_state["detections"] = detections
        app_state["scores"] = scores
        app_state["columns"] = get_column_descriptions()

        # Gemini 클라이언트 초기화
        api_key = os.getenv("GOOGLE_API_KEY")
        if HAS_GEMINI and api_key:
            app_state["gemini"] = genai.Client(api_key=api_key)
            print(f"[INFO] Gemini 연결 완료 (모델: {GEMINI_MODEL})")
        else:
            app_state["gemini"] = None
            if not api_key:
                print("[WARN] GOOGLE_API_KEY 미설정. LLM 기능 비활성화.")
            if not HAS_GEMINI:
                print("[WARN] google-genai 미설치. LLM 기능 비활성화.")

        print(f"[INFO] 로드 완료: {len(df)}행, 탐지 {len(detections)}건, 학교 {len(scores)}교")
    except Exception as e:
        print(f"[ERROR] 데이터 로드 실패: {e}")
        import traceback; traceback.print_exc()
        app_state["df"] = pd.DataFrame()
        app_state["detections"] = pd.DataFrame()
        app_state["scores"] = pd.DataFrame()
        app_state["columns"] = get_column_descriptions()
        app_state["gemini"] = None

    yield
    print("[INFO] 서버 종료")


app = FastAPI(title="EduData Watch", lifespan=lifespan)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── 모델 ──
class ChatRequest(BaseModel):
    query: str
    conversation_id: str = "default"
    history: list = []
    school_code: str = ""


class ChatResponse(BaseModel):
    plan: dict
    result_data: list
    report: str
    confidence: str
    follow_up_suggestions: list
    sixbox: Optional[dict] = None   # 학교+룰 컨텍스트가 명확할 때 6박스 첨부 (없으면 None)


# ── 엔드포인트 ──
@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


# 영어→한국어 컬럼명 변환
COL_KO = {
    "school_name": "학교명", "school_code": "학교코드", "district": "지역구",
    "school_type": "설립유형", "year": "연도",
    "student_count": "학생수", "class_count": "학급수", "teacher_count": "교원수",
    "students_per_class": "학급당학생수", "students_per_class_orig": "학급당학생수(원본)",
    "students_per_teacher": "교원1인당학생수",
    "bullying_cases": "학폭심의건수", "bullying_victims": "피해학생수",
    "bullying_protection": "보호조치건수", "bullying_perpetrators": "가해학생수",
    "bullying_discipline": "학폭조치건수",
    "graduation_rate": "진학률(%)", "meal_cost_total": "급식비총액(원)",
    "meal_cost_per_student": "1인당급식비(원)",
    "teacher_total_position": "교원총계(직위별)", "instructor_count": "강사수",
    "teacher_count_no_instructor": "교원수(강사제외)",
    "head_teacher_count": "보직교사수",
    "grade1_students": "1학년 학생수", "grade2_students": "2학년 학생수", "grade3_students": "3학년 학생수",
    "budget_revenue": "학교회계 세입(원)", "budget_expense": "학교회계 세출(원)",
    "kess_student_count": "KESS학생수", "kess_teacher_total": "KESS교원수",
    "kess_class_count": "KESS학급수",
    "semester": "학기",
    "fc_changing_room": "탈의실 수", "fc_shower": "샤워실 수", "fc_health_room": "보건실 수",
    "fc_cafeteria": "급식실 수", "fc_dorm": "기숙사 수", "fc_av_room": "시청각실 수",
    "fc_computer_room": "전산실 수", "fc_library": "도서실 수", "fc_gym": "체육관 수",
    "fc_lab": "과학실험실 수", "fc_music": "음악실 수", "fc_art": "미술실 수",
    "student_count_yoy": "학생수변동률(%)", "class_count_yoy": "학급수변동률(%)",
    "teacher_count_yoy": "교원수변동률(%)", "meal_cost_total_yoy": "급식비변동률(%)",
    "teacher_no_inst_yoy": "교원수(강사제외)변동률(%)",
    "students_per_teacher_yoy": "교원1인당학생수변동률(%)",
    "budget_revenue_yoy": "학교회계세입변동률(%)",
    "budget_expense_yoy": "학교회계세출변동률(%)",
    "graduation_rate_yoy": "진학률변동률(%)",
    "kess_teacher_regular": "KESS정규교원수",
    "students_per_teacher_kess": "KESS교원1인당학생수",
    "teacher_count_no_instructor_yoy": "교원수(강사제외)변동률(%)",
    "head_teacher_count_yoy": "보직교사수변동률(%)",
    "rank": "순위",
}

# 사용자 표에 노출 금지 — prompt 컨텍스트 키이거나 내부 식별자.
# LLM이 결과 DataFrame에 끼워 넣어도 사용자에겐 안 보이게 실제 drop.
_INTERNAL_COL_BAN = frozenset({
    "max_sr", "s_r", "school_score",
    "rule_id", "rule_ids", "rule_name", "rule_names",
    "rule_name_ko", "guide", "display_key",
    "school_name_anon",
    "_ord",
})


_COL_UNIT_HINT = {
    # 컬럼키(영문) → 단위 표시. 6박스/peer 텍스트에서 사용.
    "student_count": "명", "class_count": "개", "teacher_count": "명",
    "students_per_class": "명", "students_per_teacher": "명",
    "bullying_cases": "건", "bullying_victims": "명",
    "bullying_protection": "건", "bullying_perpetrators": "명",
    "bullying_discipline": "건",
    "graduation_rate": "%",
    "meal_cost_total": "원", "meal_cost_per_student": "원",
    "budget_revenue": "원", "budget_expense": "원",
    "teacher_total_position": "명", "instructor_count": "명",
    "teacher_count_no_instructor": "명", "head_teacher_count": "명",
    "grade1_students": "명", "grade2_students": "명", "grade3_students": "명",
    "kess_student_count": "명", "kess_teacher_total": "명", "kess_class_count": "개",
    "kess_teacher_regular": "명", "students_per_teacher_kess": "명",
}

def _fmt_krw(amount: float) -> str:
    """원 단위 큰 금액을 억/만/원으로 가독화."""
    try:
        n = int(round(amount))
    except Exception:
        return "—"
    if abs(n) < 10000:
        return f"{n:,}원"
    sign = "-" if n < 0 else ""
    a = abs(n)
    oku, rest = divmod(a, 100000000)
    man, won = divmod(rest, 10000)
    parts = []
    if oku: parts.append(f"{oku:,}억")
    if man: parts.append(f"{man:,}만")
    if won: parts.append(f"{won:,}")
    return sign + (" ".join(parts) + "원" if parts else "0원")

def _fmt_val(col_key: str, v) -> str:
    """6박스/peer 텍스트용 통합 포맷터. 컬럼키 기반 단위 부착·KRW 변환.
    급식비총액은 저장 단위가 천원 → 표시는 원으로 변환 후 KRW 가독 포맷."""
    try:
        x = float(v)
    except Exception:
        return "—"
    # 급식비총액: 천원 단위 저장 → 원으로 변환해서 KRW 표시
    if col_key == "meal_cost_total":
        x = x * 1000
        return _fmt_krw(x) if abs(x) >= 10000 else f"{int(round(x)):,}원"
    # 회계·1인당급식비 큰 금액은 KRW
    if col_key in ("budget_revenue", "budget_expense") and abs(x) >= 10000:
        return _fmt_krw(x)
    if col_key == "meal_cost_per_student":
        return _fmt_krw(x) if abs(x) >= 10000 else f"{int(round(x)):,}원"
    unit = _COL_UNIT_HINT.get(col_key, "")
    if unit == "%":
        sign = "+" if x >= 0 else ""
        return f"{sign}{x:.2f}%"
    # 정수면 콤마, 소수면 2자리
    if abs(x - int(x)) < 1e-9:
        s = f"{int(x):,}"
    else:
        s = f"{x:,.2f}"
    return s + unit if unit else s


def _rename_cols_ko(data_list: list) -> list:
    """결과 데이터의 영어 컬럼명을 한국어로 변환.
    LLM이 만든 groupby/pivot 결과는 컬럼명이 int(연도)인 경우가 있어 str 변환 후 처리.
    *_dist_mean·*_dist_median은 동료군 비교용으로 노출(접미사 라벨 부여).
    _INTERNAL_COL_BAN(max_sr·s_r·rule_id 등)은 실제 결과에서 제거."""
    result = []
    for row in data_list:
        new_row = {}
        for k, v in row.items():
            ks = str(k)
            if ks.startswith("school_name_"):
                continue
            if ks in _INTERNAL_COL_BAN:
                continue
            if ks.endswith("_dist_mean"):
                base = ks[:-len("_dist_mean")]
                ko = COL_KO.get(base, base) + "(동료군 평균)"
            elif ks.endswith("_dist_median"):
                base = ks[:-len("_dist_median")]
                ko = COL_KO.get(base, base) + "(동료군 중앙값)"
            else:
                ko = COL_KO.get(ks, ks)
            new_row[ko] = v
        result.append(new_row)
    return result


@app.get("/api/dashboard")
async def dashboard():
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())

    if scores.empty:
        return {
            "top3": [], "distribution": {}, "category_distribution": [],
            "total_detections": 0, "total_schools": 0, "data_basis": _data_basis(df),
        }

    top3 = get_top_n(scores, 3)
    dist = get_score_distribution(scores)

    # Top3 — 대표 탐지 + 한국어 카테고리/룰명 부착
    top3_enriched = [_enrich_school_summary(row, detections, df) for _, row in top3.iterrows()]

    # 카테고리별 탐지 분포 (한국어명 포함, 코드 보조)
    cat_dist = []
    if not detections.empty:
        cat_codes = detections["rule_id"].apply(_get_category_code)
        cat_counts = cat_codes.value_counts().to_dict()
        for code, ko in CATEGORY_NAMES_KO.items():
            cnt = int(cat_counts.get(code, 0))
            if cnt > 0:
                cat_dist.append({"code": code, "ko": ko, "count": cnt})
        cat_dist.sort(key=lambda x: -x["count"])

    # 룰별 분포 (좌측 필터 accordion용) — RULE_META 전체 25개 노출, 상태 메타 동봉
    # · active + 탐지 N건 → 'n건'
    # · active + 탐지 0건 → '0건'
    # · needs_mapping → '매핑 확인 필요'
    rule_counts = detections["rule_id"].value_counts().to_dict() if not detections.empty else {}
    rule_dist = []
    for rid, meta in RULE_META.items():
        cnt = int(rule_counts.get(rid, 0))
        cat = _get_category_code(rid)
        rule_dist.append({
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, meta.get("name", rid)),
            "category_code": cat,
            "category_ko": CATEGORY_NAMES_KO.get(cat, cat),
            "count": cnt,
            "status": meta.get("status", "active"),
            "mapping_note": meta.get("mapping_note", ""),
        })
    # 정렬: 카테고리 코드 → 같은 카테고리 내 (active 먼저, 탐지 많은순, 룰ID 사전순)
    _STATUS_ORDER = {"active": 0, "needs_mapping": 1, "inactive": 2, "no_source_confirmed": 3}
    rule_dist.sort(key=lambda x: (
        x["category_code"],
        _STATUS_ORDER.get(x["status"], 99),
        -x["count"],
        x["rule_id"],
    ))

    return {
        "top3": top3_enriched,
        "distribution": dist,
        "category_distribution": cat_dist,
        "rule_distribution": rule_dist,
        "rule_status_summary": _rule_status_summary(rule_counts),
        "districts_all": _seoul_25_districts(df),
        "total_detections": len(detections),
        "total_schools": len(scores),
        "zero_schools": int(len(scores[scores["score"] < 6])) if not scores.empty else 0,
        "data_basis": _data_basis(df),
    }


def _rule_status_summary(rule_counts: dict) -> dict:
    """룰별 상태 요약 — active/needs_mapping/inactive/no_source_confirmed 카운트와 표."""
    by_status = {"active": 0, "needs_mapping": 0, "inactive": 0, "no_source_confirmed": 0}
    rows = []
    for rid, meta in RULE_META.items():
        st = meta.get("status", "active")
        by_status[st] = by_status.get(st, 0) + 1
        rows.append({
            "rule_id": rid,
            "name": RULE_NAMES_KO.get(rid, meta.get("name", rid)),
            "category": meta.get("category", ""),
            "status": st,
            "detections": int(rule_counts.get(rid, 0)),
            "mapping_note": meta.get("mapping_note", ""),
        })
    return {"by_status": by_status, "rows": rows}


@app.get("/api/rule-status")
async def rule_status():
    """룰별 구현/매핑/실행/탐지 건수 표 (UI 표용 단일 엔드포인트)."""
    detections = app_state.get("detections", pd.DataFrame())
    rule_counts = detections["rule_id"].value_counts().to_dict() if not detections.empty else {}
    return _rule_status_summary(rule_counts)


@app.get("/api/schools")
async def school_list():
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())
    if scores.empty:
        return []
    return [_enrich_school_summary(row, detections, df) for _, row in scores.iterrows()]


@app.get("/api/school/{school_code}")
async def school_detail(school_code: str):
    detections = app_state.get("detections", pd.DataFrame())
    scores = app_state.get("scores", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())

    if detections.empty:
        raise HTTPException(404, "데이터 없음")

    school_det = detections[detections["school_code"] == school_code]
    school_score = scores[scores["school_code"] == school_code]

    if school_det.empty and school_score.empty:
        raise HTTPException(404, f"학교 {school_code} 없음")

    school_df = df[df["school_code"] == school_code].sort_values("year")
    school_name = school_det["school_name"].iloc[0] if not school_det.empty else (school_df["school_name"].iloc[0] if not school_df.empty else "")
    district = school_df["district"].iloc[0] if not school_df.empty else ""
    school_type = school_df["school_type"].iloc[0] if not school_df.empty else ""

    # 셀 상태 포함 데이터 테이블 구축
    data_table = _build_data_table(school_df, school_det, df)

    # 차트 데이터
    chart_data = _build_chart_data(school_df, df, district)

    # 검토 후보를 한국어 명칭으로 변환 + 관련 데이터 포함
    det_cards = _build_detection_cards(school_det, school_df, df, district)

    # 카테고리 한국어 목록
    cats_ko = list(set(d["category_ko"] for d in det_cards))

    result = {
        "school_code": school_code,
        "school_name": school_name,
        "district": district,
        "school_type": school_type,
        "score": float(school_score["score"].iloc[0]) if not school_score.empty else 0.0,
        "rank": int(school_score["rank"].iloc[0]) if not school_score.empty else 0,
        "is_repeat": bool(school_score["is_repeat"].iloc[0]) if not school_score.empty else False,
        "num_detections": int(school_score["num_detections"].iloc[0]) if not school_score.empty else 0,
        "num_rules": int(school_det["rule_id"].nunique()) if not school_det.empty else 0,
        "summary": {
            "detections": len(school_det),
            "categories_ko": cats_ko,
            "num_categories": len(cats_ko),
        },
        "data_table": data_table,
        "chart_data": chart_data,
        "detection_cards": det_cards,
    }

    # ⑦ 자가진단 리포트 — 학교 상세 하단용 종합 요약 (LLM 미사용, 정적 + 자동 주입)
    result["self_report"] = _build_self_report(result, school_df, df, det_cards, school_score)

    # Gemini 해석: 상단 요약 — 실패 시 안전 폴백 (임의 수치 X)
    client = app_state.get("gemini")
    if client and not school_det.empty:
        try:
            result["llm_explanation"] = _explain_with_gemini(client, school_det, school_df)
        except GeminiError as e:
            print(f"[WARN] llm_explanation Gemini 실패: {e}")
            result["llm_explanation"] = FALLBACK_AI_TEXT
        except Exception as e:
            print(f"[WARN] llm_explanation 예상치 못한 실패: {e}")
            result["llm_explanation"] = FALLBACK_AI_TEXT
    else:
        result["llm_explanation"] = "(LLM 비활성화 상태)"

    # 카테고리별 AI 해석은 별도 엔드포인트로 비동기 로드 (속도 최적화)

    return result


class CustomRequest(BaseModel):
    school_code: str = ""
    columns: list = []
    district_filter: str = "전체"
    question: str = ""


@app.post("/api/custom-analysis")
async def custom_analysis(req: CustomRequest):
    """커스텀 분석 — LLM 코드 생성 없이 서버 직접 분석"""
    df = app_state.get("df", pd.DataFrame())
    if df.empty:
        raise HTTPException(500, "데이터 미로드")

    # 지역 필터
    filtered = df.copy()
    if req.district_filter and req.district_filter != "전체":
        filtered = filtered[filtered["district"] == req.district_filter]

    # 학교 필터 (선택된 학교가 있으면 해당 학교 + 동료군)
    school_name = ""
    if req.school_code:
        school_data = filtered[filtered["school_code"] == req.school_code]
        if not school_data.empty:
            school_name = school_data["school_name"].iloc[0]

    # 선택 컬럼으로 시계열 + 동료군 데이터 추출
    cols = [c for c in req.columns if c in df.columns]
    if not cols:
        cols = ["student_count", "teacher_count"]

    years = sorted(filtered["year"].unique())
    display_cols = ["school_name", "year"] + cols

    # 결과 테이블 구축
    if req.school_code and school_name:
        # 해당 학교의 시계열
        school_rows = filtered[filtered["school_code"] == req.school_code][display_cols].sort_values("year")
        # 동료군 평균
        peer_rows = []
        for yr in years:
            yr_data = filtered[filtered["year"] == yr]
            row = {"school_name": "동료군 평균", "year": int(yr)}
            for c in cols:
                if c in yr_data.columns:
                    val = yr_data[c].mean()
                    row[c] = round(float(val), 1) if pd.notna(val) else None
            peer_rows.append(row)
        result_data = school_rows.to_dict(orient="records") + peer_rows
    else:
        # 전체 학교 비교
        result_data = filtered[display_cols].sort_values(["year", "school_name"]).head(30).to_dict(orient="records")

    # 변동률 계산
    anomalies = []
    if req.school_code:
        school_sorted = filtered[filtered["school_code"] == req.school_code].sort_values("year")
        for c in cols:
            vals = school_sorted[c].dropna().values
            if len(vals) >= 2:
                change = (vals[-1] - vals[-2]) / vals[-2] * 100 if vals[-2] != 0 else 0
                if abs(change) >= 10:
                    col_label = next((lbl for lbl, key in TABLE_METRICS if key == c), c)
                    anomalies.append(f"{col_label} 전년대비 {change:+.1f}%")

    # LLM 해석 (결과 데이터 기반, 코드 생성 아님)
    client = app_state.get("gemini")
    ai_result = {"해석": "", "정상사유": "", "확인권장": ""}
    if client:
        try:
            data_summary = json.dumps(result_data[:8], ensure_ascii=False, default=str)
            col_labels = [next((lbl for lbl, key in TABLE_METRICS if key == c), c) for c in cols]
            anomaly_text = ", ".join(anomalies) if anomalies else "특이사항 없음"

            target_label = school_name if school_name else "전체 학교 비교"
            prompt = f"""{target_label}의 {', '.join(col_labels)} 분석 결과:
데이터: {data_summary}
변동률 신호: {anomaly_text}

정확히 3줄만 응답:
해석: (핵심 발견 1~2문장)
정상사유: (가능한 이유 쉼표 구분)
확인권장: (담당자 행동 1문장)

주의: "이상치", "비정상" 단정 표현 금지. 학교명은 입력 데이터의 실제 학교명만 사용. "OO고등학교", "[학교명]", "A고등학교" 같은 플레이스홀더 금지."""
            text = _call_gemini(client, prompt)
            for line in text.strip().split("\n"):
                line = line.strip()
                if line.startswith("해석:"):
                    ai_result["해석"] = line[3:].strip()
                elif "정상" in line and ":" in line:
                    ai_result["정상사유"] = line.split(":", 1)[1].strip()
                elif "확인" in line and ":" in line:
                    ai_result["확인권장"] = line.split(":", 1)[1].strip()
            if not ai_result["해석"]:
                ai_result["해석"] = text.strip()[:200]
        except Exception as e:
            # 사용자 노출 문구는 통일된 폴백으로. 개발자 메시지는 서버 로그로만.
            print(f"[WARN] custom_analysis AI 해석 실패: {e}")
            ai_result["해석"] = FALLBACK_AI_TEXT

    # 결과 데이터 숫자 정리
    for row in result_data:
        for k, v in row.items():
            if isinstance(v, float):
                row[k] = round(v, 1) if v != int(v) else int(v)

    # 이상값 셀 위치 계산 (10%+ 변동)
    highlight_cells = []
    if req.school_code and len(years) >= 2:
        school_sorted = filtered[filtered["school_code"] == req.school_code].sort_values("year")
        for c in cols:
            vals = school_sorted[c].dropna().values
            col_label = next((lbl for lbl, key in TABLE_METRICS if key == c), c)
            if len(vals) >= 2:
                for j in range(1, len(vals)):
                    if vals[j-1] != 0:
                        chg = abs((vals[j] - vals[j-1]) / vals[j-1] * 100)
                        if chg >= 10:
                            highlight_cells.append({"row": j, "col": col_label})

    return {
        "columns": [next((lbl for lbl, key in TABLE_METRICS if key == c), c) for c in cols],
        "result_data": _rename_cols_ko(result_data),
        "anomalies": anomalies,
        "highlight_cells": highlight_cells,
        "ai": ai_result,
        "school_name": school_name,
        "confidence": "높음" if anomalies else "중간",
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat_explore(req: ChatRequest):
    """자연어 챗봇. 어떤 실패에서도 500 대신 안전한 ChatResponse 반환."""
    try:
        return await _chat_explore_impl(req)
    except Exception as e:
        # 어떤 단계에서든 예측 못한 예외가 나도 사용자에게 500 노출 금지.
        # (예: 비교 질의에서 MultiIndex/groupby 결과 직렬화 실패 등)
        import traceback
        print(f"[ERROR] /api/chat 미처리 예외: {type(e).__name__}: {e}")
        traceback.print_exc()
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())


async def _chat_explore_impl(req: ChatRequest):
    df_full = app_state.get("df", pd.DataFrame())
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())

    if df_full.empty:
        _log_route("fallback_help", req.query)
        return _chat_fallback_response("데이터가 아직 로드되지 않았습니다.", req.query)

    # ── deterministic guards (Gemini 호출 전 1차 분기) ──
    # 의도가 명확하고 LLM이 답할 게 없는 입력만 가로챔. 애매하면 Gemini로.
    # 학교 컨텍스트가 없을 때만 적용 (학교 상세 안에서의 후속 질문은 모두 LLM으로).
    if not req.school_code:
        # 1) 인사 + 무의미 입력
        gr = _handle_greeting_query(req.query)
        if gr is not None:
            _log_route(gr["plan"]["analysis_plan"].split(": ")[-1], req.query)
            return ChatResponse(**gr)
        # 2) 감사/종료
        th = _handle_thanks_query(req.query)
        if th is not None:
            _log_route("thanks", req.query)
            return ChatResponse(**th)
        # 3) 사용법/정체성
        hp = _handle_help_query(req.query)
        if hp is not None:
            _log_route("help", req.query)
            return ChatResponse(**hp)
        # 4) 데이터 범위 밖 — 무관 의도(날씨·주식 등)
        rg = _handle_range_guard_query(req.query)
        if rg is not None:
            _log_route("range_guard", req.query)
            return ChatResponse(**rg)
        # 4-1) 범위 밖 — 서울 외 시·도
        rgeo = _handle_out_of_region(req.query)
        if rgeo is not None:
            _log_route("range_geo", req.query)
            return ChatResponse(**rgeo)
        # 4-2) 범위 밖 — 학교급 (초·중·특수·대학·특목고)
        rgrade = _handle_out_of_grade(req.query)
        if rgrade is not None:
            _log_route("range_grade", req.query)
            return ChatResponse(**rgrade)
        # 5) 정의/설명 (★priority보다 먼저 — "검토 우선도 지수가 뭐야"가 우선순위표로 새지 않게)
        defn = _handle_definition_query(req.query)
        if defn is not None:
            _log_route("definition", req.query)
            return ChatResponse(**defn)
        # 5-1) 룰 ID 탐지 조회 — "C5 걸린 학교" 등 (단순 필터만, 조건 붙으면 LLM)
        rl = _handle_rule_lookup_query(req.query, df_full, scores, detections)
        if rl is not None:
            _log_route("rule_lookup", req.query)
            return ChatResponse(**rl)
        # 6) 우선순위/Top/N위 — scores/detections로 직접 응답
        # history를 함께 넘겨 멀티턴 후속("그 중 제일 높은") 가로채기 방지.
        pri = _handle_priority_query(req.query, df_full, scores, detections, history=req.history)
        if pri is not None:
            _log_route("priority", req.query)
            return ChatResponse(**pri)

    # 학교 컨텍스트 필터
    df = df_full
    if req.school_code:
        df = df[df["school_code"] == req.school_code].copy()
        if df.empty:
            _log_route("fallback_help", req.query)
            return _chat_fallback_response(f"학교 코드 {req.school_code}의 데이터를 찾지 못했습니다.", req.query)

    # 학교 종합점수(score)·순위(rank)는 별도 scores 테이블에 있어 df에 없다.
    # 멀티턴 후속("그 중 점수 제일 높은")에서 LLM이 df['score']로 코드 만들면 KeyError 발생.
    # → LLM 실행용 df에 scores를 left-merge해서 'score'·'rank' 노출 (원본 df_full은 미오염).
    if scores is not None and not scores.empty and "score" not in df.columns:
        try:
            df = df.merge(
                scores[["school_code", "score", "rank"]],
                on="school_code", how="left",
            )
        except Exception as e:
            print(f"[WARN] scores merge into df 실패(무시): {e}")

    columns_desc = app_state.get("columns", {})
    client = app_state.get("gemini")

    if not client:
        _log_route("fallback_help", req.query)
        return _fallback_analysis(req.query, df, columns_desc)

    # 학교명 조회 (실명만)
    school_name_for_report = ""
    if req.school_code:
        sn = df_full[df_full["school_code"] == req.school_code]["school_name"]
        if not sn.empty:
            school_name_for_report = str(sn.iloc[0])

    # ── 1단계: 분석 계획 ──
    # rule_lookup 가드는 통과했지만(복합 조건이라) 룰 식별자가 있으면
    # 그 룰의 detections 컨텍스트를 LLM prompt에 동봉 — 외부 지식 답변 방지.
    rule_ctx = None
    if not req.school_code:
        try:
            rule_ctx = _extract_rule_context(req.query, df_full, scores, detections,
                                             history=req.history)
        except Exception as e:
            print(f"[WARN] _extract_rule_context 실패(무시): {e}")
            rule_ctx = None
    # 학교 상세 챗봇 — 그 학교의 메타·점수·탐지 룰을 LLM에 명시.
    # "유사학교?" / "왜 검토대상?" / "1분 브리핑" 같은 짧은 맥락 의존 질의가
    # 학교 컨텍스트 없이 의도를 못 잡고 fallback으로 떨어지지 않도록.
    school_ctx = None
    if req.school_code:
        try:
            school_ctx = _build_school_context(req.school_code, df_full, scores, detections)
        except Exception as e:
            print(f"[WARN] _build_school_context 실패(무시): {e}")
            school_ctx = None
    try:
        plan = _get_analysis_plan(client, req.query, columns_desc, req.history,
                                  rule_context=rule_ctx, school_context=school_ctx)
    except GeminiError as e:
        print(f"[WARN] chat plan Gemini 실패: {e}")
        _log_route("fallback_help", req.query)
        return _chat_fallback_response(FALLBACK_AI_TEXT, req.query)

    # JSON 파싱 실패(plan is None) — 기본 코드 fallback 폐기, 안내 응답으로
    if plan is None:
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())

    # ── 2단계: 안전 실행 ──
    # 정책: 기본 데이터표 fallback 제거. 실행 실패는 안내 응답으로, 정상 빈 결과는 별도 안내.
    code = plan.get("pandas_code", "").strip()
    if not code:
        # LLM이 pandas_code를 못 만든 경우 — 분석 의도 불명확
        print(f"[WARN] pandas_code 비어 있음. query='{req.query[:60]}'")
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())

    try:
        result_df = safe_execute(code, df)
    except Exception as e:
        # 코드 실행 실패 — LLM이 매번 다른 형태로 잘못된 코드를 만드는 비결정성.
        # 안전 폴백 우선순위:
        #   (1) 학교 상세: 그 학교 최신 1행으로 채워 report 텍스트로 답변
        #   (2) rule_context 있음: schools 상위 N행을 fallback으로 (멀티턴 후속 '그 중 1위' 등)
        # 둘 다 안 되면 안내 응답.
        print(f"[WARN] pandas_code 실행 실패: {e}")
        fallback_df = None
        if req.school_code and not df.empty:
            try:
                fallback_df = df.sort_values("year").tail(1).copy()
                plan["analysis_plan"] = (plan.get("analysis_plan") or "") + " (코드 실행 실패 → 학교 최신 1행 안전 폴백)"
            except Exception as e2:
                print(f"[WARN] school 폴백 1행 추출 실패: {e2}")
        elif rule_ctx and (rule_ctx.get("schools") or []):
            try:
                schools = rule_ctx["schools"]
                codes = [s["school_code"] for s in schools[:5]]
                fb = df_full[df_full["school_code"].astype(str).isin([str(c) for c in codes])]
                if not fb.empty:
                    fb = fb.sort_values(["school_code", "year"]).groupby("school_code", as_index=False).tail(1)
                    code_order = {str(c): i for i, c in enumerate(codes)}
                    fb = fb.assign(_ord=fb["school_code"].astype(str).map(code_order)).sort_values("_ord").drop(columns=["_ord"])
                    if scores is not None and not scores.empty and "score" not in fb.columns:
                        fb = fb.merge(scores[["school_code","score","rank"]], on="school_code", how="left")
                    fallback_df = fb
                    plan["analysis_plan"] = (plan.get("analysis_plan") or "") + " (코드 실행 실패 → 룰 컨텍스트 상위 학교 안전 폴백)"
            except Exception as e2:
                print(f"[WARN] rule_context 폴백 실패: {e2}")
        if fallback_df is not None and not fallback_df.empty:
            result_df = fallback_df
            plan["confidence"] = "중간"
        else:
            _log_route("fallback_help", req.query)
            return ChatResponse(**_build_fallback_help_response())

    # ── 3단계: 결과 정리 (가명 컬럼 제거) ──
    is_empty_df = isinstance(result_df, pd.DataFrame) and result_df.empty
    if isinstance(result_df, pd.DataFrame):
        # school_name_anon이 결과에 섞여 들어오면 제거 (실명 정책)
        if "school_name_anon" in result_df.columns:
            result_df = result_df.drop(columns=["school_name_anon"])
        result_data_raw = result_df.head(20).to_dict(orient="records")
    else:
        result_data_raw = []
    result_data = _rename_cols_ko(result_data_raw)

    # 정상 실행 + 결과 0건 → "실패"가 아닌 정상 빈 결과 안내. 기본 데이터표 절대 X.
    if is_empty_df or not result_data:
        _log_route("empty_result", req.query)
        return ChatResponse(**_build_empty_result_response(plan))

    # ── 4단계: 보고서 생성 (실패 시 폴백) ──
    try:
        report = _generate_report(client, plan, result_data, req.query, school_name_for_report)
    except GeminiError as e:
        print(f"[WARN] chat report Gemini 실패: {e}")
        report = FALLBACK_AI_TEXT
        plan["confidence"] = "중간"

    suggestions = _generate_suggestions(plan, result_data)
    _log_route("llm", req.query)

    return ChatResponse(
        plan=plan, result_data=result_data,
        report=report,
        confidence=plan.get("confidence", "중간"),
        follow_up_suggestions=suggestions,
        sixbox=None,
    )


def _build_fallback_help_response() -> dict:
    """LLM이 코드 못 만들거나 실행 실패 — 안내 응답. 기본 데이터표 X."""
    return {
        "plan": {
            "analysis_plan": "코드 생성/실행 단계에서 의도를 확정하지 못함",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "낮음",
        },
        "result_data": [],
        "report": (
            "질문을 이해하지 못했습니다. **학교명·지역·룰 ID·검토 신호** 중심으로 물어봐 주세요.\n\n"
            "예: '노원구에서 학생수가 줄어든 학교' / '강남구 검토 우선도 1위 학교는?' / 'C5-1 진급 이탈 잡힌 학교'"
        ),
        "confidence": "낮음",
        "follow_up_suggestions": _EXAMPLE_SUGGESTIONS,
        "sixbox": None,
    }


def _build_empty_result_response(plan: dict) -> dict:
    """정상 실행됐지만 결과 0건 — 실패 아님. 조건 조정 안내."""
    return {
        "plan": plan,
        "result_data": [],
        "report": "조건에 맞는 학교가 없습니다. 조건을 조금 넓혀 다시 확인해 주세요.",
        "confidence": "높음",
        "follow_up_suggestions": _EXAMPLE_SUGGESTIONS,
        "sixbox": None,
    }


def _chat_fallback_response(message: str, query: str) -> ChatResponse:
    """챗봇 응답 안전 폴백 (500 방지)"""
    return ChatResponse(
        plan={
            "analysis_plan": "AI 응답 지연·실패로 인한 안전 폴백",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "낮음",
        },
        result_data=[],
        report=message,
        confidence="낮음",
        follow_up_suggestions=[
            "검토 우선 후보를 다시 보여줘",
            "우선 검토 신호만 요약해줘",
            "강남구 검토 후보만 보여줘",
        ],
        sixbox=None,
    )


# ──────────────────────────────────────────────────────────────
# 챗봇 라우팅 / 가드 (Gemini 호출 전 deterministic 분기)
# ──────────────────────────────────────────────────────────────
# 라우팅 로그/카운터 — 내부용. 사용자 화면 노출 X.
# 나중에 "LLM 호출 없이 처리한 질의 비율" 설명용으로 카운트만 남김.
ROUTE_COUNTER: dict = {}


def _log_route(route: str, query: str):
    """라우팅 단계 카운트 + 로그 한 줄."""
    ROUTE_COUNTER[route] = ROUTE_COUNTER.get(route, 0) + 1
    safe = (query or "").replace("\n", " ")[:60]
    print(f"[CHAT] route={route} · query='{safe}'")


# 인사·감사·도움말은 "전체 메시지가 그 의도일 때만" 처리.
# substring 매칭으로 잡으면 "안녕하세요, 개포고 학생수 알려줘"도 잡혀 실제 질의가 사라짐.
# → 접두어 strip 후 남은 실질 질의가 없을 때만 가드 적용.

_GREETING_PREFIXES = (
    "안녕하세요", "안녕히 계세요", "안녕히 가세요", "안녕",
    "반가워요", "반갑습니다", "반가워", "반갑",
    "하이요", "하이", "ㅎㅇ", "hi", "hello", "hey",
    "방가워요", "방가",
    "굿모닝", "굿이브닝", "굿밤",
)
_THANKS_PREFIXES = (
    "고맙습니다", "고마워요", "고마워", "고맙",
    "감사합니다", "감사해요", "감사",
    "수고하셨습니다", "수고하셨어요", "수고하세요", "수고했어", "수고",
    "잘 가", "잘가", "잘 있어", "안녕히",
    "thank you", "thanks", "thx", "ty",
    "ㄳ", "ㄱㅅ",
    "굿굿", "굳굳", "good", "굿",
)
_HELP_PHRASES = (
    "넌 누구", "너는 누구", "당신은 누구", "넌 뭐", "너 뭐",
    "뭐 할 수 있", "뭐할 수 있", "뭐가 가능", "할 수 있는 게",
    "사용법", "어떻게 써", "어떻게 사용", "어떻게 쓰", "어떻게 동작",
    "도움말", "헬프", "help", "사용 방법",
    "어떤 질문", "어떤거 물어", "뭐 물어",
    "넌 뭐야", "너는 뭐야", "정체",
    # 보강
    "무슨 서비스", "어떤 서비스", "뭐하는 거", "뭐 하는 거", "뭐 하는거",
    "이거 뭐", "이거 뭐임", "이게 뭐",
)
# 의미 없는 짧은 입력 — 멀티턴 컨텍스트는 안 쓰므로 "?" 단독도 무의미 입력.
_NOISE_INPUTS = (
    "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "ㅎㅎㅎ", "ㅠㅠ", "ㅠ", "ㅗㅜ",
    "ㅇㅇ", "응", "넵", "넹",
    "...", "..", ".", "?", "??", "???",
    "ㅁㄴㅇ", "ㅁㄴㅇㄹ", "ㄱㄴㄷ",
    # 키보드 매시 패턴 — 사전에 없는 랜덤 영문/문자열
    "asdf", "asdfasdf", "qwer", "qwerty", "zxcv", "zxcvbnm", "aaaa", "test", "test123", "1234", "12345",
)

# 데이터 범위 밖 키워드 — 명백히 공시·교육 데이터와 무관한 질의
_OUT_OF_SCOPE_KEYWORDS = (
    "날씨", "기온", "비 와", "눈 와",
    "주식", "코인", "비트코인", "환율", "금리",
    "오늘 몇", "지금 몇 시", "지금 시각",
    "요리", "레시피", "음식 추천", "맛집",
    "노래 추천", "영화 추천", "드라마 추천",
    "축구", "야구", "농구",
    "번역", "영어로", "한자로",
    "코딩", "프로그래밍", "파이썬", "javascript",
    "친구", "연애", "사주", "운세",
)


def _strip_prefixes(q_low: str, prefixes: tuple) -> str:
    """질의에서 인사/감사 접두어를 길이 긴 순으로 한 번 제거. 남은 문자열 반환."""
    candidates = sorted(prefixes, key=lambda p: -len(p))
    for kw in candidates:
        if q_low.startswith(kw.lower()):
            rest = q_low[len(kw):].lstrip(" ,.!?~^^。，！？")
            return rest
    return q_low


def _is_noise_only(q: str) -> bool:
    """무의미 입력(이모티콘·자음 반복·랜덤 키보드 매시) 단독 여부."""
    if not q:
        return True
    if q.lower() in (n.lower() for n in _NOISE_INPUTS):
        return True
    # 한글 자모/문장부호만 있고 의미 있는 글자 없는 경우
    if len(q) <= 3 and not any(c.isalnum() and ord(c) > 127 for c in q) and not any(c.isalnum() and c.isascii() for c in q):
        if not any('가' <= c <= '힣' for c in q):
            return True
    # 사전에 없는 랜덤 영문/숫자 단독 (한글 음절 없고 의미 키워드 아닐 때)
    rest = q.strip().lower()
    has_korean = any('가' <= c <= '힣' for c in q)
    if not has_korean and 1 <= len(rest) <= 6 and all(c.isalnum() or c in " " for c in rest):
        # 이미 greeting/thanks 가드에서 처리되는 것은 미리 거름
        meaningful = ("hi", "hello", "hey", "ok", "yes", "no",
                      "thx", "ty", "good", "thanks", "thank you", "help")
        if rest not in meaningful and not any(rest.startswith(m) for m in meaningful):
            # 자음/모음만이거나 같은 글자 반복은 노이즈
            if len(set(rest)) <= 2 or rest in ("asdf", "qwer", "zxcv", "qwerty", "asdfasdf", "qwertyuiop", "test", "test123", "1234", "12345", "123456"):
                return True
    return False


def _build_simple_response(report: str, suggestions: list, route: str) -> dict:
    """공통 안내 응답 빌더 — guard 분기 공용."""
    return {
        "plan": {
            "analysis_plan": f"deterministic guard: {route}",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "높음",
        },
        "result_data": [],
        "report": report,
        "confidence": "높음",
        "follow_up_suggestions": suggestions,
        "sixbox": None,
    }


_EXAMPLE_SUGGESTIONS = [
    "강남구 검토 우선도 1위 학교는?",
    "우선 검토 신호만 요약해줘",
    "노원구에서 학생수가 줄어든 학교",
    "학교폭력 조치 확인 신호가 있는 학교는?",
    "교원수가 3년 연속 감소한 학교",
    "급식비가 30% 이상 오른 학교",
    "C5-1 룰 설명해줘",
    "검토 우선도 카테고리 종류 알려줘",
]


def _handle_greeting_query(query: str):
    """인사 가드 — 전체 메시지가 인사이거나, 인사 접두어 제거 후 남은 질의가 없을 때만 처리.
    'ㅋㅋ' 같은 무의미 입력도 함께 처리."""
    q = (query or "").strip()
    if not q:
        return None
    # 1) 무의미 입력 단독
    if _is_noise_only(q):
        report = (
            "메시지가 비어 있거나 인식하지 못했습니다. 공시 데이터에 대해 질문해 주세요.\n\n"
            "예: 노원구에서 학생수가 줄어든 학교 / 강남구 검토 우선도 1위 학교는?"
        )
        return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="empty_input")
    # 2) 인사 접두어 strip → 남은 질의 있으면 greeting 아님
    rest = _strip_prefixes(q.lower(), _GREETING_PREFIXES)
    if rest:
        return None
    report = (
        "안녕하세요. **EduData Watch 공시 데이터 챗봇**입니다.\n\n"
        "공시 데이터에 자연어로 질문해 보세요. 예시:\n"
        "- 강남구 검토 우선도 1위 학교는?\n"
        "- 우선 검토 신호만 요약해줘\n"
        "- 노원구에서 학생수가 줄어든 학교\n"
        "- 학교 상세 화면에서 이 학교 1분 브리핑 생성\n\n"
        "본 챗봇은 판정하지 않고 확인을 돕습니다. 자유롭게 물어봐 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="greeting")


def _handle_thanks_query(query: str):
    """감사/종료 — 전체 메시지가 감사 의도일 때만."""
    q = (query or "").strip()
    if not q or len(q) > 30:
        return None
    rest = _strip_prefixes(q.lower(), _THANKS_PREFIXES)
    if rest:
        return None
    report = "도움이 됐다면 다행입니다. 추가로 확인이 필요한 학교나 검토 신호가 있으면 언제든 물어봐 주세요."
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="thanks")


def _handle_help_query(query: str):
    """사용법/정체성 안내 — 핵심 문구 substring 매칭 (이건 의도가 명확해서 substring OK)."""
    q = (query or "").strip()
    if not q or len(q) > 40:
        return None
    q_low = q.lower()
    if not any(p.lower() in q_low for p in _HELP_PHRASES):
        return None
    report = (
        "**EduData Watch 공시 데이터 챗봇**입니다.\n\n"
        "교육 공시 데이터(서울 3구 일반고 42교·2023~2025년)에 자연어로 질문하면, "
        "결정론적 룰셋과 데이터 분석으로 검토 후보를 보여드립니다.\n\n"
        "물어볼 수 있는 것:\n"
        "- 학교명·구·룰 ID·검토 우선도 기준 조회\n"
        "- 학생수·교원수·학폭·진학률·급식비 등 지표 변화\n"
        "- 동료군(같은 구) 비교\n"
        "- 우선순위·Top N\n\n"
        "본 챗봇은 판정하지 않고 확인을 돕습니다."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="help")


def _handle_range_guard_query(query: str):
    """공시 데이터 범위 밖 — 명백히 무관한 의도 (날씨·주식·시간 등)만 가드."""
    q = (query or "").strip()
    if not q:
        return None
    q_low = q.lower()
    if not any(k in q_low for k in _OUT_OF_SCOPE_KEYWORDS):
        return None
    report = (
        "본 챗봇은 **교육 공시 데이터** 분석 도구입니다.\n\n"
        "현재 표본 범위: 서울 **강남·노원·관악구** 일반고 **42교**, **2023~2025년** 공시.\n\n"
        "이 범위 안에서 학교·지표·검토 신호에 대해 질문해 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="range_guard")


@app.get("/api/chat/route-stats")
async def chat_route_stats():
    """챗봇 라우팅 카운터 (내부용). 사용자 화면 노출 X.
    LLM 호출 없이 처리한 비율 등을 발표·심사 자료로 활용.

    분류:
    - pre_llm_guard: Gemini 호출 전 deterministic 분기 (greeting/thanks/help/range_guard/priority/empty_input)
    - llm_post: Gemini 거친 응답 (llm 정상 + empty_result 정상 빈결과 + fallback_help 실패안내)
    """
    total = sum(ROUTE_COUNTER.values()) or 0
    by_route = dict(sorted(ROUTE_COUNTER.items(), key=lambda kv: -kv[1]))

    pre_llm_routes = ("greeting", "thanks", "help", "priority",
                      "range_guard", "range_geo", "range_grade",
                      "definition", "rule_lookup", "empty_input")
    pre_llm = sum(ROUTE_COUNTER.get(r, 0) for r in pre_llm_routes)

    llm_post_routes = ("llm", "empty_result", "fallback_help")
    llm_post = sum(ROUTE_COUNTER.get(r, 0) for r in llm_post_routes)

    return {
        "total": total,
        "by_route": by_route,
        "pre_llm_guard_count": pre_llm,
        "llm_post_count": llm_post,
        "pre_llm_ratio": round(pre_llm / total, 3) if total else 0.0,
        "llm_post_ratio": round(llm_post / total, 3) if total else 0.0,
        # 세부 (LLM 거친 것 중 정상 vs 빈 결과 vs 실패)
        "llm_breakdown": {
            "llm_success": ROUTE_COUNTER.get("llm", 0),
            "empty_result": ROUTE_COUNTER.get("empty_result", 0),
            "fallback_help": ROUTE_COUNTER.get("fallback_help", 0),
        },
    }


# ── 우선순위·Top 질의는 LLM 우회, scores/detections로 직접 응답 ──
_PRIORITY_KEYWORDS = (
    "우선순위", "가장 높은", "가장 우선", "최우선", "상위",
    "top", "TOP", "Top", "1위",
    "검토 우선",
    # 보강
    "점수 높은", "점수가 높은", "제일 높은", "제일높은", "최고점", "최고 점수",
    "젤 높은", "젤높은",
)
# 서울 25개 자치구 — 챗봇 필터·범위 가드 공용 단일 출처.
# ※ '중'·'동'·'강'처럼 짧고 일반어와 겹치는 자치구명은 substring 매칭하지 말 것.
#   분리: _DISTRICTS_LONG(2자 이상, 안전) vs _DISTRICTS_SHORT(1자, 단어경계 필요).
_DISTRICTS_LONG = (
    "강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천",
    "노원", "도봉", "동대문", "동작", "마포", "서대문", "서초", "성동",
    "성북", "송파", "양천", "영등포", "용산", "은평", "종로", "중랑",
)
# 1자 자치구는 별도 — '~구'·'~구의'·'~ 지역'·'~ 일반고' 형태일 때만 자치구로 본다.
_DISTRICTS_SHORT = ("중",)
# 전체 합 — 표시·서울 힌트 등 substring 무관 용도에만 사용.
_DISTRICTS_KO = _DISTRICTS_LONG + _DISTRICTS_SHORT


def _district_in_query(query: str) -> str | None:
    """쿼리에서 자치구를 정확히 식별. 1자 자치구는 단어경계로 검사.
    매칭 우선순위: long(substring OK) → short(단어경계).
    예: '강남구 학교' → '강남' / '중구 학교' → '중' / '전체 중 제일 높은' → None."""
    q = (query or "").strip()
    if not q:
        return None
    for d in _DISTRICTS_LONG:
        if d in q:
            return d
    # 1자 자치구 — '중' 뒤에 '구·구의·구에서·구만· 지역· 일반고· 학교' 등이 와야 함.
    # 또는 단독 '중' 으로 '중에서 / 중에 있는 / 중에서만' 등 조사 용법은 자치구 아님.
    for d in _DISTRICTS_SHORT:
        # 패턴: '중구' / '중 지역' / '중 일반고' / '중 학교' 만 자치구
        if re.search(rf"\b{d}구\b", q) or re.search(rf"\b{d}\s*(?:지역|일반고|고등학교)\b", q):
            return d
    return None


# ── 범위 밖 (지역/학교급) 가드 ──
# 표본: 서울 25개 자치구 일반고 210교. 그 외 시도·학교급 질의는 안내로 끝.
# ※ 시·도명은 substring 매칭하지 말 것 — '경기고'의 '경기', '대구고'의 '대구' 오탐.
#   학교명(메인 DataFrame의 school_name)과 매칭되는 토큰이 있으면 학교 질의로 보고 통과.
_OUT_OF_REGION_KEYWORDS = (
    "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "수원", "성남", "고양", "용인", "안양", "안산", "부천", "화성",
    # 전국 범위 — 서울만 다루는 표본보다 넓음
    "전국", "전국적",
)
# 지역명 뒤에 일반어("학교/고등학교/일반고/도/시/지역/광역시")가 따라올 때만 지역 의도.
_REGION_TAIL_TOKENS = ("학교", "고등학교", "일반고", "교육청", "지역", "도", "광역시", "특별시", "특별자치도", "특별자치시")
_SEOUL_HINTS = _DISTRICTS_LONG + _DISTRICTS_SHORT + ("서울",)
_OUT_OF_GRADE_KEYWORDS = (
    "초등학교", "초등생", "초딩",
    "중학교", "중학생", "중딩",
    "유치원", "어린이집",
    "특수학교", "대학교", "대학원",
    # 특수목적 / 자율형 — 일반고 외
    "특성화고", "마이스터고", "외국어고", "외고", "과학고", "과고",
    "예술고", "예고", "체육고", "체고", "자사고", "자율형",
)


_SCHOOL_NAME_PARTICLES = ("는", "은", "이", "가", "을", "를", "의", "에", "에서", "에선", "도", "만", "랑", "와", "과", "로", "으로")
_REGION_PLUS_GO_RE = re.compile(
    r"(?:" + "|".join(re.escape(k) for k in (
        "부산", "대구", "인천", "광주", "대전", "울산", "세종",
        "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    )) + r")고(?=\s|$|[?!.,]|" + "|".join(re.escape(p) for p in _SCHOOL_NAME_PARTICLES) + r")"
)


def _query_school_in_data(query: str) -> bool:
    """쿼리에 메인 DataFrame의 실제 학교명(또는 ~고 줄임형)이 있으면 True.
    표본 안에서 해결 가능한 학교 의도 — priority/rule_lookup 등 표본 기반 응답이 정당."""
    df = app_state.get("df", pd.DataFrame())
    if df.empty or "school_name" not in df.columns:
        return False
    q = (query or "").strip()
    if not q:
        return False
    for name in df["school_name"].unique():
        n = str(name)
        if not n:
            continue
        if n in q:
            return True
        if n.endswith("등학교"):
            short = n[:-3]  # '경기고등학교' → '경기고'
            if short and short in q:
                return True
    return False


def _looks_like_school_name(query: str) -> bool:
    """입력에 학교명 패턴이 있으면 True. 두 경로:
    (1) 메인 DataFrame의 실제 학교명/그 줄임형(~고) 매칭 — 현재 데이터
    (2) 시·도명+'고' (예: 부산고/대구고/강원고) — 전국 확장 대비 (데이터 없어도 학교 의도)
    데이터 미로드 상태에서도 (2)는 동작."""
    q = (query or "").strip()
    if not q:
        return False
    # (2) 시도명+고 패턴 (확장 대비)
    if _REGION_PLUS_GO_RE.search(q):
        return True
    # (1) 메인 DataFrame 학교명 (현재 표본)
    df = app_state.get("df", pd.DataFrame())
    if df.empty or "school_name" not in df.columns:
        return False
    for name in df["school_name"].unique():
        n = str(name)
        if not n:
            continue
        if n in q:
            return True
        if n.endswith("등학교"):
            short = n[:-3]  # '경기고등학교' → '경기고'
            if short and short in q:
                return True
    return False


def _handle_out_of_region(query: str):
    """서울 외 시·도명이 학교 일반어와 함께 등장하면 표본 범위 안내.
    ※ 학교명(known schools)이 있으면 학교 질의로 보고 통과.
    ※ 시·도 단독 + 학교 일반어가 있을 때만 지역 의도로 본다."""
    q = (query or "").strip()
    if not q:
        return None
    # 1) 학교명이 있으면 지역 guard 통과 (경기고/대구고 한 버그)
    if _looks_like_school_name(q):
        return None
    # 2) 시·도명 매칭
    matched = [k for k in _OUT_OF_REGION_KEYWORDS if k in q]
    if not matched:
        return None
    # 3) 서울 힌트가 있으면 지역 guard 통과 (자치구 SHORT은 단어경계로)
    if any(s in q for s in _DISTRICTS_LONG) or _district_in_query(q) or "서울" in q:
        return None
    # 4) 지역 의도 확정 — 시·도명 + 학교 일반어 OR 시·도명 단독(or 단독+1~2 조사).
    has_region_tail = any(t in q for t in _REGION_TAIL_TOKENS)
    # 단독 매칭 — q.strip()이 시·도명과 동일하거나 시·도명+1~2자(조사) 정도일 때만.
    # "부산 보고", "경기 참고" 같은 짧은 일반 문장은 통과.
    q_stripped = q.strip()
    is_region_solo = any(
        q_stripped == m or
        re.fullmatch(rf"{re.escape(m)}[은는이가의에도]{{0,2}}", q_stripped) or
        re.fullmatch(rf"{re.escape(m)}\s*(?:은요|는요|이요|가요)?[?!.,]?", q_stripped)
        for m in matched
    )
    # 전국 계열 키워드는 단독·접사("전국적인"·"전국적") 모두 표본 범위 안내 대상.
    is_national = any(("전국" in m) for m in matched)
    if not (has_region_tail or is_region_solo or is_national):
        return None
    report = (
        "본 도구는 **서울 일반고**만 다룹니다.\n\n"
        "현재 표본: 서울 25개 자치구 일반고 **210교** · 2023~2025년 공시.\n\n"
        "표본 범위 안의 학교·지표·검토 신호에 대해 다시 물어봐 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="range_geo")


def _handle_out_of_grade(query: str):
    """초·중·특수·대학·특목고 등 일반고 외 학교급 질의는 표본 범위 안내."""
    q = (query or "").strip()
    if not q:
        return None
    if not any(k in q for k in _OUT_OF_GRADE_KEYWORDS):
        return None
    report = (
        "본 도구는 **일반계 고등학교**만 다룹니다.\n\n"
        "초·중등학교, 특수학교, 대학교, 특수목적 고등학교(외고·과고·자사고 등)는 현재 표본 범위에 포함되지 않습니다.\n\n"
        "서울 일반고 학교·룰·검토 신호에 대해 물어봐 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="range_grade")


# ── 정의/설명 가드 — LLM 호출 없이 정적 응답 (priority 가드보다 먼저) ──
# 분석 동사가 붙으면 "정의"가 아닌 "분석"으로 보고 LLM에 위임.
_ANALYSIS_VERBS = (
    "보여줘", "보여줄", "보여 줘", "보여줄래", "보여봐",
    "리스트", "목록", "list",
    "걸린", "잡힌", "탐지", "검출", "걸려",
    "조회", "찾아", "찾으", "검색",
    "비교", "비교해", "대조",
    "그래프", "차트", "표시해",
    "탑", "1위", "상위", "랭킹", "순위", "1등", "최고점",
    "있는 학교", "어떤 학교", "어디",
    "건수", "몇 개", "몇 교", "몇교", "개수",
)
_DEFINITION_PATTERNS = (
    "뭐야", "뭐임", "뭔지", "뭔가", "뭐냐",
    "무슨", "무엇",
    "뜻이", "뜻은", "뜻", "의미",
    "설명", "정의",
    "기준이", "기준은", "기준",
    "어떻게 정해", "어떻게 계산", "어떻게 산정", "어떻게 매겨", "어떻게 산출",
    "산식", "공식", "산출 방법",
    # 목록/설명 요청형 (분석 동사가 아닐 때만 — 분석 동사 가드와 함께 동작)
    "종류", "어떤 것", "어떤 게", "뭐뭐", "뭐 뭐", "다 알려", "다 보여", "전부",
    "검사 항목", "점검 항목", "어떤 룰들", "어떤 룰이",
)
# 도구 용어 → 정적 글로서리 키
# ※ 금지단어("별점·별 등급·★") 트리거는 유지하되, 응답 텍스트에서 그 단어를 노출하지 않는다.
_GLOSSARY_TERMS = {
    "검토 우선도": "score_system", "우선도 지수": "score_system", "우선도": "score_system",
    "지수": "score_system", "종합점수": "score_system", "종합 점수": "score_system",
    "탐지 점수": "sr",
    "s_r": "sr", "S_school": "score_system",
    "위험도": "risk", "w_d": "risk",
    "초과량": "m_r", "m_r": "m_r",
    "감쇠": "damping",
    # 옛 용어 트리거 — 응답은 현 점수체계로 안내
    "별점": "stars_legacy", "별 등급": "stars_legacy", "별등급": "stars_legacy", "★": "stars_legacy",
    "룰셋": "rules", "룰": "rules",
    "카테고리": "categories", "대분류": "categories",
    "점수": "score_system",
}
_DEFINITION_TEXT = {
    "score_system": (
        "**검토 우선도 (학교 종합 점수)** — 0~100점.\n\n"
        "**산식**: S = 0.6·V + 0.2·C + 0.2·R\n"
        "- V(값): 대분류별 감쇠 후 합산\n"
        "- C(구조): 탐지된 대분류 수 / 9 × 100\n"
        "- R(반복): 같은 룰 3년 연속 = 100\n\n"
        "**라벨 임계**:\n"
        "- 70+ 즉시 검토\n"
        "- 50~70 우선 검토 대상\n"
        "- 30~50 일반 검토\n"
        "- 0~30 참고"
    ),
    "sr": (
        "**s_r (탐지 항목 점수)** — 0~10점.\n\n"
        "**산식**: s_r = 위험도(w_d) × min(2, m_r)\n"
        "- w_d: 룰 위험도 (C3=5, B1·C1·C2·F1'=3, D2·E·C5·G1=2)\n"
        "- m_r: 초과량 계수 (연속/이진/고정/비대칭)"
    ),
    "risk": (
        "**위험도 (w_d)** — 룰 카테고리별 점수 천장.\n\n"
        "- C3 학생 안전: 5 (s_r 천장 10)\n"
        "- B1·C1·C2·F1' 자원·재정·교차: 3 (천장 6)\n"
        "- D2·E·C5·G1 통계·누락·진급·추세: 2 (천장 4)"
    ),
    "m_r": (
        "**초과량 계수 (m_r)** — 탐지 항목이 임계를 얼마나 넘었는지. 0~2 상한.\n\n"
        "**유형 4종**:\n"
        "- 연속형: (|값| - 임계) / 임계\n"
        "- 이진형(규모): 규모 / 최소기준\n"
        "- 고정형: 룰별 상수 (D2-2=1.0, E2-2=0.8 등)\n"
        "- 비대칭(C5-1): 감소·증가 별도 임계"
    ),
    "damping": (
        "**감쇠 (대분류별)** — 같은 대분류 안에서 최고 s_r 1건은 100% 반영, 나머지는 ×0.3.\n\n"
        "한 원인이 여러 룰에 동시 탐지될 때 점수가 부풀려지지 않도록 보정합니다."
    ),
    "stars_legacy": (
        "**검토 우선도 (학교 종합 점수)** — 0~100점.\n\n"
        "현 체계는 탐지 항목별 점수(0~10)와 학교 종합 점수(0~100)로 구성됩니다.\n"
        "산식: S = 0.6·V + 0.2·C + 0.2·R\n\n"
        "**라벨 임계**: 70+ 즉시 검토 / 50~70 우선 검토 대상 / 30~50 일반 검토 / 0~30 참고."
    ),
    "rules": (
        "**룰셋 v4** — 결정론적 임계 기반 25개.\n\n"
        "각 룰은 risk · m_type · 임계 메타로 정의되며, 9개 대분류로 묶입니다: "
        "C1(학생·자원), C2(학생·재정), C3(미조치 피해), B1(전년 대비 급변동), "
        "D2(유사학교 편차), E(누락·미갱신), C5(학년 진급), F1'(교차 불일치), G1(장기 추세).\n\n"
        "특정 룰을 더 자세히 보려면 룰 ID(예: C5-1) 또는 룰명으로 다시 물어봐 주세요."
    ),
    "categories": (
        "**대분류 9개** — 학교 종합 점수 구조 점수 C의 분모.\n\n"
        "- C1 학생·자원 연동 점검\n"
        "- C2 학생·재정 연동 점검\n"
        "- C3 미조치 피해 점검\n"
        "- B1 전년 대비 급변동\n"
        "- D2 유사학교 대비 편차\n"
        "- E 누락·미갱신 점검 (E1+E2 통합)\n"
        "- C5 학년 진급 인원 점검\n"
        "- F1' 연계 시점 차이 점검\n"
        "- G1 장기 추세 점검"
    ),
}


def _definition_rules_full_list() -> str:
    """RULE_META 25개 룰 전체 목록 (활성/비활성 표기)."""
    by_cat: dict = {}
    for rid, m in RULE_META.items():
        cat = m.get("category", "").rstrip("'")
        by_cat.setdefault(cat, []).append(rid)
    lines = ["**룰셋 v4 — 전체 25개 룰**", "", "9개 대분류 · 결정론적 임계 기반 탐지.", ""]
    cat_order = ["C1", "C2", "C3", "B1", "D2", "C5", "E1", "E2", "F1", "G1"]
    for cat in cat_order:
        if cat not in by_cat:
            continue
        ko = CATEGORY_NAMES_KO.get(cat, cat)
        lines.append(f"**[{cat}] {ko}**")
        for rid in by_cat[cat]:
            name = RULE_NAMES_KO.get(rid, rid)
            st = RULE_META.get(rid, {}).get("status", "active")
            mark = "" if st == "active" else f" ({st})"
            lines.append(f"- {rid} {name}{mark}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _definition_categories_full_list() -> str:
    return _DEFINITION_TEXT["categories"]


def _definition_for_rule(rid: str) -> str:
    """RULE_META + RULE_NAMES_KO + SIXBOX_GUIDE로 룰 정적 설명 생성."""
    name = RULE_NAMES_KO.get(rid, rid)
    meta = RULE_META.get(rid, {})
    cat = meta.get("category", "").rstrip("'")
    cat_ko = CATEGORY_NAMES_KO.get(cat, "")
    guide = SIXBOX_GUIDE.get(rid, {})
    pattern = guide.get("pattern", "")
    risk = meta.get("risk", 0)
    m_type = meta.get("m_type", "")
    status = meta.get("status", "active")

    lines = [f"**[{rid}] {name}**"]
    if cat_ko:
        lines.append(f"카테고리: {cat_ko}")
    if pattern:
        lines.append("")
        lines.append(f"**패턴**: {pattern}")
    if risk and m_type:
        lines.append("")
        lines.append(f"**점수**: 위험도 w_d={risk}, m_r 유형={m_type}, s_r 천장 = {risk * 2}점.")
    if status != "active":
        lines.append("")
        lines.append(f"※ 현재 상태: {status} (점수 산정에서 제외).")
    return "\n".join(lines)


def _definition_for_category(code: str) -> str:
    """CATEGORY_NAMES_KO + RULE_META로 카테고리 정적 설명 생성."""
    code_clean = code.rstrip("'")
    ko = CATEGORY_NAMES_KO.get(code_clean, code)
    rules = [(rid, RULE_NAMES_KO.get(rid, rid))
             for rid, m in RULE_META.items()
             if m.get("category", "").rstrip("'") == code_clean]
    risk_set = sorted({m.get("risk", 0) for rid, m in RULE_META.items()
                       if m.get("category", "").rstrip("'") == code_clean and m.get("status") == "active"},
                      reverse=True)
    risk_txt = "/".join(str(r) for r in risk_set) or "-"
    lines = [f"**[{code}] {ko}**", "", f"위험도: {risk_txt}", "", "**포함 룰**:"]
    for rid, name in rules:
        st = RULE_META.get(rid, {}).get("status", "active")
        mark = "" if st == "active" else f" ({st})"
        lines.append(f"- {rid} {name}{mark}")
    return "\n".join(lines)


_LIST_TRIGGER_TERMS = ("종류", "어떤 것", "어떤 게", "뭐뭐", "뭐 뭐",
                       "다 알려", "다 보여", "전부", "검사 항목", "점검 항목",
                       "어떤 룰들", "어떤 룰이")


def _handle_definition_query(query: str):
    """정의·설명 질의 → 정적 응답 (LLM 호출 X). priority guard보다 먼저 통과시킨다.
    경계: 분석 동사(보여줘/걸린/1위/...)가 붙으면 정의 아님 → LLM.
    예외: '검사 항목 전부 보여줘' 같은 목록형은 '보여줘'가 있어도 정적 목록 응답."""
    q = (query or "").strip()
    if not q or len(q) > 80:
        return None
    q_low = q.lower()

    has_list_trigger = any(t in q for t in _LIST_TRIGGER_TERMS)
    # 1) 분석 동사 차단 — 단, 목록형 트리거가 있으면 분석 동사를 무시 (룰/카테고리 전체 목록 응답)
    if not has_list_trigger and any(v.lower() in q_low for v in _ANALYSIS_VERBS):
        return None
    # 2) 정의 패턴 또는 목록형 트리거 필요
    has_def_pattern = any(p in q_low for p in _DEFINITION_PATTERNS)
    if not (has_def_pattern or has_list_trigger):
        return None

    # 2-1) 목록형 ("룰 종류"·"카테고리 종류"·"어떤 룰들 있어"·"검사 항목") → 전체 리스트
    # ★ "검사 항목"/"점검 항목"은 분석 동사(보여줘)와 함께 와도 정의(목록)로 처리
    if has_list_trigger:
        # 카테고리 목록
        if "카테고리" in q or "대분류" in q:
            return _build_simple_response(_definition_categories_full_list(),
                                          _EXAMPLE_SUGGESTIONS, route="definition")
        # 룰 목록 (룰·검사·점검·룰셋)
        if any(kw in q for kw in ("룰", "룰셋", "검사", "점검")):
            return _build_simple_response(_definition_rules_full_list(),
                                          _EXAMPLE_SUGGESTIONS, route="definition")

    # 3-1: 룰 ID 매칭 (대소문자 무관, 더 긴 것 우선)
    for rid in sorted(RULE_NAMES_KO.keys(), key=lambda r: -len(r)):
        if rid.lower() in q_low:
            return _build_simple_response(_definition_for_rule(rid),
                                          _EXAMPLE_SUGGESTIONS, route="definition")
    # 3-2: 룰 한국어명 (괄호 안 보조 설명은 제거하고도 매칭)
    for rid, name in RULE_NAMES_KO.items():
        if not name:
            continue
        name_core = name.split("(")[0].strip()
        if name in q or (name_core and name_core in q):
            return _build_simple_response(_definition_for_rule(rid),
                                          _EXAMPLE_SUGGESTIONS, route="definition")
    # 3-3: 카테고리 코드 또는 한국어명
    for code, ko in CATEGORY_NAMES_KO.items():
        if code in q.split() or (code in q and (q == code or has_def_pattern)):
            if ko in q or len(code) <= 3:
                return _build_simple_response(_definition_for_category(code),
                                              _EXAMPLE_SUGGESTIONS, route="definition")
        if ko and ko in q:
            return _build_simple_response(_definition_for_category(code),
                                          _EXAMPLE_SUGGESTIONS, route="definition")
    # 3-4: 도구 용어 (길이 긴 것 우선)
    for term in sorted(_GLOSSARY_TERMS.keys(), key=lambda t: -len(t)):
        if term.lower() in q_low:
            text_key = _GLOSSARY_TERMS[term]
            return _build_simple_response(_DEFINITION_TEXT[text_key],
                                          _EXAMPLE_SUGGESTIONS, route="definition")
    return None


# ── 룰 ID 탐지 조회 (`rule_lookup`) — "C5 걸린 학교"·"C3-3A 탐지된 학교" 직접 응답 ──
# priority_query와 동일한 패턴으로 LLM 우회. detections에서 rule_id 필터링해 답.
_LOOKUP_VERBS = (
    "걸린", "걸려", "걸렸", "걸린 학교", "걸린 곳", "걸린데", "걸리는",
    "탐지된", "탐지 된", "탐지", "검출된", "검출",
    "잡힌", "잡힌 학교", "포함된",
)
# 추가 조건어가 붙으면 단순 필터로 안 됨 → LLM 위임 (priority guard와 동일 의도)
_LOOKUP_COMPLEX_HINTS = (
    # 정렬·극값
    "많은", "적은", "높은", "낮은", "큰", "작은",
    "제일", "가장", "최대", "최소", "Top", "top", "1위", "상위", "하위",
    "이상인", "초과인", "이하인", "미만인",
    # 비교
    "비교", "대비", "평균", "VS", "vs", " 대 ", "보다",
    # 기간 한정 (연도 4자리 별도 정규식으로 보강)
    "2023년", "2024년", "2025년", "작년", "최근",
    # 교집합·복수 룰 표현
    "둘 다", "둘다", "셋 다", "셋다", "모두", "전부",
    " 그리고 ", " 및 ", " 와 ", " 과 ", " 랑 ", " 이랑 ",
    # 부분 집합 — "중"은 "OO 중 OO" 패턴이 흔함
    "중 ", "중에서",
    # 집계(count) 의도 — 단순 목록 X, LLM 위임
    "몇 개", "몇개", "개수", "총 몇", "총몇", " count", " Count",
)
# 별도 정규식 — 연도 4자리(2020·2021·...) 단독 등장
_LOOKUP_YEAR_RE = re.compile(r"\b20\d{2}\b")
# 후속 지시어 — 직전 턴 컨텍스트를 이어받는 표현. priority 가로채기 방지용.
_FOLLOWUP_REFERRERS = (
    # "그 중/그중" — 조사·종결 변형
    "그 중", "그중",
    # "이 중/이중"
    "이 중", "이중",
    # "그것/그것들/그 학교(들)"
    "그것 중", "그것들 중", "그 학교 중", "그 학교들 중",
    "그 학교에서", "그 학교들에서",
    # 위치 지시
    "거기서", "거기에서", "거기 중",
    "위에서", "방금", "조금 전",
    # "이것/이것들"
    "이것 중", "이것들 중",
)


def _has_followup_referrer(query: str) -> bool:
    if not query:
        return False
    return any(r in query for r in _FOLLOWUP_REFERRERS)


def _history_rule_identifier(history) -> tuple:
    """history 마지막 3개 질의에서 룰 식별자 탐색 (최신→과거).
    매칭되면 (rule_ids, display_key, original_query) 반환, 아니면 None."""
    if not history:
        return None
    try:
        last3 = list(history)[-3:]
    except TypeError:
        return None
    for entry in reversed(last3):
        try:
            q = (entry or {}).get("query", "") if isinstance(entry, dict) else ""
        except AttributeError:
            q = ""
        if not q:
            continue
        rids, key = _extract_rule_id_or_name(q)
        if rids:
            return (rids, key, q)
    return None


# 카테고리 코드 다음에 올 수 있는 한국어 조사·접속 표현 (단어경계 보강)
_KOREAN_BOUNDARY = "[랑와과의이가은는을를도만에에서으로로하고]"


def _count_rule_identifiers(query: str) -> int:
    """쿼리에 등장하는 서로 다른 룰 카테고리/식별자 수.
    동일 카테고리 안의 룰(C3-3A·C3-3B 등)은 1개로 묶어서 센다 — '미조치 피해' 단일 개념 카운트.
    2개 이상이면 복합 의도(교집합/복수)로 본다."""
    if not query:
        return 0
    q = query
    q_low = q.lower()
    cats: set = set()
    # 1) 룰 ID 매칭 → 그 카테고리만 추가
    for rid in RULE_NAMES_KO.keys():
        if rid.lower() in q_low:
            cat = RULE_META.get(rid, {}).get("category", "").rstrip("'")
            if cat:
                cats.add(cat)
    # 2) 룰 한국어명/줄임형 매칭
    for rid, name in RULE_NAMES_KO.items():
        if not name:
            continue
        name_core = name.split("(")[0].strip()
        if (name in q) or (name_core and name_core in q):
            cat = RULE_META.get(rid, {}).get("category", "").rstrip("'")
            if cat:
                cats.add(cat)
    # 3) 카테고리 코드 단어경계 (한국어 조사 포함)
    for code in CATEGORY_NAMES_KO.keys():
        pattern = rf"(?:^|\s|[^\w]){re.escape(code)}(?:$|\s|[^\w-]|{_KOREAN_BOUNDARY})"
        if re.search(pattern, q):
            cats.add(code)
    # 4) 카테고리 한국어명
    for code, ko in CATEGORY_NAMES_KO.items():
        if ko and ko in q:
            cats.add(code)
    return len(cats)


def _is_complex_lookup(query: str) -> bool:
    """복합 조건/조건어 여부. True면 단순 rule_lookup 처리 금지(LLM 위임)."""
    if not query:
        return False
    if any(h in query for h in _LOOKUP_COMPLEX_HINTS):
        return True
    if _LOOKUP_YEAR_RE.search(query):
        return True
    # 자치구 + 룰 = 교집합(특정 구 안에서 룰 걸린 학교) → 복합
    if _district_in_query(query) is not None:
        return True
    return False


def _extract_rule_context(query: str, df: pd.DataFrame,
                         scores: pd.DataFrame, detections: pd.DataFrame,
                         history=None) -> dict | None:
    """쿼리에서 룰 식별자 추출 + 해당 detections의 학교 목록 반환.
    LLM 위임 경로에서 prompt에 동봉할 컨텍스트.
    현재 쿼리에 룰이 없고 후속 지시어("그 중" 등)가 있으면 history에서 직전 룰 승계.
    매칭 안 되면 None."""
    if detections is None or detections.empty or df is None or df.empty:
        return None
    rids, display_key = _extract_rule_id_or_name(query)
    # 후속 맥락 승계 — 현재 쿼리에 룰 없고 지시어가 있으면 history 직전 룰 사용
    if not rids and history and _has_followup_referrer(query):
        prev = _history_rule_identifier(history)
        if prev:
            rids, display_key, _ = prev
    if not rids:
        return None
    df_d = detections[detections["rule_id"].isin(rids)].copy()
    if "s_r" in df_d.columns:
        df_d["s_r"] = pd.to_numeric(df_d["s_r"], errors="coerce").fillna(0)
    else:
        df_d["s_r"] = 0
    rule_names = [{"rule_id": rid, "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
                   "guide": (SIXBOX_GUIDE.get(rid, {}) or {}).get("pattern", "")}
                  for rid in rids]
    schools = []
    if not df_d.empty:
        grouped = (df_d.groupby(["school_code", "school_name"])
                       .agg(max_sr=("s_r", "max"),
                            years=("year", lambda s: sorted(set(int(y) for y in s if pd.notna(y)))),
                            rules=("rule_id", lambda s: sorted(set(str(r) for r in s))))
                       .reset_index())
        if scores is not None and not scores.empty:
            grouped = grouped.merge(scores[["school_code", "score"]], on="school_code", how="left")
        else:
            grouped["score"] = None
        for _, r in grouped.iterrows():
            sf = df[df["school_code"] == r["school_code"]]
            district_v = str(sf["district"].iloc[0]) if not sf.empty else ""
            students_v = None
            if not sf.empty and "year" in sf.columns and not sf.empty:
                last = sf.sort_values("year").tail(1)
                if "student_count" in last.columns and pd.notna(last["student_count"].iloc[0]):
                    students_v = int(last["student_count"].iloc[0])
            schools.append({
                "school_code": str(r["school_code"]),
                "school_name": str(r["school_name"]),
                "district": district_v,
                "max_sr": round(float(r["max_sr"]), 2),
                "years": r["years"],
                "rule_ids": r["rules"],
                "school_score": (round(float(r["score"]), 1)
                                 if r.get("score") is not None and pd.notna(r["score"]) else None),
                "student_count": students_v,
            })
        schools.sort(key=lambda s: -s["max_sr"])
    return {
        "display_key": display_key,
        "rule_ids": rids,
        "rule_names": rule_names,
        "schools": schools,
    }


def _extract_rule_id_from_query(query: str) -> str | None:
    """쿼리에서 룰 ID 추출 (긴 것 우선)."""
    if not query:
        return None
    q_low = query.lower()
    for rid in sorted(RULE_NAMES_KO.keys(), key=lambda r: -len(r)):
        if rid.lower() in q_low:
            return rid
    return None


def _extract_rule_id_or_name(query: str) -> tuple[list[str], str]:
    """룰 ID 또는 룰명에서 매칭되는 rule_id 리스트와 표시명 추출.
    카테고리 코드(C5/C3 등)만 들어오면 그 카테고리의 모든 활성 rule_id 반환."""
    if not query:
        return [], ""
    q = query
    q_low = q.lower()
    # 1) 룰 ID (긴 것 우선)
    for rid in sorted(RULE_NAMES_KO.keys(), key=lambda r: -len(r)):
        if rid.lower() in q_low:
            return [rid], rid
    # 2) 룰 한국어명 (괄호 안 보조 제거 후도 매칭)
    for rid, name in RULE_NAMES_KO.items():
        if not name:
            continue
        name_core = name.split("(")[0].strip()
        if name in q or (name_core and name_core in q):
            return [rid], rid
    # 3) 카테고리 코드 (C5·C3·B1·D2·C2·E1·E2·F1·G1)
    for code in CATEGORY_NAMES_KO.keys():
        # 코드는 단어 단위로 등장(스페이스/구두점 경계) 또는 끝
        if re.search(rf"(?:^|\s|[^\w]){re.escape(code)}(?:$|\s|[^\w-])", q):
            rids = [rid for rid, m in RULE_META.items()
                    if m.get("category", "").rstrip("'") == code and m.get("status") == "active"]
            if rids:
                return rids, code
    # 4) 카테고리 한국어명
    for code, ko in CATEGORY_NAMES_KO.items():
        if ko and ko in q:
            rids = [rid for rid, m in RULE_META.items()
                    if m.get("category", "").rstrip("'") == code and m.get("status") == "active"]
            if rids:
                return rids, code
    return [], ""


def _handle_rule_lookup_query(query: str, df: pd.DataFrame, scores: pd.DataFrame,
                              detections: pd.DataFrame):
    """룰 ID/룰명 + '걸린/탐지된 학교' → detections 직접 조회.
    경계: 비교·조건어·연도·자치구 등이 붙거나 룰이 2개 이상이면 LLM 위임 → None."""
    if detections is None or detections.empty or df is None or df.empty:
        return None
    q = (query or "").strip()
    if not q:
        return None
    # 1) 조회 동사(걸린/탐지) 있어야 함
    if not any(v in q for v in _LOOKUP_VERBS):
        return None
    # 2) 룰 식별자 추출
    rids, display_key = _extract_rule_id_or_name(q)
    if not rids:
        return None
    # 3) 복수 룰(2개+ 식별자) 또는 복합 조건 → LLM 위임 (컨텍스트는 별도로 prompt에 동봉)
    if _count_rule_identifiers(q) >= 2:
        return None
    if _is_complex_lookup(q):
        return None

    # 4) detections에서 필터
    df_d = detections[detections["rule_id"].isin(rids)].copy()
    if df_d.empty:
        # 룰이 활성인데 탐지 0건이면 자연스러운 빈 결과 안내
        report = f"**{display_key}** 룰에 해당하는 탐지는 현재 표본(서울 일반고 210교 · 2023~2025년)에서 0건입니다."
        return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="rule_lookup")

    # 학교별 최고 s_r + 연도 묶음 → 점수 내림차순 정렬
    if "s_r" in df_d.columns:
        df_d["s_r"] = pd.to_numeric(df_d["s_r"], errors="coerce").fillna(0)
    else:
        df_d["s_r"] = 0
    school_agg = (df_d.groupby(["school_code", "school_name"])
                      .agg(max_sr=("s_r", "max"),
                           years=("year", lambda s: sorted(set(int(y) for y in s if pd.notna(y)))),
                           n=("rule_id", "count"))
                      .reset_index()
                      .sort_values(["max_sr", "n"], ascending=[False, False]))

    # 학교 종합점수 조인 (있으면 표시)
    if scores is not None and not scores.empty:
        school_agg = school_agg.merge(
            scores[["school_code", "score", "rank"]], on="school_code", how="left"
        )
    else:
        school_agg["score"] = None
        school_agg["rank"] = None

    rows = school_agg.head(15)
    result_data = []
    for _, r in rows.iterrows():
        sf = df[df["school_code"] == r["school_code"]]
        district_v = str(sf["district"].iloc[0]) if not sf.empty else ""
        yrs = ",".join(str(y) for y in (r["years"] or [])) or "-"
        score_v = r.get("score")
        score_txt = f"{float(score_v):.1f}" if score_v is not None and pd.notna(score_v) else "-"
        result_data.append({
            "학교명": str(r["school_name"]),
            "지역구": district_v,
            "탐지 연도": yrs,
            "탐지 항목 점수(s_r) 최대": f"{float(r['max_sr']):.2f}",
            "학교 종합점수": score_txt,
        })

    rule_names = ", ".join(f"{rid}({RULE_NAMES_KO.get(rid, rid)})" for rid in rids)
    report = (
        f"**{display_key}** 룰에 해당하는 학교 — 총 **{len(school_agg)}교** "
        f"(탐지 항목 점수 s_r 내림차순, 상위 {len(rows)}교 표시).\n\n"
        f"대상 룰: {rule_names}\n\n"
        "점수 자체는 판정이 아니며, 본부 담당자의 확인을 돕는 정렬 신호입니다."
    )

    # 단일 룰 결과면 6박스 첨부(데이터 패널이 학교 1개를 요구하지 않는 학교군 응답에는 None)
    sixbox = None
    return {
        "plan": {
            "analysis_plan": f"deterministic guard: rule_lookup ({display_key})",
            "columns_used": [],
            "criteria": display_key,
            "pandas_code": "",
            "comparison": "",
            "confidence": "높음",
        },
        "result_data": result_data,
        "report": report,
        "confidence": "높음",
        "follow_up_suggestions": _EXAMPLE_SUGGESTIONS,
        "sixbox": sixbox,
    }


def _handle_priority_query(query: str, df: pd.DataFrame, scores: pd.DataFrame,
                           detections: pd.DataFrame, history=None):
    """'우선순위가 가장 높은 학교' 류 질문 → scores 기준 응답. 매칭 안 되면 None.
    Codex 검수 치명 3건 대응:
      (1) 룰 식별자 + 조건어 조합이면 priority가 가로채지 말고 LLM 위임.
      (2) 후속 지시어 + history 직전 룰 언급이면 직전 컨텍스트 유지로 LLM 위임.
    """
    if scores is None or scores.empty or df is None or df.empty:
        return None
    q = (query or "").strip()
    if not any(k in q for k in _PRIORITY_KEYWORDS):
        return None
    # ★ 치명 1: 룰 식별자 + 조건어("중·제일·높은·둘 다" 등) — LLM이 룰 컨텍스트 위 처리.
    if _count_rule_identifiers(q) > 0 and _is_complex_lookup(q):
        return None
    # ★ 치명 2: 후속 지시어("그 중 제일 높은") + history에 직전 룰 언급 — 맥락 승계 LLM.
    if _has_followup_referrer(q) and history and _history_rule_identifier(history):
        return None
    # 학교명 패턴이 있는데 표본에 없으면 전체 top5 응답은 부적절 — LLM/안내로 위임.
    # 예: "부산고 우선순위" → 부산고는 서울 표본에 없음 → 전체 top5 반환하면 오답.
    if _looks_like_school_name(q) and not _query_school_in_data(q):
        return None

    # district 필터 — '중' 같은 1자 자치구는 _district_in_query로 단어경계 처리
    district = _district_in_query(q)
    scope = scores
    scope_label = "전체"
    if district:
        district_codes = set(df[df["district"] == district]["school_code"].astype(str))
        scope = scores[scores["school_code"].astype(str).isin(district_codes)].copy()
        scope_label = f"{district}구"
    if scope.empty:
        return None

    # 표시 건수 N
    m = re.search(r"top\s*(\d+)|상위\s*(\d+)|(\d+)\s*위|(\d+)\s*교", q.lower())
    n = 5
    if m:
        nums = [int(g) for g in m.groups() if g]
        if nums:
            n = nums[0]
    elif any(k in q for k in ("1위", "가장 높은", "가장 우선", "최우선")):
        n = 1
    n = max(1, min(n, 10))

    top = scope.head(n)

    result_data = []
    for _, row in top.iterrows():
        rep = _representative_detection(detections, row["school_code"])
        rep_text = ""
        if rep is not None:
            rid = str(rep.get("rule_id", ""))
            rep_text = f"{RULE_NAMES_KO.get(rid, rid)} ({rid})"
        sf = df[df["school_code"] == row["school_code"]]
        district_v = str(sf["district"].iloc[0]) if not sf.empty else ""
        result_data.append({
            "순위": int(row.get("rank", 0)),
            "학교명": str(row["school_name"]),
            "지역구": district_v,
            "검토 우선도 지수": f"{float(row.get('score', 0)):.1f}",
            "검토 신호 수": int(row.get("num_detections", 0)),
            "관련 카테고리 수": int(row.get("num_categories", 0)),
            "반복 신호": "반복" if bool(row.get("is_repeat", False)) else "단발",
            "대표 검토 신호": rep_text,
        })

    sixbox_attached = None
    if n == 1 and result_data:
        s = top.iloc[0]
        rep = _representative_detection(detections, s["school_code"])
        rep_line = ""
        if rep is not None:
            rid = str(rep.get("rule_id", ""))
            rep_line = f" 대표 검토 신호: {RULE_NAMES_KO.get(rid, rid)}({rid}) — {rep.get('detail','')}."
            # 챗봇 응답에 대표 룰 6박스 첨부 — 학교+룰 컨텍스트 명확
            try:
                school_df_one = df[df["school_code"] == str(s["school_code"])]
                district_one = str(school_df_one["district"].iloc[0]) if not school_df_one.empty else ""
                dyn_label = str(rep.get("col_label", "") or "").strip()
                dyn_key = str(rep.get("col_key", "") or "").strip()
                if dyn_key:
                    col_pairs_one = [{"key": dyn_key, "label": _canon_label(dyn_label or dyn_key)}]
                else:
                    col_pairs_one = [{"key": k, "label": _canon_label(label)} for label, k in RULE_COLUMNS.get(rid, [])]
                rule_obj_one = {
                    "rule_id": rid,
                    "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
                    "year": int(rep.get("year", 0)),
                    "detail": str(rep.get("detail", "")),
                    "col_labels": [p["label"] for p in col_pairs_one],
                    "col_keys": [p["key"] for p in col_pairs_one],
                    "col_pairs": col_pairs_one,
                }
                sixbox_attached = _build_sixbox(rule_obj_one, school_df_one, df, district_one)
            except Exception as e:
                print(f"[WARN] priority sixbox 생성 실패: {e}")
                sixbox_attached = None
        report = (
            f"{scope_label} 기준 검토 우선도가 가장 높은 학교는 **{s['school_name']}**입니다 "
            f"(검토 우선도 지수 {float(s['score']):.1f}, 전체 순위 {int(s['rank'])}위, "
            f"검토 신호 {int(s.get('num_detections', 0))}건, {int(s.get('num_categories', 0))}개 카테고리).{rep_line}\n\n"
            f"본 답변은 앱 내부의 검토 우선도 지수(scores)와 룰 기반 검토 신호(detections)를 그대로 사용한 결과이며, "
            f"추가 분석 기준을 만들지 않았습니다. 지수는 학교 평가가 아니라 확인 순서를 돕는 내부 분석값입니다."
        )
    else:
        report = (
            f"{scope_label} 기준 검토 우선도 상위 {len(result_data)}교를 추출했습니다. "
            f"이 결과는 앱의 검토 우선 후보와 동일한 기준(검토 우선도 지수·룰 기반 검토 신호)을 사용합니다."
        )

    return {
        "plan": {
            "analysis_plan": f"{scope_label} 학교들을 검토 우선도 지수 기준 정렬 → 상위 {n}교 추출",
            "columns_used": ["score", "rank", "num_detections", "num_categories", "is_repeat"],
            "criteria": "검토 우선도 지수 = 핵심 신호 강도 + 영역 가중치 + 반복 신호 가중치 (내부 분석 지수, 학교 평가 점수 아님)",
            "pandas_code": "",
            "comparison": "scores DataFrame 사용 (앱 내부 통일 기준)",
            "confidence": "높음",
        },
        "result_data": result_data,
        "report": report,
        "confidence": "높음",
        "follow_up_suggestions": [
            "1순위 학교 상세 보여줘",
            "우선 검토 신호만 요약해줘",
            "강남구 검토 후보만 보여줘" if district != "강남" else "전체 검토 후보 보여줘",
            "이 학교들의 공통 검토 신호는?",
        ],
        "sixbox": sixbox_attached,
    }


@app.get("/api/school/{school_code}/ai/{category_ko}")
async def category_ai(school_code: str, category_ko: str):
    """카테고리별 AI 해석 (비동기 로드). 실패 시 안전 폴백 (UI 빈칸 방지)."""
    client = app_state.get("gemini")
    if not client:
        return {**FALLBACK_CATEGORY, "해석": "LLM 비활성화 상태입니다."}

    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())
    school_det = detections[detections["school_code"] == school_code]
    school_df = df[df["school_code"] == school_code]
    school_name = school_df["school_name"].iloc[0] if not school_df.empty else ""
    district = school_df["district"].iloc[0] if not school_df.empty else ""

    det_cards = _build_detection_cards(school_det, school_df, df, district)
    card = next((c for c in det_cards if c["category_ko"] == category_ko), None)
    if not card:
        return {**FALLBACK_CATEGORY, "해석": "해당 카테고리 데이터가 없습니다."}

    try:
        return _explain_category(client, card, school_name, district)
    except GeminiError as e:
        print(f"[WARN] category_ai Gemini 실패 ({school_code}/{category_ko}): {e}")
        return dict(FALLBACK_CATEGORY)
    except Exception as e:
        print(f"[WARN] category_ai 예상치 못한 실패 ({school_code}/{category_ko}): {e}")
        return dict(FALLBACK_CATEGORY)


@app.get("/api/stats")
async def stats():
    df = app_state.get("df", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    # 내부 룰 메타(star)는 노출 명칭을 신호 강도 기준으로 변경. UI 분기에는 쓰지 않음.
    return {
        "data_rows": len(df),
        "schools": df["school_name"].nunique() if not df.empty else 0,
        "years": sorted(df["year"].unique().tolist()) if not df.empty else [],
        "total_detections": len(detections),
        "priority_signal_count": len(detections[detections["star"] == 3]) if not detections.empty else 0,
        "normal_signal_count": len(detections[detections["star"] == 2]) if not detections.empty else 0,
        "rules_triggered": detections["rule_id"].nunique() if not detections.empty else 0,
    }


# ── 룰 생성기 테스트 (샌드박스) ──

class RuleLabRequest(BaseModel):
    query: str

@app.post("/api/rulelab")
async def rulelab(req: RuleLabRequest):
    """샌드박스 룰 생성기 — AI가 자연어 → 룰 해석 + 코드 생성 + 시뮬레이션. 메인 엔진 무관."""
    client = app_state.get("gemini")
    df = app_state.get("df", pd.DataFrame())

    if client is None:
        return {"error": "AI 모델이 연결되지 않았습니다. GOOGLE_API_KEY를 설정해주세요."}

    if df.empty:
        return {"error": "데이터가 로드되지 않았습니다."}

    cols = list(df.columns)
    sample = df.head(3).to_dict(orient='records')

    col_desc = {
        "student_count": "학생수", "teacher_count": "교원수", "class_count": "학급수",
        "students_per_class": "학급당학생수", "students_per_teacher": "교원1인당학생수",
        "budget_revenue": "학교회계 세입", "budget_expense": "학교회계 세출",
        "meal_cost_per_student": "1인당급식비", "graduation_rate": "졸업자진학률",
        "bullying_cases": "학폭심의건수", "bullying_victims": "피해학생수", "bullying_perpetrators": "가해학생수",
        "student_count_yoy": "학생수 전년대비변동률(%)", "teacher_count_yoy": "교원수 전년대비변동률(%)",
        "district": "구", "school_name": "학교명", "year": "연도", "school_type": "설립유형",
    }
    available = {c: col_desc.get(c, c) for c in cols if c in col_desc}

    prompt = f"""너는 교육 공공데이터 검증 룰 생성 AI야. 서울 일반고 210교 × 3년(2023~2025) 데이터를 분석해.

사용 가능한 컬럼과 설명:
{available}

중요: district 컬럼 값은 "강남", "노원", "관악" 등 '구' 없이 저장됨. "관악구"가 아니라 "관악"으로 필터링해야 함.
중요: bullying_cases, bullying_victims, bullying_perpetrators는 NaN이 많음. fillna(0) 후 사용.
중요: thresholds의 val은 반드시 0이 아닌 의미 있는 기본값 설정 (예: 심의건수 5건, 피해학생 3명 등).

사용자 질문: {req.query}

반드시 아래 JSON 형식으로만 응답 (다른 텍스트·마크다운 금지):
{{
  "interpretation": "조건을 전문적으로 다듬은 해석 (한국어 2~3문장, <b>태그 사용). 사용자 말 그대로가 아니라, 구체적 지표·수치 기준으로 재해석",
  "code": "pandas 코드. df는 이미 존재. result_df에 결과 저장. import 금지. df와 pd만 사용. .sort_values()로 심각도 순 정렬. 중요: 임계값은 반드시 코드 맨 위에 THRESHOLD_0, THRESHOLD_1 등 변수로 선언하고 조건에서 해당 변수 사용. 예: THRESHOLD_0 = 5\nresult_df = df[df['bullying_cases'] >= THRESHOLD_0]",
  "columns_used": ["실제 사용한 컬럼명 (위 목록에서만 선택)"],
  "indicators": [
    {{"name": "한국어 지표명", "col": "컬럼명", "checked": true/false}}
  ],
  "thresholds": [
    {{"label": "임계값 설명", "min": 최소값(숫자), "max": 최대값(숫자), "val": 기본값(숫자), "unit": "단위(%, 명, 건 등)"}}
  ],
  "primary_condition": {{
    "label": "주 조건",
    "value": "전문적으로 다듬은 조건 요약 (예: 학폭 심의건수 연간 5건 이상)",
    "desc": "이 조건이 뭘 확인하는지 (예: 해당 연도 학교폭력 심의 건수)"
  }},
  "secondary_condition": null 또는 {{"label": "보조 조건", "value": "...", "desc": "..."}},
  "risk_level": 1~3 (3=학생안전, 2=자원배분/재정, 1=통계참고),
  "risk_name": "위험 분류 (학생 안전, 자원 배분, 재정 연동 등)"
}}"""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"temperature": 0},
        )
        text = response.text.strip()

        # JSON 파싱
        import json as _json
        # 코드블록 마커 제거
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        try:
            parsed = _json.loads(text)
        except:
            return {"interpretation": "AI 응답을 파싱할 수 없습니다.", "message": text, "results": []}

        interpretation = parsed.get("interpretation", "")
        code = parsed.get("code", "")

        # 샌드박스 실행
        results = []
        if code:
            try:
                local_ns = {"df": df.copy(), "pd": pd, "np": __import__("numpy")}
                exec(code, {"__builtins__": {}}, local_ns)
                result_df = local_ns.get("result_df", pd.DataFrame())

                if isinstance(result_df, pd.DataFrame) and not result_df.empty:
                    used_cols = parsed.get("columns_used", [])[:4]
                    for _, row in result_df.head(20).iterrows():
                        detail_parts = []
                        for c in used_cols:
                            if c in row.index:
                                v = row[c]
                                label = {
                                    "bullying_cases":"심의건수","bullying_victims":"피해학생","bullying_perpetrators":"가해학생",
                                    "student_count":"학생수","teacher_count":"교원수","students_per_class":"학급당학생수",
                                    "meal_cost_per_student":"1인당급식비","budget_revenue":"세입","graduation_rate":"진학률",
                                }.get(c, c)
                                if isinstance(v, float):
                                    detail_parts.append(f"{label} <b>{v:.1f}</b>")
                                else:
                                    detail_parts.append(f"{label} <b>{v}</b>")
                        results.append({
                            "school": str(row.get("school_name", "")),
                            "year": str(row.get("year", "")),
                            "detail": " / ".join(detail_parts),
                        })
            except Exception as e:
                return {
                    **parsed,
                    "results": [],
                    "message": f"코드 실행 중 오류: {str(e)[:100]}. 조건을 다시 설명해주세요.",
                }

        return {
            **parsed,
            "results": results,
            "message": f"210교 중 {len(results)}교 탐지" if results else "조건에 해당하는 학교가 없습니다.",
        }

    except Exception as e:
        return {"error": f"AI 호출 실패: {str(e)[:100]}"}


class RuleLabRerunRequest(BaseModel):
    code: str
    columns_used: list = []

@app.post("/api/rulelab/rerun")
async def rulelab_rerun(req: RuleLabRerunRequest):
    """샌드박스 코드 재실행 — 임계값 수정 후 조건 적용."""
    df = app_state.get("df", pd.DataFrame())
    if df.empty:
        return {"error": "데이터가 로드되지 않았습니다."}

    results = []
    try:
        local_ns = {"df": df.copy(), "pd": pd, "np": __import__("numpy")}
        exec(req.code, {"__builtins__": {}}, local_ns)
        result_df = local_ns.get("result_df", pd.DataFrame())

        if isinstance(result_df, pd.DataFrame) and not result_df.empty:
            used_cols = req.columns_used[:4]
            col_labels = {
                "bullying_cases":"심의건수","bullying_victims":"피해학생","bullying_perpetrators":"가해학생",
                "student_count":"학생수","teacher_count":"교원수","students_per_class":"학급당학생수",
                "meal_cost_per_student":"1인당급식비","budget_revenue":"세입","graduation_rate":"진학률",
                "student_count_yoy":"학생수변동률","teacher_count_yoy":"교원수변동률",
            }
            for _, row in result_df.head(20).iterrows():
                detail_parts = []
                for c in used_cols:
                    if c in row.index:
                        v = row[c]
                        label = col_labels.get(c, c)
                        if isinstance(v, float):
                            detail_parts.append(f"{label} <b>{v:.1f}</b>")
                        else:
                            detail_parts.append(f"{label} <b>{v}</b>")
                results.append({
                    "school": str(row.get("school_name", "")),
                    "year": str(row.get("year", "")),
                    "detail": " / ".join(detail_parts),
                })
    except Exception as e:
        return {"results": [], "message": f"코드 실행 오류: {str(e)[:100]}"}

    return {
        "results": results,
        "message": f"210교 중 {len(results)}교 탐지" if results else "조건에 해당하는 학교가 없습니다.",
    }


# ── 한국어 명칭 매핑 ──

RULE_NAMES_KO = {
    # 룰 이름 — 룰셋_정의서_v4 표 기준 정렬 (PDF 피드백 3)
    "C1-1": "학생↔학급 역방향 변동", "C1-2": "학생↔학급 완만 역방향",
    "C1-3": "학생↔교원 불균형", "C1-4": "학급↔교원 불균형",
    "C1-5": "학생↔보직교사 불균형", "C1-7": "교원1인당학생수 급변",
    "C1-8": "학급당학생수 급변",
    "C3-3A": "미조치 피해 (강력)", "C3-3B": "미조치 피해 (참고)",
    "B1-1": "학생·학급·교원 급변동(이중)", "B1-2": "학생·학급·교원 급변동(단년)",
    "B1-3": "학교회계 변동", "B1-4": "학교회계 강한 변동",
    "B1-5": "진학률 급변동", "B1-6": "학폭 심의 급증",
    "C2-3": "급식비 변동", "C2-3+": "급식비 강한 변동",
    "D2-1": "유사학교 상하위 10%", "D2-2": "유사학교 IQR 극단값",
    "C5-1": "진급 시 학생 이탈",
    "E1-1": "3년 연속 미입력", "E1-2": "단독 미입력",
    "E1-3": "공시 의무 항목 미제출",
    "E2-2": "3년 동일값 반복",
    "F1'-1": "교원수 교차 불일치",
    # G1-1: 본교 단일 시계열의 누적 단조 추세를 점검 (m_r = 변동폭/전체평균변동폭).
    # G1-2: 같은 방향 + 변동폭이 전체 평균의 2배+.
    "G1-1": "다년 단조 추세",
    "G1-2": "추세 급변동",
}

CATEGORY_NAMES_KO = {
    "C1": "학생·자원 연동 점검", "C3": "미조치 피해 점검",
    "B1": "전년 대비 급변동", "C2": "학생·재정 연동 점검",
    "D2": "유사학교 대비 편차", "C5": "학년 진급 인원 점검",
    "E1": "누락 패턴 점검", "E2": "수치 미갱신 점검",
    "F1": "연계 시점 차이 점검",
    "G1": "장기 추세 점검",
}

# 데이터 테이블에 표시할 지표 목록 (단일 라벨, 중복 행 없음).
# RULE_COLUMNS·차트 등에서 다른 라벨로 들어와도 LABEL_ALIAS로 정규화하여 같은 행으로 매칭.
TABLE_METRICS = [
    ("학생수", "student_count"),
    ("1학년 학생수", "grade1_students"),
    ("2학년 학생수", "grade2_students"),
    ("학급수", "class_count"),
    ("교원수", "teacher_count"),
    ("교원수(강사제외)", "teacher_count_no_instructor"),
    ("보직교사수", "head_teacher_count"),
    ("학급당학생수", "students_per_class"),
    ("교원1인당학생수", "students_per_teacher"),
    ("학폭 심의건수", "bullying_cases"),
    ("피해학생수", "bullying_victims"),
    ("보호조치건수", "bullying_protection"),
    ("가해학생수", "bullying_perpetrators"),
    ("진학률(%)", "graduation_rate"),
    ("급식비총액(원)", "meal_cost_total"),
    ("1인당급식비(원)", "meal_cost_per_student"),
    ("학교회계 세입", "budget_revenue"),
    ("학교회계 세출", "budget_expense"),
]

# 라벨 동의어 — 다양한 곳에서 들어오는 라벨을 정식 라벨로 정규화
LABEL_ALIAS = {
    "진학률": "진학률(%)",
    "급식비총액": "급식비총액(원)",
    "급식비 총액": "급식비총액(원)",
    "1인당급식비": "1인당급식비(원)",
    "1인당 급식비": "1인당급식비(원)",
    "학폭건수": "학폭 심의건수",
    # 시설 (rule_engine._fc_name이 내려주는 라벨과 chart_cols 라벨 통일)
    "기숙사": "기숙사실수",
}

def _canon_label(label: str) -> str:
    """라벨 정규화 — 별칭이 있으면 정식 라벨, 없으면 원본."""
    return LABEL_ALIAS.get(label, label)


def _build_data_table(school_df, detections_df, full_df) -> list:
    """셀 상태 포함 시계열 데이터 테이블"""
    if school_df.empty:
        return []

    years = sorted(school_df["year"].unique())
    district = school_df["district"].iloc[0] if "district" in school_df.columns else ""

    # 탐지된 (연도, 컬럼) 쌍 수집 — E2-2 등 동적 컬럼 룰은 detection.col_key를 우선 사용
    detected_cells = set()
    for _, d in detections_df.iterrows():
        yr = int(d.get("year", 0))
        rid = d.get("rule_id", "")
        dyn_col = str(d.get("col_key", "") or "").strip()
        if dyn_col:
            detected_cells.add((yr, dyn_col))
        else:
            for _, col_key in RULE_COLUMNS.get(rid, []):
                detected_cells.add((yr, col_key))

    rows = []
    for label, col in TABLE_METRICS:
        if col not in school_df.columns:
            continue
        row = {"지표": label, "col_key": col}
        # 동료군 평균 — school_df에 이미 _dist_mean이 계산되어 있음
        peer_col = f"{col}_dist_mean"
        if peer_col in school_df.columns:
            last_yr = school_df[school_df["year"] == years[-1]]
            if not last_yr.empty and pd.notna(last_yr[peer_col].iloc[0]):
                row["동료군평균"] = round(float(last_yr[peer_col].iloc[0]), 1)
            else:
                row["동료군평균"] = None
        else:
            row["동료군평균"] = None

        for yr in years:
            yr_row = school_df[school_df["year"] == yr]
            if yr_row.empty:
                row[str(yr)] = {"value": None, "status": "empty"}
                continue
            val = yr_row[col].iloc[0]
            if pd.isna(val):
                row[str(yr)] = {"value": None, "status": "empty"}
                continue

            val = float(val)
            status = "normal"
            if (yr, col) in detected_cells:
                status = "detected"

            row[str(yr)] = {"value": round(val, 1) if val != int(val) else int(val), "status": status}

        rows.append(row)
    return rows


def _build_chart_data(school_df, full_df, district) -> dict:
    """Chart.js용 데이터 — 본교 시계열 + 동료군 평균 + 동료군 범위(min/max).
    룰 단위 Evidence Chart에서 룰 코드별로 표시 컬럼을 선택해서 사용."""
    if school_df.empty:
        return {}

    years = sorted(school_df["year"].unique())
    labels = [int(y) for y in years]

    def _peer_subset(y):
        return full_df[(full_df["district"] == district) & (full_df["year"] == y)] if district else full_df[full_df["year"] == y]

    def get_vals(col):
        out = []
        for y in years:
            sub = school_df[school_df["year"] == y]
            if sub.empty or pd.isna(sub[col].iloc[0]):
                out.append(None)
            else:
                out.append(round(float(sub[col].iloc[0]), 1))
        return out

    def get_peer_mean(col):
        out = []
        for y in years:
            sub = _peer_subset(y)[col].dropna()
            out.append(round(float(sub.mean()), 1) if len(sub) > 0 else None)
        return out

    def get_peer_minmax(col):
        mins, maxs = [], []
        for y in years:
            sub = _peer_subset(y)[col].dropna()
            if len(sub) > 0:
                mins.append(round(float(sub.min()), 1))
                maxs.append(round(float(sub.max()), 1))
            else:
                mins.append(None); maxs.append(None)
        return mins, maxs

    # 차트용 컬럼 후보 (룰 단위 Evidence Chart에서 선택해서 표시) — 단일 라벨, 별칭 따로 처리
    # 시설 7개(fc_*)도 포함: E1-1/E1-2 Status Timeline용. NaN(미입력)인 경우도 시계열에 그대로 표시.
    chart_cols = [
        ("student_count", "학생수"),
        ("grade1_students", "1학년 학생수"),
        ("grade2_students", "2학년 학생수"),
        ("class_count", "학급수"),
        ("teacher_count", "교원수"),
        ("teacher_count_no_instructor", "교원수(강사제외)"),
        ("head_teacher_count", "보직교사수"),
        ("students_per_class", "학급당학생수"),
        ("students_per_teacher", "교원1인당학생수"),
        ("bullying_cases", "학폭 심의건수"),
        ("bullying_victims", "피해학생수"),
        ("bullying_protection", "보호조치건수"),
        ("bullying_perpetrators", "가해학생수"),
        ("graduation_rate", "진학률(%)"),
        ("meal_cost_total", "급식비총액(원)"),
        ("meal_cost_per_student", "1인당급식비(원)"),
        ("budget_revenue", "학교회계 세입"),
        ("budget_expense", "학교회계 세출"),
        # 시설 (E1-1 / E1-2 Status Timeline)
        ("fc_changing_room", "학생탈의실"),
        ("fc_shower", "학생샤워실"),
        ("fc_health_room", "보건실"),
        ("fc_cafeteria", "학생식당"),
        ("fc_dorm", "기숙사실수"),
        ("fc_av_room", "시청각실"),
        ("fc_computer_room", "컴퓨터실"),
    ]

    series = {}  # 라벨 또는 col_key → {self, peer_mean, peer_min, peer_max}
    for col_key, label in chart_cols:
        if col_key not in school_df.columns:
            continue
        self_vals = get_vals(col_key)
        peer_mean = get_peer_mean(col_key)
        peer_min, peer_max = get_peer_minmax(col_key)
        payload = {
            "self": self_vals,
            "peer_mean": peer_mean,
            "peer_min": peer_min,
            "peer_max": peer_max,
        }
        # 정식 라벨로도, col_key로도 접근 가능 (프론트에서 col_keys 우선 매칭 시 안정)
        series[label] = payload
        series[col_key] = payload

    return {
        "labels": labels,
        "series": series,
        # ↓ 하위 호환 (기존 키 그대로 유지)
        "학생수": series.get("학생수", {}).get("self") or [None] * len(years),
        "학급수": series.get("학급수", {}).get("self") or [None] * len(years),
        "교원수": series.get("교원수", {}).get("self") or [None] * len(years),
        "학급당학생수": series.get("학급당학생수", {}).get("self") or [None] * len(years),
        "학폭건수": series.get("학폭건수", {}).get("self") or [None] * len(years),
        "피해학생수": series.get("피해학생수", {}).get("self") or [None] * len(years),
        "보호조치건수": series.get("보호조치건수", {}).get("self") or [None] * len(years),
        "진학률": series.get("진학률", {}).get("self") or [None] * len(years),
        "동료군_학생수": series.get("학생수", {}).get("peer_mean") or [None] * len(years),
        "동료군_교원수": series.get("교원수", {}).get("peer_mean") or [None] * len(years),
        "동료군_학급당학생수": series.get("학급당학생수", {}).get("peer_mean") or [None] * len(years),
    }


def _get_category_code(rule_id: str) -> str:
    """C3-3A → C3, B1-5 → B1, F1'-1 → F1"""
    base = rule_id.split("-")[0]
    return base.rstrip("'")


# 학교 종합 점수(0~100) → 한국어 라벨. 임계 단일 출처는 priority_scorer.LABEL_THRESHOLDS.
# 프론트 indexLabel과 동일한 임계·라벨을 사용.
_GRADE_LABEL_KO = {
    "critical": "즉시 검토",
    "major":    "우선 검토 대상",
    "minor":    "일반 검토",
    "warning":  "참고",
}
def _grade_label_ko(score: float) -> str:
    return _GRADE_LABEL_KO.get(label_for(score), "참고")


def _categories_ko(categories_str: str) -> list:
    """'C1, C3, B1' → [{'code':'C1','ko':'학생·자원 연동 점검'}, ...]"""
    if not categories_str or pd.isna(categories_str):
        return []
    codes = [c.strip() for c in str(categories_str).split(",") if c.strip()]
    return [{"code": c, "ko": CATEGORY_NAMES_KO.get(c, c)} for c in codes]


def _representative_detection(detections_df: pd.DataFrame, school_code: str):
    """학교의 가장 강한 탐지 1건 — s_r(탐지 건 점수, v4) 우선, 동률이면 최신 연도.
    s_r 컬럼이 없는 경우(레거시 경로) star로 폴백."""
    school = detections_df[detections_df["school_code"] == school_code]
    if school.empty:
        return None
    sort_keys = ["s_r", "year"] if "s_r" in school.columns else ["star", "year"]
    return school.sort_values(sort_keys, ascending=[False, False]).iloc[0]


def _enrich_school_summary(row, detections_df: pd.DataFrame, df: pd.DataFrame) -> dict:
    """학교 1건에 대표 탐지 + 한국어 카테고리/룰명 + 메타 부착.
    검토 우선도 지수는 score를 float로 노출 (소수점 1자리 표시는 프론트에서 처리).
    max_star는 내부 룰 메타로만 유지 — UI에서 등급 분기에 쓰지 않음."""
    score_f = float(row["score"])
    item = {
        "school_code": str(row["school_code"]),
        "school_name": str(row["school_name"]),
        "score": score_f,                       # 표기·정렬은 프론트에서 .toFixed(1)
        "rank": int(row.get("rank", 0)),
        "max_star": int(row.get("max_star", 0)),  # 내부 메타 (UI 분기 X)
        "num_categories": int(row.get("num_categories", 0)),
        "num_detections": int(row.get("num_detections", 0)),
        "is_repeat": bool(row.get("is_repeat", False)),
        "categories_ko": _categories_ko(row.get("categories", "")),
    }
    school_rows = df[df["school_code"] == row["school_code"]]
    if not school_rows.empty:
        item["district"] = str(school_rows["district"].iloc[0])
        item["school_type"] = str(school_rows["school_type"].iloc[0])
    # 룰 필터 정확 매칭용 — 해당 학교가 트리거한 전체 rule_id 집합 (중복 제거)
    school_det = detections_df[detections_df["school_code"] == row["school_code"]]
    item["rule_ids"] = sorted({str(r) for r in school_det["rule_id"].unique()}) if not school_det.empty else []
    rep = _representative_detection(detections_df, row["school_code"])
    if rep is not None:
        rid = str(rep.get("rule_id", ""))
        cat = _get_category_code(rid)
        item["rep"] = {
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
            "category_code": cat,
            "category_ko": CATEGORY_NAMES_KO.get(cat, cat),
            "detail": str(rep.get("detail", "")),
            "year": int(rep.get("year", 0)),
            "star": int(rep.get("star", 0)),
        }
    return item


def _build_self_report(detail_result: dict, school_df, full_df, det_cards: list, school_score) -> dict:
    """⑦ 자가진단 리포트 — 한 학교의 종합 요약. LLM 미사용, 정적 + 자동 주입.
    포함: 학교명·지수·순위·주요 검토 신호 Top N·카테고리 요약·동료군 대비 요약·확인 권장."""
    rank_total = int(school_score["rank"].iloc[0]) if not school_score.empty else 0
    score_f = float(school_score["score"].iloc[0]) if not school_score.empty else 0.0
    # 라벨 — priority_scorer.LABEL_THRESHOLDS 단일 출처(70/50/30). 프론트 indexLabel과 임계 동일.
    grade_label = _grade_label_ko(score_f)

    # 주요 검토 신호 Top 5 — s_r(탐지 건 점수, v4) 우선, 동률은 최신 연도
    flat_rules = []
    for cat in det_cards:
        for r in cat.get("rules", []):
            flat_rules.append({
                "rule_id": r["rule_id"],
                "rule_name_ko": r["rule_name_ko"],
                "category_ko": cat["category_ko"],
                "year": r["year"],
                "s_r": float(r.get("s_r", 0) or 0),
                "star": r.get("star", 0),  # 백워드 호환 (UI 분기 X)
                "detail": r.get("detail", ""),
            })
    flat_rules.sort(key=lambda x: (-x["s_r"], -x["year"], x["rule_id"]))
    top_signals = flat_rules[:5]

    # 카테고리 요약 — 카테고리별 룰 수·세부 룰 수
    cat_summary = []
    for cat in det_cards:
        seen_rules = {r["rule_id"] for r in cat.get("rules", [])}
        cat_summary.append({
            "category_ko": cat["category_ko"],
            "cat_code": cat.get("cat_code", ""),
            "total_detections": len(cat.get("rules", [])),
            "rule_count": len(seen_rules),
            "is_repeat": bool(cat.get("is_repeat", False)),
        })

    # 동료군 대비 요약 — 핵심 4지표(학생수·교원수·학급당학생수·1인당급식비)에서 본교 vs 동료군
    peer_summary = []
    if not school_df.empty:
        last_yr = sorted(school_df["year"].unique())[-1]
        last = school_df[school_df["year"] == last_yr]
        for col, label, unit in [
            ("student_count", "학생수", "명"),
            ("teacher_count", "교원수", "명"),
            ("students_per_class", "학급당학생수", "명/학급"),
            ("meal_cost_per_student", "1인당 급식비", "원"),
        ]:
            peer_col = f"{col}_dist_mean"
            if col in last.columns and peer_col in last.columns:
                self_v = last[col].iloc[0]
                peer_v = last[peer_col].iloc[0]
                if pd.notna(self_v) and pd.notna(peer_v) and peer_v != 0:
                    diff_pct = (float(self_v) - float(peer_v)) / float(peer_v) * 100
                    peer_summary.append({
                        "label": label,
                        "unit": unit,
                        "self": round(float(self_v), 1),
                        "peer": round(float(peer_v), 1),
                        "diff_pct": round(float(diff_pct), 1),
                    })

    # 확인 권장 — Top 신호에서 SIXBOX_GUIDE recommend 추출 (중복 제거 최대 5건)
    recommends = []
    seen_rids = set()
    for s in top_signals:
        rid = s["rule_id"]
        if rid in seen_rids:
            continue
        seen_rids.add(rid)
        guide = SIXBOX_GUIDE.get(rid)
        if guide and guide.get("recommend"):
            recommends.append({"rule_id": rid, "rule_name_ko": s["rule_name_ko"], "text": guide["recommend"]})
        if len(recommends) >= 5:
            break

    return {
        "school_name": detail_result.get("school_name", ""),
        "district": detail_result.get("district", ""),
        "school_type": detail_result.get("school_type", ""),
        "score": round(score_f, 1),
        "grade_label": grade_label,
        "rank": rank_total,
        "num_detections": detail_result.get("num_detections", 0),
        "num_rules": detail_result.get("num_rules", 0),
        "num_categories": len(det_cards),
        "is_repeat": detail_result.get("is_repeat", False),
        "year_range": f"{int(school_df['year'].min())}~{int(school_df['year'].max())}" if not school_df.empty else "",
        "top_signals": top_signals,
        "category_summary": cat_summary,
        "peer_summary": peer_summary,
        "recommends": recommends,
    }


SEOUL_DISTRICTS_25 = [
    "강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천", "노원", "도봉",
    "동대문", "동작", "마포", "서대문", "서초", "성동", "성북", "송파", "양천", "영등포",
    "용산", "은평", "종로", "중", "중랑",
]


def _seoul_25_districts(df: pd.DataFrame) -> list:
    """서울 25개 구 — 보유/미보유 상태 포함"""
    if df is None or df.empty:
        return [{"name": d, "active": False, "schools": 0} for d in SEOUL_DISTRICTS_25]
    out = []
    for d in SEOUL_DISTRICTS_25:
        sub = df[df["district"] == d]
        cnt = int(sub["school_code"].nunique()) if not sub.empty else 0
        out.append({"name": d, "active": cnt > 0, "schools": cnt})
    return out


def _data_basis(df: pd.DataFrame) -> dict:
    """공시 데이터 기준 메타 (대시보드 헤더 문구용)"""
    if df.empty:
        return {}
    years = sorted(int(y) for y in df["year"].unique())
    districts = sorted(df["district"].dropna().unique().tolist())
    return {
        "years": years,
        "year_range": f"{years[0]}~{years[-1]}" if years else "",
        "districts": districts,
        "schools": int(df["school_name"].nunique()),
        "source": "학교알리미 · KESS 공시 데이터",
    }


# ── 공통 6박스 스키마 (룰 단위 + 챗봇 재사용) ──
# 사용자 화면에 안정적으로 표시. LLM 미사용 → 응답 지연/실패에도 깨지지 않음.
# 박스 6개 항목 (요청 사양):
#   1. 핵심 발견 / 2. 수치 변화 / 3. 패턴 해석 / 4. 동료군 맥락 / 5. 정상 예외 가능성 / 6. 확인 권장
# 룰 ID별 정적 가이드 — 3·5·6 박스는 룰 의미 기반 작성. 1·2·4는 detection·school_df로 자동 주입.

SIXBOX_GUIDE = {
    "C1-1": {
        "pattern": "학생수와 학급수가 반대 방향으로 움직임. 자원 배분이 의도된 결과인지 확인 필요.",
        "normal": "학급 신설·통폐합, 학년 정원 조정, 특수학급 별도 운영.",
        "recommend": "학생수·학급수 입력 원본을 함께 확인하고, 학급 신설/통폐합 여부와 학생 전출입을 점검해 주세요.",
    },
    "C1-2": {
        "pattern": "학생수와 학급수가 반대 방향(1학급 변동). C1-1보다 완만한 신호.",
        "normal": "학급 1개 신설/폐지가 자연스러운 학교 규모 조정과 함께 일어난 경우.",
        "recommend": "학급수 1학급 변동의 사유와 학생 전입·전출 추이를 함께 확인해 주세요.",
    },
    "C1-3": {
        "pattern": "학생수는 안정(±5% 이내)인데 강사 제외 교원수가 10% 또는 5명 이상 변동.",
        "normal": "정년 퇴임·임용·인사 발령 시기, 기간제 교원 비율 변화.",
        "recommend": "교원 자격종별 집계 기준(강사 포함 여부)을 확인하고, 정원·기간제·강사 분류를 점검해 주세요.",
    },
    "C1-4": {
        "pattern": "학급수 변동(0~1)은 작은데 강사 제외 교원수가 5명 이상 변동.",
        "normal": "정년·퇴직·이동, 기간제 교원 일괄 임용.",
        "recommend": "교원 분류 정확성과 학급 운영 기준을 함께 확인해 주세요.",
    },
    "C1-5": {
        "pattern": "학생수는 안정인데 보직교사 2명 이상 변동.",
        "normal": "학교 조직 개편, 부장 교사 인사 발령, 보직 분리/통합.",
        "recommend": "보직 인사 발령 사유와 보직 분류 기준을 확인해 주세요.",
    },
    "C1-7": {
        "pattern": "교원1인당학생수가 20% 이상 변동. 학생수 또는 교원수 단독 변화일 가능성.",
        "normal": "학생 신·편입학 또는 교원 정원 조정.",
        "recommend": "학생수·교원수 변화 원인을 분리해서 확인해 주세요.",
    },
    "C1-8": {
        "pattern": "학급당 학생수가 1.5명 이상 급변. 학급수 또는 학생수 파싱 정확성 점검.",
        "normal": "학급 신설/폐지가 학년 중간에 일어난 경우.",
        "recommend": "학급수 원본 표기('28(3)' 등 괄호 포함 시 총학급수 파싱 정확성)를 우선 확인해 주세요.",
    },
    "C3-3A": {
        "pattern": "피해학생 3명 이상 + 보호조치 0건 + 가해학생 존재(선도조치 수행 추정).",
        "normal": "보호조치 미실시 사유, 보호조치 후 입력 누락, 가해학생 별도 조치 미입력.",
        "recommend": "피해학생 보호조치 미실시 사유 또는 미입력 사유를 확인해 주세요. 가해학생 조치 별도 미입력 시 가해학생수 포함 여부도 점검합니다.",
    },
    "C3-3B": {
        "pattern": "피해학생 1~2명 + 보호조치 0건 + 가해학생 존재. C3-3A보다 약한 신호.",
        "normal": "보호조치 입력 누락, 학년도 기준 차이.",
        "recommend": "보호조치·가해조치 입력 누락 여부를 확인하고, 학년도(공시연도 -1) 기준이 맞는지 함께 점검해 주세요.",
    },
    "B1-1": {
        "pattern": "전년 10% 이상 변동 + 직전 2년 평균 대비 3배 — 갑자기 튄 해.",
        "normal": "통폐합·정원 조정·이동, 신·편입학 일시 집중.",
        "recommend": "직전 2년 평균 대비 변동 폭의 사유(통폐합·정원 조정·이동 등)를 확인해 주세요.",
    },
    "B1-2": {
        "pattern": "전년 10% 이상 변동 — 단년 변동(시계열 부족 또는 3배 미만).",
        "normal": "정원 변동, 인사 발령, 학생 일시 집중.",
        "recommend": "변동 사유와 다음 해 추이까지 함께 확인해 주세요.",
    },
    "B1-3": {
        "pattern": "학교회계 세입 또는 세출이 전년 대비 ±30% 변동.",
        "normal": "1회성 사업 반영, 시설 확충, 정부 이전수입 조정.",
        "recommend": "회계 카테고리별 변동 원인과 1회성/경상 구분을 확인해 주세요.",
    },
    "B1-4": {
        "pattern": "학교회계 세입 또는 세출이 ±50% 변동. 강한 변동.",
        "normal": "대규모 시설 사업, 정책 자금 일괄 반영.",
        "recommend": "변동 카테고리·금액·사업명을 함께 확인해 주세요.",
    },
    "B1-5": {
        "pattern": "진학률 15%p 이상 변동. 20%p 이상이면 강한 신호.",
        "normal": "졸업생 분모 변동, 진학 분류 기준 변경(전문대·해외대 포함 여부).",
        "recommend": "진학률 산정 분모와 진학 분류 기준을 확인해 주세요.",
    },
    "B1-6": {
        "pattern": "학폭 심의 건수 0~1건에서 5건 이상으로 급증.",
        "normal": "특정 사안 집중 발생, 학년도(공시연도 -1) 기준 차이.",
        "recommend": "학폭 심의 건수의 학년도 기준(공시연도 -1)을 확인하고, 실태조사 결과와 함께 점검해 주세요.",
    },
    "C2-3": {
        "pattern": "학생수 안정(±5%)인데 급식비 ±10% 변동.",
        "normal": "단가 변동, 운영 업체 변경, 식수 일시 변동.",
        "recommend": "급식비 입력단위(천원)와 사업 항목 분류를 확인해 주세요.",
    },
    "C2-3+": {
        "pattern": "급식비 ±30% 강한 변동. 단위 혼동(천원↔원) 가능성.",
        "normal": "1회성 사업, 정책 자금 반영.",
        "recommend": "급식비 단위 혼동 또는 1회성 사업 반영 여부를 우선 확인해 주세요.",
    },
    "D2-1": {
        "pattern": "동료군(같은 구·연도) 백분위 10% 미만 또는 90% 초과 — 상위·하위 10%.",
        "normal": "학교 유형 특성(소규모·예술·특수), 시설 차이.",
        "recommend": "학교 유형 특성을 고려해 동료군 정의가 적정한지 확인해 주세요.",
    },
    "D2-2": {
        "pattern": "IQR 1.5배 외부 또는 중앙값 대비 50% 이상 차이 — 강한 극단값.",
        "normal": "학교 유형(소규모·예술고 등), 시설 차이, 사업 특성.",
        "recommend": "학교 유형 특성에 따른 정상 예외 가능성을 확인하고, 동료군 정의가 적정한지 함께 점검해 주세요.",
    },
    "C5-1": {
        "pattern": "전년 1학년 → 당해 2학년 진급률이 -7~+3% 범위 밖. 자퇴·전출 집중 가능.",
        "normal": "신·편입학·전출입·자퇴·휴학·검정고시 전환.",
        "recommend": "신·편입학, 전출·전입, 자퇴·휴학 등 학생수 변동 사유를 확인해 주세요.",
    },
    "E1-1": {
        "pattern": "시설 컬럼이 3년 연속 미입력(NaN). 미입력 vs 시설 미보유 구분이 어려움.",
        "normal": "해당 시설 미보유, 입력 항목 누락.",
        "recommend": "해당 시설의 보유 여부와 입력 누락 여부를 확인해 주세요.",
    },
    "E1-2": {
        "pattern": "동료군 입력률 90% 이상인데 본교만 미입력 — 단독 누락 신호.",
        "normal": "본교만 해당 시설 미보유, 입력 시기 차이.",
        "recommend": "동료군 대비 본교만 미입력한 사유와 시설 보유 여부를 확인해 주세요.",
    },
    "E1-3": {
        "pattern": "공시 의무 항목 미제출 점검 — 현재 학교 유형별 의무 항목 매핑 확인 대기.",
        "normal": "—",
        "recommend": "학교 유형별 공시 의무 항목 매핑이 확정되면 실 탐지를 수행합니다.",
    },
    "E2-2": {
        "pattern": "3년간 동일 수치 반복(노이즈 정수 0~5 제외). 미갱신 가능성 점검.",
        "normal": "실제 변동 없음, 단위·산식·반올림 동일 유지.",
        "recommend": "3년간 동일 수치가 실제 변동 없음인지, 입력 갱신 누락인지 확인해 주세요.",
    },
    "F1'-1": {
        "pattern": "학교알리미(강사 제외) vs KESS 교원수 차이 3명 이상.",
        "normal": "강사 포함/미포함 기준 차이, 발령 시점 차이.",
        "recommend": "학교알리미 교원총계에서 강사를 제외하고 KESS와 비교해 주세요.",
    },
    "G1-1": {
        "pattern": "단년 변동은 작지만 다년에 걸쳐 같은 방향으로 누적 8% 이상 변화. B1 단년 급변에는 안 잡히는 누적 변화를 점검합니다. (m_r = 본교 변동폭 / 전체평균변동폭)",
        "normal": "지역 인구 변화, 학교 운영 변화, 정책 영향, 물가 상승.",
        "recommend": "다년 누적 변동의 사유(인구·운영·정책·물가 등)를 함께 확인하고 추세를 지속 모니터링해 주세요.",
    },
    "G1-2": {
        "pattern": "전체 평균과 같은 방향이지만 변동폭이 평균의 2배 이상으로 빠른 추세. (m_r = 본교 변동폭 / 전체평균변동폭)",
        "normal": "지역 단위 정책·인구 변화의 영향이 평균보다 본교에 강하게 누적되는 경우.",
        "recommend": "동료군 평균과의 격차가 누적되는 사유를 점검하고, 동일 방향의 다른 지표도 함께 비교해 주세요.",
    },
}


def _build_sixbox(rule, school_df, full_df, district):
    """공통 6박스 — 룰 1개 + 학교 시계열 → 6박스 dict.
    LLM 미사용. detection·school_df 데이터로 자동 주입. 룰 ID 미등록 시 기본 가이드."""
    rid = rule.get("rule_id", "")
    guide = SIXBOX_GUIDE.get(rid, {
        "pattern": "본교 수치와 동료군 추이를 함께 확인할 신호입니다.",
        "normal": "—",
        "recommend": "본교 값과 동료군 값을 비교하여 정상 예외 가능성과 입력 정확성을 확인해 주세요.",
    })
    # 1. 핵심 발견 — detection.detail (또는 룰명 기반 폴백)
    finding = rule.get("detail", "") or rule.get("rule_name_ko", "")
    # 2. 수치 변화 — col_pairs 첫 컬럼의 3년 시계열
    numbers = ""
    if not school_df.empty and rule.get("col_keys"):
        first_key = rule["col_keys"][0]
        if first_key in school_df.columns:
            yrs = sorted(school_df["year"].unique())
            vals = []
            for y in yrs:
                row = school_df[school_df["year"] == y]
                if not row.empty and pd.notna(row[first_key].iloc[0]):
                    v = float(row[first_key].iloc[0])
                    vals.append(_fmt_val(first_key, v))
                else:
                    vals.append("—")
            label = rule.get("col_labels", [first_key])[0] if rule.get("col_labels") else first_key
            numbers = f"{label}: " + " → ".join(vals)
    # 3. 패턴 해석 — 룰 정적 가이드
    pattern = guide["pattern"]
    # 4. 동료군 맥락 — col_pairs 첫 컬럼의 동료군 평균(최근 연도)
    peer = ""
    if not school_df.empty and rule.get("col_keys") and district:
        first_key = rule["col_keys"][0]
        peer_col = f"{first_key}_dist_mean"
        if peer_col in school_df.columns:
            last_yr = sorted(school_df["year"].unique())[-1]
            last_row = school_df[school_df["year"] == last_yr]
            if not last_row.empty and pd.notna(last_row[peer_col].iloc[0]):
                p = float(last_row[peer_col].iloc[0])
                self_v = last_row[first_key].iloc[0] if first_key in last_row.columns and pd.notna(last_row[first_key].iloc[0]) else None
                label = rule.get("col_labels", [first_key])[0] if rule.get("col_labels") else first_key
                if self_v is not None:
                    s = float(self_v)
                    diff_pct = ((s - p) / p * 100) if p != 0 else 0
                    sign = "+" if diff_pct >= 0 else ""
                    peer = f"{district}구 동료군 평균 {_fmt_val(first_key, p)} 대비 본교 {_fmt_val(first_key, s)} ({sign}{diff_pct:.1f}%)"
                else:
                    peer = f"{district}구 동료군 평균 {_fmt_val(first_key, p)}"
    if not peer:
        peer = "동료군 비교 데이터가 제한적입니다."
    # 5. 정상 예외 가능성 — 룰 정적 가이드
    normal = guide["normal"]
    # 6. 확인 권장 — 룰 정적 가이드
    recommend = guide["recommend"]
    return {
        "finding": finding,
        "numbers": numbers,
        "pattern": pattern,
        "peer": peer,
        "normal": normal,
        "recommend": recommend,
    }


# 룰 → 관련 컬럼 매핑 (Evidence Chart·룰 카드 표시용)
RULE_COLUMNS = {
    "C1-1":  [("학생수","student_count"), ("학급수","class_count")],
    "C1-2":  [("학생수","student_count"), ("학급수","class_count")],
    # C1-3 / C1-4: 탐지 조건이 강사 제외 교원수(teacher_count_no_instructor) 기준이므로
    # Evidence·차트도 강사 제외 컬럼으로 통일.
    "C1-3":  [("학생수","student_count"), ("교원수(강사제외)","teacher_count_no_instructor")],
    "C1-4":  [("학급수","class_count"), ("교원수(강사제외)","teacher_count_no_instructor")],
    "C1-5":  [("학생수","student_count"), ("보직교사수","head_teacher_count")],
    "C1-7":  [("교원1인당학생수","students_per_teacher")],
    "C1-8":  [("학급당학생수","students_per_class")],
    "C3-3A": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "C3-3B": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "B1-1":  [("학생수","student_count"), ("학급수","class_count"), ("교원수","teacher_count")],
    "B1-2":  [("학생수","student_count"), ("학급수","class_count"), ("교원수","teacher_count")],
    "B1-3":  [("학교회계 세입","budget_revenue"), ("학교회계 세출","budget_expense")],
    "B1-4":  [("학교회계 세입","budget_revenue"), ("학교회계 세출","budget_expense")],
    "B1-5":  [("진학률(%)","graduation_rate")],
    "B1-6":  [("학폭 심의건수","bullying_cases")],
    "C2-3":  [("급식비총액(원)","meal_cost_total"), ("학생수","student_count")],
    "C2-3+": [("급식비총액(원)","meal_cost_total"), ("학생수","student_count")],
    "D2-1":  [("학급당학생수","students_per_class"), ("교원1인당학생수","students_per_teacher"), ("1인당급식비(원)","meal_cost_per_student")],
    "D2-2":  [("학급당학생수","students_per_class"), ("교원1인당학생수","students_per_teacher"), ("1인당급식비(원)","meal_cost_per_student")],
    "C5-1":  [("1학년 학생수","grade1_students"), ("2학년 학생수","grade2_students")],
    "E1-1":  [],   # 시설 미입력 — 차트로 표현하지 않음
    "E1-2":  [],
    "E1-3":  [],
    "E2-2":  [("학급수","class_count"), ("교원수","teacher_count"), ("학생수","student_count")],
    "F1'-1": [("교원수(강사제외)","teacher_count_no_instructor")],
    # G1: 7개 지표 — 실제 detection.values["col_key"]가 단일 지표를 지정. 기본값은 7개 전부.
    "G1-1":  [("학생수","student_count"), ("교원수","teacher_count"), ("학급수","class_count"),
              ("학급당학생수","students_per_class"), ("학폭 심의건수","bullying_cases"),
              ("진학률(%)","graduation_rate"), ("1인당급식비(원)","meal_cost_per_student")],
    "G1-2":  [("학생수","student_count"), ("교원수","teacher_count"), ("학급수","class_count"),
              ("학급당학생수","students_per_class"), ("학폭 심의건수","bullying_cases"),
              ("진학률(%)","graduation_rate"), ("1인당급식비(원)","meal_cost_per_student")],
}


def _build_detection_cards(detections_df, school_df, full_df, district) -> list:
    """카테고리별 그룹핑 + 관련 컬럼 데이터 포함"""
    years = sorted(school_df["year"].unique()) if not school_df.empty else []

    # 카테고리별 그룹핑
    cat_groups = {}
    for _, d in detections_df.iterrows():
        rid = d.get("rule_id", "")
        cat = _get_category_code(rid)
        cat_ko = CATEGORY_NAMES_KO.get(cat, cat)
        if cat_ko not in cat_groups:
            cat_groups[cat_ko] = {"category_ko": cat_ko, "cat_code": cat,
                                  "max_star": 0, "max_sr": 0.0, "rules": [], "years_set": set()}
        star = int(d.get("star", 0))
        # s_r: 탐지 건 점수 (v4). 정렬·강조 기준은 s_r 단일 출처.
        try:
            s_r_val = float(d.get("s_r", 0) or 0)
        except (TypeError, ValueError):
            s_r_val = 0.0
        cat_groups[cat_ko]["max_star"] = max(cat_groups[cat_ko]["max_star"], star)
        cat_groups[cat_ko]["max_sr"] = max(cat_groups[cat_ko]["max_sr"], s_r_val)
        cat_groups[cat_ko]["years_set"].add(int(d.get("year", 0)))
        # detection 단위 동적 col_label/col_key (E2-2, D2, B1, E1 등). 없으면 RULE_COLUMNS 기본.
        dyn_label = str(d.get("col_label", "") or "").strip()
        dyn_key = str(d.get("col_key", "") or "").strip()
        if dyn_key:
            # 매칭은 col_key, 표시는 한국어 라벨 — 짝 보존
            col_pairs = [{"key": dyn_key, "label": _canon_label(dyn_label or dyn_key)}]
        else:
            col_pairs = [{"key": k, "label": _canon_label(label)}
                         for label, k in RULE_COLUMNS.get(rid, [])]
        col_keys = [p["key"] for p in col_pairs]
        col_labels = [p["label"] for p in col_pairs]
        rule_obj = {
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
            "year": int(d.get("year", 0)),
            "star": star,                  # 내부 메타 (UI 분기 X, 백워드 호환)
            "s_r": round(s_r_val, 2),      # 탐지 건 점수 (v4 정렬·라벨 단일 출처)
            "detail": d.get("detail", ""),
            "col_labels": col_labels,    # 표시용 (한국어)
            "col_keys": col_keys,        # 매칭용 (영문 컬럼 ID)
            "col_pairs": col_pairs,      # 짝 보존 — 프론트 차트가 우선 사용
        }
        rule_obj["sixbox"] = _build_sixbox(rule_obj, school_df, full_df, district)
        cat_groups[cat_ko]["rules"].append(rule_obj)

    # 카테고리별로 관련 컬럼 데이터 테이블 생성
    # — detection 단위 col_label/col_key가 있는 룰은 그 컬럼 우선, 없으면 RULE_COLUMNS 기본
    detections_by_cat = {}
    for _, d in detections_df.iterrows():
        c_code = _get_category_code(d.get("rule_id", ""))
        c_ko = CATEGORY_NAMES_KO.get(c_code, c_code)
        detections_by_cat.setdefault(c_ko, []).append(d)

    result = []
    # 카테고리 정렬: 최고 s_r(탐지 건 점수) 우선, 동률은 max_star로 폴백
    for cat_ko, group in sorted(cat_groups.items(),
                                key=lambda x: (-x[1].get("max_sr", 0.0), -x[1].get("max_star", 0))):
        # 관련 컬럼 수집
        related_cols = set()
        for d in detections_by_cat.get(cat_ko, []):
            dyn_key = str(d.get("col_key", "") or "").strip()
            dyn_label = str(d.get("col_label", "") or "").strip()
            if dyn_key and dyn_label:
                related_cols.add((_canon_label(dyn_label), dyn_key))
            else:
                for label, col in RULE_COLUMNS.get(d.get("rule_id", ""), []):
                    related_cols.add((_canon_label(label), col))

        # 미니 데이터 테이블 (해당 카테고리 컬럼만)
        mini_table = []
        for label, col in sorted(related_cols):
            if col not in school_df.columns:
                continue
            row = {"지표": label}
            for yr in years:
                yr_data = school_df[school_df["year"] == yr]
                val = yr_data[col].iloc[0] if not yr_data.empty and pd.notna(yr_data[col].iloc[0]) else None
                if val is not None:
                    val = round(float(val), 1) if float(val) != int(float(val)) else int(float(val))
                row[str(int(yr))] = val
            # 동료군 평균
            peer_col = f"{col}_dist_mean"
            if peer_col in school_df.columns and years:
                last = school_df[school_df["year"] == years[-1]]
                if not last.empty and pd.notna(last[peer_col].iloc[0]):
                    row["동료군"] = round(float(last[peer_col].iloc[0]), 1)
                else:
                    row["동료군"] = None
            else:
                row["동료군"] = None
            mini_table.append(row)

        group["data_table"] = mini_table
        group["col_keys"] = [col for _, col in sorted(related_cols)]  # 기존 컬럼 키 목록
        group["years"] = sorted(group["years_set"])
        group["is_repeat"] = len(group["years_set"]) >= 3
        del group["years_set"]
        result.append(group)

    return result


# ── Gemini 호출 함수 ──

class GeminiError(Exception):
    """Gemini 호출 실패 (지연·할당량·503 등). 호출부에서 폴백 처리."""
    pass


def _call_gemini(client, prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """Gemini API 호출 공통 함수. 실패 시 GeminiError."""
    full_prompt = f"{system}\n\n---\n\n{prompt}"
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
        )
        text = getattr(response, "text", None)
        if not text:
            raise GeminiError("응답 본문이 비어 있음")
        return text
    except GeminiError:
        raise
    except Exception as e:
        # 503·할당량·지연·네트워크 등 모두 GeminiError로 표준화
        raise GeminiError(f"{type(e).__name__}: {e}")


# 사용자 노출용 안전 폴백 메시지 — 임의 수치 만들지 않음. 톤·표현 통일.
FALLBACK_AI_TEXT = "AI 보조 해석을 불러오지 못했습니다. 원천 데이터와 검토 신호를 기준으로 확인해 주세요."
FALLBACK_CATEGORY = {
    "해석": "AI 보조 해석을 불러오지 못했습니다. 원천 데이터와 검토 신호를 기준으로 확인해 주세요.",
    "정상사유": "",
    "확인권장": "본 화면의 검토 후보 수치를 직접 확인해 주세요.",
}


def _explain_category(client, card: dict, school_name: str, district: str) -> dict:
    """카테고리별 AI 해석 (해석/정상사유/확인권장)"""
    rules_text = "\n".join(f"- {r['year']}년: {r['detail']}" for r in card["rules"])
    table_text = ""
    if card.get("data_table"):
        for row in card["data_table"]:
            vals = " / ".join(f"{k}:{v}" for k, v in row.items() if k != "지표" and v is not None)
            table_text += f"  {row['지표']}: {vals}\n"

    prompt = f"""{school_name}({district}구) "{card['category_ko']}" 카테고리에서 추출된 검토 후보 요약:
{rules_text}
{table_text}
정확히 3줄만 응답. 장황하게 쓰지 마. 숫자 위주로 간결하게.
해석: (동료군 대비 핵심 차이 1문장)
정상사유: (가능한 이유 2~3개, 쉼표 구분)
확인권장: (담당자 행동 1문장)

주의: "이상하다", "비정상", "오류" 같은 단정형 표현 금지. "{school_name}"을 그대로 사용하고 다른 학교명·플레이스홀더 만들지 마."""

    text = _call_gemini(client, prompt)
    result = {"해석": "", "정상사유": "", "확인권장": ""}
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("해석:"):
            result["해석"] = line[3:].strip()
        elif line.startswith("정상사유:") or line.startswith("정상 사유:"):
            result["정상사유"] = line.split(":", 1)[1].strip()
        elif line.startswith("확인권장:") or line.startswith("확인 권장:"):
            result["확인권장"] = line.split(":", 1)[1].strip()
    # 파싱 실패 시 전체를 해석에
    if not result["해석"]:
        result["해석"] = text.strip()[:200]
    return result


def _explain_with_gemini(client, detections_df, school_df) -> str:
    det_list = []
    for _, d in detections_df.iterrows():
        rid = d.get("rule_id", "")
        det_list.append(f"{d.get('year')}년 {RULE_NAMES_KO.get(rid, rid)}: {d.get('detail','')}")
    det_text = " / ".join(det_list)

    prompt = f"""아래 학교의 검토 후보 요약을 작성해주세요.

검토 후보 항목: {det_text}

반드시 아래 형식으로 3줄 응답 (각 줄 30~50자, 구체적 수치 포함):
① [가장 우선] 어떤 지표에서 어떤 검토 신호가 추출됐는지 수치와 함께 설명
② [패턴] 몇 년 연속인지, 몇 개 영역에서 동시 검토 후보로 잡혔는지 등
③ [확인 권장] 담당자가 구체적으로 무엇을 확인해야 하는지

주의: "이상하다", "비정상", "오류" 같은 단정형 표현 금지. "검토 신호 / 확인 필요" 사용."""
    return _call_gemini(client, prompt)


def _build_school_context(school_code: str, df_full: pd.DataFrame,
                          scores: pd.DataFrame, detections: pd.DataFrame) -> dict | None:
    """학교 상세 챗봇용 — 그 학교의 메타·최신 지표·종합점수·탐지 룰 요약.
    LLM이 '유사학교/왜 검토대상/1분 브리핑' 같은 맥락 의존 질의에 답할 근거를 제공."""
    if not school_code or df_full is None or df_full.empty:
        return None
    sdf = df_full[df_full["school_code"].astype(str) == str(school_code)]
    if sdf.empty:
        return None
    latest = sdf.sort_values("year").tail(1).iloc[0]
    out = {
        "school_code": str(school_code),
        "school_name": str(latest.get("school_name", "")),
        "district": str(latest.get("district", "")),
        "school_type": str(latest.get("school_type", "")),
        "year_latest": int(latest.get("year", 0)) if pd.notna(latest.get("year")) else None,
        "student_count": int(latest["student_count"]) if pd.notna(latest.get("student_count")) else None,
        "class_count": int(latest["class_count"]) if pd.notna(latest.get("class_count")) else None,
        "teacher_count": int(latest["teacher_count"]) if pd.notna(latest.get("teacher_count")) else None,
    }
    # 종합점수·순위
    if scores is not None and not scores.empty:
        sc = scores[scores["school_code"].astype(str) == str(school_code)]
        if not sc.empty:
            r = sc.iloc[0]
            out["score"] = float(r.get("score")) if pd.notna(r.get("score")) else None
            out["rank"] = int(r.get("rank")) if pd.notna(r.get("rank")) else None
            out["num_detections"] = int(r.get("num_detections", 0))
            out["num_categories"] = int(r.get("num_categories", 0))
    # 탐지 룰 요약 (상위 8)
    rules_brief: list = []
    if detections is not None and not detections.empty:
        det = detections[detections["school_code"].astype(str) == str(school_code)].copy()
        if not det.empty:
            if "s_r" in det.columns:
                det["s_r"] = pd.to_numeric(det["s_r"], errors="coerce").fillna(0)
            det = det.sort_values("s_r", ascending=False)
            seen = set()
            for _, r in det.iterrows():
                rid = str(r.get("rule_id", ""))
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                rules_brief.append({
                    "rule_id": rid,
                    "rule_name": RULE_NAMES_KO.get(rid, rid),
                    "year": int(r.get("year", 0)) if pd.notna(r.get("year")) else None,
                    "s_r": round(float(r.get("s_r", 0)), 2),
                })
                if len(rules_brief) >= 8:
                    break
    out["rules"] = rules_brief
    return out


def _format_school_context(ctx: dict) -> str:
    """학교 상세 챗봇용 prompt 단락. df는 그 학교 행들만 담고 있다는 전제 명시."""
    if not ctx or not ctx.get("school_code"):
        return ""
    parts = ["[학교 컨텍스트] 사용자는 학교 상세 화면에서 다음 학교에 대해 묻고 있다."]
    parts.append(
        f"- 학교: {ctx.get('school_name','')} ({ctx.get('district','')}구, "
        f"{ctx.get('school_type','')}, 코드 {ctx.get('school_code','')})"
    )
    yl = ctx.get("year_latest")
    stu = ctx.get("student_count")
    cls = ctx.get("class_count")
    tch = ctx.get("teacher_count")
    if yl:
        parts.append(f"- 최신 연도({yl}): 학생수 {stu if stu is not None else '-'} · "
                     f"학급수 {cls if cls is not None else '-'} · 교원수 {tch if tch is not None else '-'}")
    sc = ctx.get("score")
    rk = ctx.get("rank")
    if sc is not None:
        parts.append(f"- 검토 우선도 지수: {sc:.1f} (전체 순위 {rk}위, "
                     f"검토 신호 {ctx.get('num_detections',0)}건, "
                     f"카테고리 {ctx.get('num_categories',0)}개)")
    rules = ctx.get("rules") or []
    if rules:
        parts.append("- 이 학교 탐지 룰 (s_r 내림차순, 상위 {0}건):".format(len(rules)))
        for r in rules:
            parts.append(f"  · {r['rule_id']} {r['rule_name']} (연도 {r.get('year','-')}, s_r {r['s_r']:.2f})")
    parts.append("")
    parts.append("[df 구조] df는 이 학교의 2023~2025년 행만 담는다(최대 3행).")
    parts.append("[지시]")
    parts.append("- '유사학교/동료군/비교' 질의 → 같은 행 안의 *_dist_mean 컬럼이 이미 같은 구·연도의 평균(transform).")
    parts.append("  자기 값(student_count 등) vs 같은 행의 _dist_mean을 비교해 결과 DataFrame을 만든다. 다른 학교 row를 찾지 말 것.")
    parts.append("- '왜 검토 대상/검토 신호/이유' 질의 → 위 '이 학교 탐지 룰' 목록을 근거로 plan·report 작성.")
    parts.append("- '1분 브리핑/요약' 질의 → 학생수·학급·교원·검토 우선도·대표 신호 한 문단 요약. result는 df.tail(1) 등으로 최소 1행 DataFrame.")
    parts.append("- result는 항상 DataFrame이어야 한다 (빈 결과 안 됨).")
    parts.append("[주의] 위 '이 학교 탐지 룰' 목록의 rule_id·rule_name·s_r은 prompt 컨텍스트일 뿐 df 컬럼이 아니다. "
                 "df에 없는 컬럼(특히 's_r','rule_id','rule_name')을 코드에서 호출 금지 — KeyError 발생. "
                 "이런 정보를 인용하려면 plan/report 텍스트에서 직접 언급하되 result는 df의 실제 컬럼으로만 만든다.")
    parts.append("")
    parts.append("[코드 예시 — 그대로 변형해 사용]")
    parts.append("# 유사학교/동료군 비교")
    parts.append("# latest = df.sort_values('year').tail(1)")
    parts.append("# cols = ['student_count','class_count','teacher_count','students_per_class','students_per_teacher']")
    parts.append("# rows = []")
    parts.append("# for c in cols:")
    parts.append("#     dm = c + '_dist_mean'")
    parts.append("#     if dm in latest.columns:")
    parts.append("#         rows.append({'지표': c, '본교': latest[c].iloc[0], '동료군 평균': latest[dm].iloc[0]})")
    parts.append("# result = pd.DataFrame(rows)")
    parts.append("")
    parts.append("# 1분 브리핑 / 왜 검토 대상 — 학교 메타 + 최신 지표")
    parts.append("# result = df.sort_values('year').tail(1)[['school_name','district','school_type','year','student_count','class_count','teacher_count','score','rank']]")
    return "\n".join(parts)


def _format_rule_context(ctx: dict) -> str:
    """rule_lookup 컨텍스트(룰+학교)를 LLM prompt용 단락으로 직렬화.
    LLM이 추측하지 않고 주어진 학교 목록 위에서만 조건을 적용하도록 강제."""
    if not ctx or not ctx.get("rule_ids"):
        return ""
    parts = []
    parts.append(f"[탐지 룰 컨텍스트] 사용자가 가리킨 룰: {ctx.get('display_key','')}")
    for rn in ctx.get("rule_names", []):
        rid = rn.get("rule_id", "")
        name = rn.get("rule_name_ko", "")
        guide = rn.get("guide", "")
        parts.append(f"- {rid} {name}" + (f": {guide}" if guide else ""))
    schools = ctx.get("schools", [])
    if schools:
        parts.append(f"\n[이 룰에 걸린 학교 — 표본 안 {len(schools)}교, max s_r 내림차순]")
        parts.append("학교코드 | 학교명 | 지역구 | 탐지연도 | 학생수(최신) | max_sr | 학교종합점수")
        for s in schools[:80]:
            yrs = ",".join(str(y) for y in (s.get("years") or [])) or "-"
            stu = s.get("student_count")
            stu_txt = str(stu) if stu is not None else "-"
            sc = s.get("school_score")
            sc_txt = f"{sc:.1f}" if sc is not None else "-"
            parts.append(f"{s.get('school_code','')} | {s.get('school_name','')} | "
                         f"{s.get('district','')} | {yrs} | {stu_txt} | "
                         f"{s.get('max_sr',0):.2f} | {sc_txt}")
        if len(schools) > 80:
            parts.append(f"... (외 {len(schools) - 80}교 생략)")
        parts.append("")
        parts.append("[지시] 위 학교 목록만 사용하라. 룰 식별자를 지역/지표로 오해하지 말 것.")
        parts.append("사용자 추가 조건(학생수/연도/지역/정렬)에 맞춰 위 목록을 필터·정렬해서 답하라.")
        parts.append("[컬럼 매핑] '점수'·'우선도'·'순위' 질의는 df['score'] / df['rank'] 사용. "
                     "위 학교 목록의 school_code 집합으로 먼저 필터한 뒤 df['score'] 내림차순.")
        parts.append("[주의] 위 표의 max_sr·학교종합점수·탐지연도는 prompt 컨텍스트일 뿐 df 컬럼이 아니다. "
                     "df에 없는 컬럼(특히 'max_sr','s_r','rule_id','rule_ids','rule_name','rule_names','school_score')을 코드에서 호출 금지 — KeyError 발생. "
                     "df의 실제 컬럼만 사용: df['score']·df['rank']·df['student_count']·df['district'] 등.")
    else:
        parts.append("\n[참고] 이 룰의 표본 안 탐지는 0건이다. 외부 지식으로 추측 답변 금지.")
    return "\n".join(parts)


def _get_analysis_plan(client, query: str, columns: dict, history: list,
                       rule_context: dict | None = None,
                       school_context: dict | None = None) -> dict:
    cols_text = "\n".join(f"- {k}: {v}" for k, v in columns.items())
    history_text = "\n".join(f"Q: {h.get('query','')} → {h.get('summary','')}" for h in history[-3:]) if history else "없음"

    ctx_block = _format_rule_context(rule_context) if rule_context else ""
    ctx_section = f"\n\n{ctx_block}\n" if ctx_block else ""

    school_block = _format_school_context(school_context) if school_context else ""
    school_section = f"\n\n{school_block}\n" if school_block else ""

    prompt = f"""사용자 질문을 분석하여 pandas DataFrame 분석 계획을 JSON으로 작성하세요.

[사용 가능한 컬럼]
{cols_text}

[이전 대화]
{history_text}{ctx_section}{school_section}

[사용자 질문]
{query}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{
    "analysis_plan": "분석 계획 설명",
    "columns_used": ["컬럼1", "컬럼2"],
    "criteria": "적용할 수치 기준과 근거",
    "pandas_code": "result = df[...].groupby(...)...",
    "comparison": "비교 기준",
    "confidence": "높음/중간/낮음"
}}

pandas_code 규칙:
- 결과를 반드시 result 변수(pandas DataFrame)에 할당
- df 변수 사용 (pd, np 사용 가능)
- import 금지, 파일 I/O 금지
- 최대 10줄 이내
- NaN 처리: .dropna() 또는 .fillna() 사용
- 룰 컨텍스트가 있으면 그 컨텍스트의 school_code 집합만 대상으로 필터 (df[df['school_code'].isin([...])]) 후 추가 조건 적용
- 학교 컨텍스트가 있으면 df는 그 학교 행만 담고 있다. '유사학교/동료군' 질의는 같은 행 안의 *_dist_mean 컬럼(같은 구·연도 평균)과 자기 값을 비교 — 다른 학교 row를 찾지 말 것. result는 항상 DataFrame (최소한 학교 최신 1행이라도)."""

    text = _call_gemini(client, prompt, SYSTEM_PROMPT + "\n\n반드시 JSON 형식으로만 응답하세요. 마크다운 코드블록 없이 순수 JSON만.")

    # JSON 추출
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 정책: 기본 코드(데이터표 head) fallback 폐기.
        # 파싱 실패는 "정상 결과"가 아니라 실패 → 호출부에서 안내 응답으로 처리.
        print(f"[WARN] _get_analysis_plan JSON 파싱 실패. 원본 앞부분: {text[:200]}")
        return None


def _generate_report(client, plan: dict, result_data: list, query: str, school_name: str = "") -> str:
    # 실명 정책: school_name이 있으면 그것만, 없으면 result_data의 실제 학교명만 사용 — 플레이스홀더 절대 금지
    if school_name:
        target_clause = f"[대상 학교] {school_name}"
        name_rule = (
            f'주의: "[학교명]", "OO고등학교", "A고등학교" 같은 플레이스홀더/가명 금지. '
            f'반드시 "{school_name}"을 그대로 사용하고, 다른 학교명을 만들어내지 마.'
        )
        intro = f"아래는 {school_name}의 검토 후보 분석 결과입니다."
        finding_clause = f'1. 핵심 발견 — {school_name}의 구체적 수치를 포함하여 1문장'
    else:
        target_clause = "[대상] 복수 학교 비교 (특정 학교 지정 없음)"
        name_rule = (
            '주의: 학교명은 [분석 결과] result_data 안의 실제 학교명을 그대로 사용. '
            '"OO고등학교", "[학교명]", "A고등학교" 같은 플레이스홀더·가명 절대 금지. '
            '데이터에 없는 학교명을 만들어내지 마.'
        )
        intro = "아래는 검토 후보 분석 결과입니다."
        finding_clause = "1. 핵심 발견 — 결과 데이터의 실제 학교명과 구체적 수치를 포함하여 1문장"

    prompt = f"""{intro} 담당자를 위한 보고서를 작성해주세요.

{target_clause}
[사용자 질문] {query}
[분석 계획] {plan.get('analysis_plan', '')}
[적용 기준] {plan.get('criteria', '')}
[분석 결과]
{json.dumps(result_data[:10], ensure_ascii=False, indent=2, default=str)}

보고서 형식:
{finding_clause}
2. 상세 결과 — 연도별 수치 나열 (학교명 명시)
3. 맥락 해석 — 동료군 대비 비교
4. 확인 권장 사항

{name_rule}
"이상치", "비정상" 단정 표현 금지. "검토 후보 / 검토 신호 / 확인 필요" 사용."""
    return _call_gemini(client, prompt)


def _generate_suggestions(plan: dict, result_data: list) -> list:
    suggestions = []
    columns = plan.get("columns_used", [])
    if "teacher_count" in columns and "student_count" in columns:
        suggestions.append("여기에 급식비도 추가해서 보고 싶어")
    if "student_count" in columns:
        suggestions.append("학생수 3년 추이를 보여줘")
    suggestions.append("강남구만 따로 볼 수 있어?")
    suggestions.append("이 학교 전체 룰 점검 결과 보여줘")
    return suggestions[:4]


def _fallback_analysis(query: str, df: pd.DataFrame, columns: dict) -> ChatResponse:
    """Gemini 비활성 시 호출되던 함수. 정책 변경: 기본 데이터표 반환 금지 → 안내 응답."""
    return ChatResponse(**_build_fallback_help_response())


if __name__ == "__main__":
    import uvicorn
    # 포트는 환경변수 PORT 우선 (Railway/Heroku/Render 등은 동적 주입)
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
