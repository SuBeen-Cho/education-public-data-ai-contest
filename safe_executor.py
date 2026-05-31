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


# 금지 패턴 (텍스트 레벨 1차 방어 — SafeNamespace가 2차 방어)
BLOCKED_PATTERNS = [
    r'\bimport\b', r'\bexec\b', r'\beval\b', r'\bopen\b',
    r'\bos\.', r'\bsys\.', r'\bsubprocess\b',
    r'__\w+__', r'\bglobals\b', r'\blocals\b',
    r'\bcompile\b', r'\bgetattr\b', r'\bsetattr\b',
    r'\bdelattr\b', r'\bbreakpoint\b',
    # 파일 I/O — DataFrame.to_<I/O> 메서드 호출은 텍스트로 잡는다 (SafeNamespace 못 막음)
    r'\.to_csv\b', r'\.to_excel\b', r'\.to_sql\b',
    r'\.to_pickle\b', r'\.to_parquet\b', r'\.to_hdf\b',
    r'\.to_json\b', r'\.to_clipboard\b', r'\.to_html\b',
    r'\.to_latex\b', r'\.to_markdown\b', r'\.to_stata\b',
    r'\.to_xml\b', r'\.to_feather\b', r'\.to_orc\b',
    r'\.to_records\b',  # ndarray 변환 — to_<X> 패턴 일관성
    # pandas/numpy I/O 진입점 (SafeNamespace 화이트리스트 누락 대비 보강)
    r'\bpd\.read_\w+', r'\bpd\.HDFStore\b', r'\bpd\.ExcelFile\b',
    r'\bpd\.ExcelWriter\b', r'\bpd\.read\b',
    r'\bnp\.load\b', r'\bnp\.save\b', r'\bnp\.loadtxt\b',
    r'\bnp\.savetxt\b', r'\bnp\.fromfile\b', r'\bnp\.tofile\b',
    r'\bnp\.genfromtxt\b', r'\bnp\.memmap\b',
    # inplace 변경 금지
    r'\bdrop\b.*\binplace\b',
    # SafeNamespace 내부 참조 시도 차단 (이중 방어 — 클로저 숨김이 1차)
    r'\._mod\b', r'\._wl\b', r'\._name\b',
]

# pd 화이트리스트 — 분석에서 실제 쓰이는 안전한 진입점만.
# read_*/HDFStore/ExcelFile 등 I/O는 의도적으로 제외.
PD_WHITELIST = frozenset([
    # 자료구조 생성자
    "DataFrame", "Series", "Index", "MultiIndex", "RangeIndex",
    "Categorical", "CategoricalIndex", "DatetimeIndex", "TimedeltaIndex",
    "PeriodIndex", "IntervalIndex", "Interval", "Period",
    "Timestamp", "Timedelta",
    # 결합·재구성
    "concat", "merge", "merge_asof", "merge_ordered", "join",
    "melt", "pivot", "pivot_table", "crosstab", "wide_to_long",
    "get_dummies", "from_dummies",
    # 변환
    "to_numeric", "to_datetime", "to_timedelta",
    # null 체크
    "isna", "isnull", "notna", "notnull",
    # 시계열·구간
    "date_range", "timedelta_range", "period_range", "interval_range",
    "cut", "qcut",
    # 유틸
    "factorize", "unique", "value_counts", "array",
    # 상수
    "NA", "NaT",
])

# np 화이트리스트
NP_WHITELIST = frozenset([
    # 통계
    "mean", "median", "std", "var", "sum", "prod",
    "min", "max", "percentile", "quantile", "average",
    "ptp", "corrcoef", "cov", "histogram", "bincount",
    # 조건·비교
    "where", "select", "choose", "clip",
    "maximum", "minimum", "fmax", "fmin",
    "isnan", "isinf", "isfinite", "isclose", "allclose",
    "isin", "all", "any", "logical_and", "logical_or", "logical_not", "logical_xor",
    # 수학
    "abs", "absolute", "sign", "log", "log1p", "log2", "log10",
    "exp", "expm1", "sqrt", "square", "power", "reciprocal",
    "floor", "ceil", "trunc", "round", "rint", "fix",
    "add", "subtract", "multiply", "divide", "mod", "remainder",
    # 배열 생성
    "array", "asarray", "ones", "zeros", "empty", "full",
    "ones_like", "zeros_like", "empty_like", "full_like",
    "arange", "linspace", "logspace", "eye", "identity",
    # 정렬·검색
    "sort", "argsort", "argmax", "argmin", "argwhere",
    "searchsorted", "unique", "nonzero",
    # 누적·차분
    "cumsum", "cumprod", "diff", "gradient",
    # 변환·재구성
    "concatenate", "stack", "vstack", "hstack", "column_stack",
    "split", "reshape", "transpose",
    "nan_to_num",
    # dtype·상수
    "float64", "float32", "int64", "int32", "int16", "int8",
    "uint64", "uint32", "uint16", "uint8", "bool_", "object_",
    "nan", "NaN", "inf", "Inf", "pi", "e",
    "newaxis",
])


