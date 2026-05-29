"""
safe_executor.py — LLM이 생성한 pandas 코드를 안전하게 실행
화이트리스트 기반 샌드박스. 읽기 전용. 타임아웃 5초.
"""

import pandas as pd
import numpy as np
import signal
import re


class SecurityError(Exception):
    """금지된 코드 패턴이 감지되었을 때 발생"""
    pass


class TimeoutError(Exception):
    """실행 시간 초과 시 발생"""
    pass


# 금지 패턴
BLOCKED_PATTERNS = [
    r'\bimport\b', r'\bexec\b', r'\beval\b', r'\bopen\b',
    r'\bos\.', r'\bsys\.', r'\bsubprocess\b',
    r'__\w+__', r'\bglobals\b', r'\blocals\b',
    r'\bcompile\b', r'\bgetattr\b', r'\bsetattr\b',
    r'\bdelattr\b', r'\bbreakpoint\b',
    r'\.to_csv\b', r'\.to_excel\b', r'\.to_sql\b',  # 파일 쓰기 금지
    r'\bdrop\b.*\binplace\b',  # inplace 변경 금지
]

# 허용 pandas 메서드
ALLOWED_METHODS = {
    'groupby', 'filter', 'query', 'describe', 'mean', 'median',
    'std', 'quantile', 'value_counts', 'sort_values', 'head', 'tail',
    'merge', 'concat', 'pivot_table', 'agg', 'apply', 'transform',
    'nlargest', 'nsmallest', 'unique', 'nunique', 'count', 'sum',
    'min', 'max', 'idxmin', 'idxmax', 'corr', 'cov',
    'rolling', 'shift', 'diff', 'pct_change', 'rank',
    'isin', 'between', 'isna', 'notna', 'fillna', 'dropna',
    'astype', 'copy', 'reset_index', 'set_index',
    'rename', 'iloc', 'loc', 'assign', 'where', 'mask',
    'abs', 'round', 'clip',
}

TIMEOUT_SECONDS = 5


def _timeout_handler(signum, frame):
    raise TimeoutError(f"실행 시간 {TIMEOUT_SECONDS}초 초과")


def validate_code(code: str) -> None:
    """코드 안전성 검증"""
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, code):
            match = re.search(pattern, code).group()
            raise SecurityError(f"금지된 패턴 감지: '{match}'")

    # 줄 수 제한 (30줄 이내)
    lines = [l for l in code.strip().split('\n') if l.strip() and not l.strip().startswith('#')]
    if len(lines) > 30:
        raise SecurityError(f"코드가 너무 깁니다 ({len(lines)}줄). 최대 30줄까지 허용.")


def safe_execute(code: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    LLM이 생성한 pandas 코드를 안전하게 실행
    - df는 읽기 전용 복사본
    - 화이트리스트 기반 실행
    - 5초 타임아웃
    """
    # 1. 코드 검증
    validate_code(code)

    # 2. DataFrame 복사 (원본 보호)
    safe_df = df.copy()

    # 3. 제한된 실행 환경
    local_vars = {
        "df": safe_df,
        "pd": pd,
        "np": np,
        "result": None,
    }

    restricted_builtins = {
        "len": len, "range": range, "enumerate": enumerate,
        "zip": zip, "map": map, "filter": filter,
        "sorted": sorted, "reversed": reversed,
        "min": min, "max": max, "sum": sum,
        "abs": abs, "round": round,
        "int": int, "float": float, "str": str, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "True": True, "False": False, "None": None,
        "print": lambda *a, **kw: None,  # print 무시
    }

    # 4. 타임아웃 설정 (Unix only)
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT_SECONDS)
    except (AttributeError, ValueError):
        old_handler = None  # Windows에서는 SIGALRM 미지원

    # 5. 실행
    try:
        exec(code, {"__builtins__": restricted_builtins}, local_vars)
    except TimeoutError:
        raise
    except SecurityError:
        raise
    except Exception as e:
        raise RuntimeError(f"코드 실행 오류: {type(e).__name__}: {e}")
    finally:
        # 타임아웃 해제
        try:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except (AttributeError, ValueError):
            pass

    # 6. 결과 반환 — 정책: result 변수 명시 할당만 인정. 폴백 모두 폐기.
    result = local_vars.get("result")
    if result is None:
        # 정책: "마지막 DataFrame 검색" + "전체 df 반환" fallback 모두 폐기.
        # result 미정의 = "코드는 실행됐지만 결과를 받지 못한" 실패 상황.
        # 호출부 try/except에서 fallback_help 안내 응답으로 라우팅됨.
        # (정상 빈 결과(0건 DataFrame)와 구분 — 그건 빈 DataFrame을 그대로 반환.)
        raise RuntimeError("result 변수가 정의되지 않았습니다. pandas 코드가 'result = ...'에 할당하지 않음.")

    if isinstance(result, pd.Series):
        result = result.to_frame()

    return result


# 테스트
if __name__ == "__main__":
    # 테스트 DataFrame
    test_df = pd.DataFrame({
        "school": ["A고", "B고", "C고"],
        "students": [500, 300, 800],
        "teachers": [30, 20, 50],
    })

    # 안전한 코드
    safe_code = 'result = df[df["students"] > 400]'
    print("안전한 코드 테스트:")
    print(safe_execute(safe_code, test_df))

    # 위험한 코드
    try:
        dangerous_code = 'import os; os.system("ls")'
        safe_execute(dangerous_code, test_df)
    except SecurityError as e:
        print(f"\n보안 차단: {e}")
