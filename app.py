"""
app.py — FastAPI 메인 서버
EduData Watch 프로토타입 백엔드
LLM: Google Gemini 2.5 flash-lite (.env에서 키 로드)
"""

import os
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from data_loader import load_and_merge_all, get_column_descriptions
from rule_engine import RuleEngine
from priority_scorer import calculate_priority_scores, get_top_n, get_score_distribution
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
- 절대 판정하지 마세요 ("오류다", "잘못이다" 금지)
- "검토 후보", "확인 권장" 표현만 사용
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
        scores = calculate_priority_scores(detections)

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
    "bullying_cases": "학폭건수", "bullying_victims": "피해학생수",
    "bullying_protection": "보호조치건수", "bullying_perpetrators": "가해학생수",
    "graduation_rate": "진학률(%)", "meal_cost_total": "급식비총액",
    "meal_cost_per_student": "1인당급식비",
    "teacher_total_position": "교원총계(직위별)", "instructor_count": "강사수",
    "teacher_count_no_instructor": "교원수(강사제외)",
    "kess_student_count": "KESS학생수", "kess_teacher_total": "KESS교원수",
    "student_count_yoy": "학생수변동률(%)", "class_count_yoy": "학급수변동률(%)",
    "teacher_count_yoy": "교원수변동률(%)", "meal_cost_total_yoy": "급식비변동률(%)",
}

def _rename_cols_ko(data_list: list) -> list:
    """결과 데이터의 영어 컬럼명을 한국어로 변환"""
    result = []
    for row in data_list:
        new_row = {}
        for k, v in row.items():
            # _dist_mean 등 내부 컬럼 제거
            if k.endswith("_dist_mean") or k.endswith("_dist_median") or k.startswith("school_name_"):
                continue
            ko = COL_KO.get(k, k)
            new_row[ko] = v
        result.append(new_row)
    return result


@app.get("/api/dashboard")
async def dashboard():
    scores = app_state.get("scores", pd.DataFrame())
    if scores.empty:
        return {"top3": [], "distribution": {}, "total_detections": 0, "total_schools": 0}

    top3 = get_top_n(scores, 3)
    dist = get_score_distribution(scores)
    detections = app_state.get("detections", pd.DataFrame())

    return {
        "top3": top3.to_dict(orient="records"),
        "distribution": dist,
        "total_detections": len(detections),
        "total_schools": len(scores),
        "zero_schools": len(scores[scores["score"] < 6]) if not scores.empty else 0,
    }


@app.get("/api/schools")
async def school_list():
    scores = app_state.get("scores", pd.DataFrame())
    if scores.empty:
        return []
    return scores[["school_code", "school_name", "score", "rank", "num_categories"]].to_dict(orient="records")


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
        "score": int(school_score["score"].iloc[0]) if not school_score.empty else 0,
        "rank": int(school_score["rank"].iloc[0]) if not school_score.empty else 0,
        "is_repeat": bool(school_score["is_repeat"].iloc[0]) if not school_score.empty else False,
        "summary": {
            "detections": len(school_det),
            "categories_ko": cats_ko,
            "num_categories": len(cats_ko),
        },
        "data_table": data_table,
        "chart_data": chart_data,
        "detection_cards": det_cards,
    }

    # Gemini 해석: 상단 요약 + 카테고리별 해석
    client = app_state.get("gemini")
    if client and not school_det.empty:
        try:
            result["llm_explanation"] = _explain_with_gemini(client, school_det, school_df)
        except Exception as e:
            result["llm_explanation"] = f"(LLM 해석 오류: {e})"
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

            prompt = f"""{school_name or '전체'} 학교의 {', '.join(col_labels)} 분석 결과:
데이터: {data_summary}
변동률 이상: {anomaly_text}

정확히 3줄만 응답:
해석: (핵심 발견 1~2문장)
정상사유: (가능한 이유 쉼표 구분)
확인권장: (담당자 행동 1문장)"""
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
            ai_result["해석"] = f"AI 해석 오류: {e}"

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
    df = app_state.get("df", pd.DataFrame())
    if df.empty:
        raise HTTPException(500, "데이터 미로드")

    # 학교 필터 — 선택된 학교만 분석
    if req.school_code:
        df = df[df["school_code"] == req.school_code].copy()
        if df.empty:
            raise HTTPException(404, "해당 학교 데이터 없음")

    columns_desc = app_state.get("columns", {})
    client = app_state.get("gemini")

    if not client:
        return _fallback_analysis(req.query, df, columns_desc)

    try:
        # 1단계: 분석 계획
        plan = _get_analysis_plan(client, req.query, columns_desc, req.history)

        # 2단계: 안전 실행
        code = plan.get("pandas_code", "result = df.head()")
        try:
            result_df = safe_execute(code, df)
        except Exception as e:
            # 코드 실행 실패 → 기본 데이터로 폴백
            fallback_cols = ["school_name", "year", "student_count", "class_count", "teacher_count",
                             "bullying_cases", "bullying_victims", "graduation_rate"]
            available = [c for c in fallback_cols if c in df.columns]
            result_df = df[available].sort_values("year") if available else df.head(5)
            plan["confidence"] = "중간"

        # 3단계: 결과 해석
        result_data_raw = result_df.head(20).to_dict(orient="records") if isinstance(result_df, pd.DataFrame) else []
        result_data = _rename_cols_ko(result_data_raw)
        # 학교명 조회
        school_name_for_report = ""
        if req.school_code:
            full_df = app_state.get("df", pd.DataFrame())
            sn = full_df[full_df["school_code"] == req.school_code]["school_name"]
            if not sn.empty:
                school_name_for_report = sn.iloc[0]
        report = _generate_report(client, plan, result_data, req.query, school_name_for_report)

        # 4단계: 후속 질문 제안
        suggestions = _generate_suggestions(plan, result_data)

        return ChatResponse(
            plan=plan, result_data=result_data,
            report=report,
            confidence=plan.get("confidence", "중간"),
            follow_up_suggestions=suggestions,
        )

    except Exception as e:
        raise HTTPException(500, f"분석 오류: {e}")