def _make_safe_namespace(mod, whitelist, name: str = ""):
    """모듈을 화이트리스트로 감싸 허용 속성만 노출.
    pd.read_csv 같은 I/O 진입점을 차단. lambda·comprehension에서도 보임.

    내부 참조(mod·whitelist·name)는 클로저에만 둔다.
    예전 구현은 __slots__("_mod","_wl","_name")이라 pd._mod 로 원본 모듈에 직접 닿아
    화이트리스트를 우회할 수 있었다. 이제 인스턴스 슬롯이 비어 있어 어떤 속성도
    __getattr__로 폴백 → 화이트리스트 검사 통과 못 함.
    """
    _wl_local = whitelist
    _name_local = name or getattr(mod, "__name__", "module")
    _mod_local = mod

    class _SafeNS:
        __slots__ = ()

        def __getattr__(self, item):
            # 일반 인스턴스 속성이 없어 모든 접근이 여기로 옴.
            # 화이트리스트에 있는 이름만 통과. 그 외(특히 '_'·dunder)는 일괄 차단.
            if item in _wl_local:
                return getattr(_mod_local, item)
            raise SecurityError(
                f"'{_name_local}.{item}' is not in the sandbox whitelist (blocked)"
            )

        def __setattr__(self, key, value):
            raise SecurityError(
                f"sandbox '{_name_local}' is read-only (set '{key}' blocked)"
            )

        def __delattr__(self, key):
            raise SecurityError(
                f"sandbox '{_name_local}' is read-only (del '{key}' blocked)"
            )

        def __dir__(self):
            return sorted(_wl_local)

        def __repr__(self):
            return f"<SafeNamespace {_name_local} whitelist={len(_wl_local)} items>"

    _SafeNS.__name__ = f"SafeNamespace_{_name_local}"
    return _SafeNS()


# 하위 호환 — 외부에서 SafeNamespace(...) 형태로 부르는 호출을 지원.
def SafeNamespace(mod, whitelist, name: str = ""):
    return _make_safe_namespace(mod, whitelist, name)


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

    # 3. 제한된 실행 환경 — globals와 locals를 같은 dict로 통합.
    # 분리하면 lambda·list comprehension 같은 nested scope에서 globals만 보이고
    # np/pd가 locals에만 있어 NameError 발생. exec 단일 namespace로 회피.
    # pd/np는 SafeNamespace로 감싸 read_csv·read_excel·HDFStore 등 I/O 진입점 차단.
    safe_pd = SafeNamespace(pd, PD_WHITELIST, name="pd")
    safe_np = SafeNamespace(np, NP_WHITELIST, name="np")
    env = {
        "__builtins__": restricted_builtins,
        "df": safe_df,
        "pd": safe_pd,
        "np": safe_np,
        "result": None,
    }

    # 4. 타임아웃 설정 (Unix only)
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT_SECONDS)
    except (AttributeError, ValueError):
        old_handler = None  # Windows에서는 SIGALRM 미지원

    # 5. 실행
    try:
        exec(code, env)
    except TimeoutError:
        raise
    except SecurityError:
        # SafeNamespace 차단도 그대로 SecurityError로 전파.
        raise
    except Exception as e:
        # SafeNamespace의 SecurityError가 다른 예외에 wrap되어 들어올 수 있음 — 원인 검사.
        if isinstance(e, SecurityError) or "SecurityError" in type(e).__name__:
            raise
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
    result = env.get("result")
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