@app.get("/api/school/{school_code}/ai/{category_ko}")
async def category_ai(school_code: str, category_ko: str):
    """카테고리별 AI 해석 (비동기 로드)"""
    client = app_state.get("gemini")
    if not client:
        return {"해석": "", "정상사유": "", "확인권장": ""}

    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())
    school_det = detections[detections["school_code"] == school_code]
    school_df = df[df["school_code"] == school_code]
    school_name = school_df["school_name"].iloc[0] if not school_df.empty else ""
    district = school_df["district"].iloc[0] if not school_df.empty else ""

    # 해당 카테고리 카드 구축
    det_cards = _build_detection_cards(school_det, school_df, df, district)
    card = next((c for c in det_cards if c["category_ko"] == category_ko), None)
    if not card:
        return {"해석": "해당 카테고리 없음", "정상사유": "", "확인권장": ""}

    try:
        return _explain_category(client, card, school_name, district)
    except Exception as e:
        return {"해석": str(e), "정상사유": "", "확인권장": ""}


@app.get("/api/stats")
async def stats():
    df = app_state.get("df", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    return {
        "data_rows": len(df),
        "schools": df["school_name"].nunique() if not df.empty else 0,
        "years": sorted(df["year"].unique().tolist()) if not df.empty else [],
        "total_detections": len(detections),
        "star3_count": len(detections[detections["star"] == 3]) if not detections.empty else 0,
        "star2_count": len(detections[detections["star"] == 2]) if not detections.empty else 0,
        "rules_triggered": detections["rule_id"].nunique() if not detections.empty else 0,
    }


# ── 한국어 명칭 매핑 ──

RULE_NAMES_KO = {
    "C1-1": "학생↔학급 역방향 변동", "C1-2": "학생↔학급 완만 역방향",
    "C1-3": "학생↔교원 불균형", "C1-8": "학급당학생수 급변",
    "C3-3A": "미조치 피해 (강력)", "C3-3B": "미조치 피해 (참고)",
    "B1-1": "학생·교원 급변동", "B1-5": "진학률 급변동", "B1-6": "학폭 심의 급증",
    "C2-3": "급식비 변동", "C2-3+": "급식비 강한 변동",
    "D2-2": "유사학교 대비 극단값", "C5-1": "학생수 비정상 변동",
    "E2-2": "수치 3년 정체", "F1'-1": "교원수 교차 불일치",
}

CATEGORY_NAMES_KO = {
    "C1": "학생·자원 연동 점검", "C3": "미조치 피해 점검",
    "B1": "전년 대비 급변동", "C2": "학생·재정 연동 점검",
    "D2": "유사학교 대비 편차", "C5": "학년 진급 인원 점검",
    "E2": "미갱신 의심 점검", "F1": "연계 시점 차이 점검",
}

# 데이터 테이블에 표시할 지표 목록
TABLE_METRICS = [
    ("학생수", "student_count"),
    ("학급수", "class_count"),
    ("교원수", "teacher_count"),
    ("학급당학생수", "students_per_class"),
    ("교원1인당학생수", "students_per_teacher"),
    ("학폭 심의건수", "bullying_cases"),
    ("피해학생수", "bullying_victims"),
    ("보호조치건수", "bullying_protection"),
    ("가해학생수", "bullying_perpetrators"),
    ("진학률(%)", "graduation_rate"),
    ("급식비총액(천원)", "meal_cost_total"),
    ("1인당급식비(원)", "meal_cost_per_student"),
]


def _build_data_table(school_df, detections_df, full_df) -> list:
    """셀 상태 포함 시계열 데이터 테이블"""
    if school_df.empty:
        return []

    years = sorted(school_df["year"].unique())
    district = school_df["district"].iloc[0] if "district" in school_df.columns else ""

    # 탐지된 (연도, 컬럼) 쌍 수집
    detected_cells = set()
    for _, d in detections_df.iterrows():
        yr = int(d.get("year", 0))
        rid = d.get("rule_id", "")
        # 룰 → 관련 컬럼 매핑
        rule_cols = {
            "C1-1": ["student_count", "class_count"],
            "C1-8": ["students_per_class"],
            "C1-3": ["student_count", "teacher_count"],
            "C3-3A": ["bullying_victims", "bullying_protection"],
            "C3-3B": ["bullying_victims", "bullying_protection"],
            "B1-1": ["student_count", "teacher_count"],
            "B1-5": ["graduation_rate"],
            "B1-6": ["bullying_cases"],
            "C2-3": ["meal_cost_total"], "C2-3+": ["meal_cost_total"],
            "D2-2": ["students_per_class", "students_per_teacher"],
            "C5-1": ["student_count"],
            "E2-2": ["student_count", "teacher_count", "class_count"],
        }
        for col in rule_cols.get(rid, []):
            detected_cells.add((yr, col))

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
    """Chart.js용 데이터"""
    if school_df.empty:
        return {}

    years = sorted(school_df["year"].unique())
    labels = [int(y) for y in years]

    def get_vals(col):
        return [round(float(school_df[school_df["year"] == y][col].iloc[0]), 1)
                if not school_df[school_df["year"] == y].empty and pd.notna(school_df[school_df["year"] == y][col].iloc[0])
                else None for y in years]

    def get_peer(col):
        if district:
            return [round(float(full_df[(full_df["district"] == district) & (full_df["year"] == y)][col].mean()), 1)
                    if len(full_df[(full_df["district"] == district) & (full_df["year"] == y)][col].dropna()) > 0
                    else None for y in years]
        return [None] * len(years)

    return {
        "labels": labels,
        "학생수": get_vals("student_count"),
        "학급수": get_vals("class_count"),
        "교원수": get_vals("teacher_count"),
        "학급당학생수": get_vals("students_per_class"),
        "학폭건수": get_vals("bullying_cases"),
        "피해학생수": get_vals("bullying_victims"),
        "보호조치건수": get_vals("bullying_protection"),
        "진학률": get_vals("graduation_rate"),
        "동료군_학생수": get_peer("student_count"),
        "동료군_교원수": get_peer("teacher_count"),
        "동료군_학급당학생수": get_peer("students_per_class"),
    }


def _get_category_code(rule_id: str) -> str:
    """C3-3A → C3, B1-5 → B1, F1'-1 → F1"""
    base = rule_id.split("-")[0]
    return base.rstrip("'")


# 룰 → 관련 컬럼 매핑
RULE_COLUMNS = {
    "C1-1": [("학생수","student_count"), ("학급수","class_count")],
    "C1-8": [("학급당학생수","students_per_class")],
    "C1-3": [("학생수","student_count"), ("교원수","teacher_count")],
    "C3-3A": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "C3-3B": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "B1-1": [("학생수","student_count"), ("교원수","teacher_count")],
    "B1-5": [("진학률(%)","graduation_rate")],
    "B1-6": [("학폭 심의건수","bullying_cases")],
    "C2-3": [("급식비총액","meal_cost_total"), ("학생수","student_count")],
    "C2-3+": [("급식비총액","meal_cost_total"), ("학생수","student_count")],
    "D2-2": [("학급당학생수","students_per_class"), ("교원1인당학생수","students_per_teacher")],
    "C5-1": [("학생수","student_count")],
    "E2-2": [("학급수","class_count"), ("교원수","teacher_count")],
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
            cat_groups[cat_ko] = {"category_ko": cat_ko, "cat_code": cat, "max_star": 0, "rules": [], "years_set": set()}
        star = int(d.get("star", 0))
        cat_groups[cat_ko]["max_star"] = max(cat_groups[cat_ko]["max_star"], star)
        cat_groups[cat_ko]["years_set"].add(int(d.get("year", 0)))
        cat_groups[cat_ko]["rules"].append({
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
            "year": int(d.get("year", 0)),
            "star": star,
            "detail": d.get("detail", ""),
        })

    # 카테고리별로 관련 컬럼 데이터 테이블 생성
    result = []
    for cat_ko, group in sorted(cat_groups.items(), key=lambda x: -x[1]["max_star"]):
        # 관련 컬럼 수집
        related_cols = set()
        for rule in group["rules"]:
            for label, col in RULE_COLUMNS.get(rule["rule_id"], []):
                related_cols.add((label, col))

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

def _call_gemini(client, prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """Gemini API 호출 공통 함수"""
    full_prompt = f"{system}\n\n---\n\n{prompt}"
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=full_prompt,
    )
    return response.text


def _explain_category(client, card: dict, school_name: str, district: str) -> dict:
    """카테고리별 AI 해석 (해석/정상사유/확인권장)"""
    rules_text = "\n".join(f"- {r['year']}년: {r['detail']}" for r in card["rules"])
    table_text = ""
    if card.get("data_table"):
        for row in card["data_table"]:
            vals = " / ".join(f"{k}:{v}" for k, v in row.items() if k != "지표" and v is not None)
            table_text += f"  {row['지표']}: {vals}\n"

    prompt = f"""{school_name}({district}구) "{card['category_ko']}" 탐지 결과:
{rules_text}
{table_text}
정확히 3줄만 응답. 장황하게 쓰지 마. 숫자 위주로 간결하게.
해석: (동료군 대비 핵심 차이 1문장)
정상사유: (가능한 이유 2~3개, 쉼표 구분)
확인권장: (담당자 행동 1문장)"""

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

    prompt = f"""아래 학교의 이상치 탐지 결과를 요약해주세요.

탐지 결과: {det_text}

반드시 아래 형식으로 3줄 응답 (각 줄 30~50자, 구체적 수치 포함):
① [가장 심각한 항목] 어떤 지표가 어떻게 이상한지 수치와 함께 설명
② [패턴] 몇 년 연속인지, 몇 개 영역에서 동시 탐지인지 등
③ [확인 사항] 담당자가 구체적으로 무엇을 확인해야 하는지"""
    return _call_gemini(client, prompt)


def _get_analysis_plan(client, query: str, columns: dict, history: list) -> dict:
    cols_text = "\n".join(f"- {k}: {v}" for k, v in columns.items())
    history_text = "\n".join(f"Q: {h.get('query','')} → {h.get('summary','')}" for h in history[-3:]) if history else "없음"

    prompt = f"""사용자 질문을 분석하여 pandas DataFrame 분석 계획을 JSON으로 작성하세요.

[사용 가능한 컬럼]
{cols_text}

[이전 대화]
{history_text}

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
- 결과를 반드시 result 변수에 할당
- df 변수 사용 (pd, np 사용 가능)
- import 금지, 파일 I/O 금지
- 최대 10줄 이내
- NaN 처리: .dropna() 또는 .fillna() 사용"""

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
        return {
            "analysis_plan": f"JSON 파싱 실패. 원본: {text[:200]}",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "result = df[['school_name','year','student_count','teacher_count','class_count']].head(10)",
            "comparison": "",
            "confidence": "낮음"
        }


def _generate_report(client, plan: dict, result_data: list, query: str, school_name: str = "") -> str:
    prompt = f"""아래는 {school_name or '학교'}의 분석 결과입니다. 담당자를 위한 보고서를 작성해주세요.

[학교명] {school_name}
[사용자 질문] {query}
[분석 계획] {plan.get('analysis_plan', '')}
[적용 기준] {plan.get('criteria', '')}
[분석 결과]
{json.dumps(result_data[:10], ensure_ascii=False, indent=2, default=str)}

보고서 형식:
1. 핵심 발견 — {school_name}의 구체적 수치를 포함하여 1문장
2. 상세 결과 — 연도별 수치 나열 (학교명 명시)
3. 맥락 해석 — 동료군 대비 비교
4. 확인 권장 사항

주의: "[학교명]" 같은 플레이스홀더 쓰지 마. 반드시 "{school_name}"을 직접 써."""
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
    cols = ["school_name", "year", "student_count", "teacher_count", "class_count"]
    available = [c for c in cols if c in df.columns]
    result = df[available].head(10) if available else df.head(5)
    return ChatResponse(
        plan={"analysis_plan": "기본 데이터 조회", "columns_used": available, "pandas_code": ""},
        result_data=_rename_cols_ko(result.to_dict(orient="records")),
        report="기본 데이터를 표시합니다.",
        confidence="중간",
        follow_up_suggestions=["급식비 추이 분석", "학폭 패턴 분석"],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
