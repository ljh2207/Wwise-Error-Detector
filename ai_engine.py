"""
AI 분석 엔진

동작 방식:
  "AI 분석" 버튼 클릭 시 selected_error.json 저장 후
  claude -p (print 모드) 를 subprocess 로 실행한다.
  Claude Code 세션 인증을 그대로 사용 — API 키 불필요.
  분석 결과는 호출한 쪽(대시보드)으로 문자열 반환.

캐시 동작:
  error_code + 정규화된 description을 키로 analysis_cache.json에 저장.
  동일 유형 에러는 AI 호출 없이 즉시 반환 — 토큰 절약.
"""
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------
# 분석 결과 캐시
# ------------------------------------------------------------------
_CACHE_PATH = os.path.join(_HERE, "analysis_cache.json")
_cache_lock = threading.Lock()

# description에서 가변 부분(파일명·GUID·숫자)을 제거해 패턴 키로 만든다
_NORM_PATTERNS = [
    # Wwise 미디어 경로: "Footsteps\Footsteps_Dirt.wav" → "{media}"
    (re.compile(r'[\w\\/ ]+\.(wav|wem|ogg|mp3|aiff?|flac)', re.IGNORECASE), '{media}'),
    # GUID: {XXXXXXXX-...}
    (re.compile(r'\{[0-9A-Fa-f\-]{36}\}'), '{guid}'),
    # 순수 숫자 (ID, timestamp 등)
    (re.compile(r'\b\d+\b'), '{n}'),
]


def _normalize_description(description: str) -> str:
    """description에서 파일명·GUID·숫자 등 가변 부분을 제거해 패턴 문자열로 반환."""
    s = description.strip()
    for pattern, replacement in _NORM_PATTERNS:
        s = pattern.sub(replacement, s)
    # 연속 공백 정리
    return re.sub(r'\s+', ' ', s)


def _cache_key(error) -> str:
    return f"{error.error_code}|{_normalize_description(error.description)}"


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _cache_get(error) -> str | None:
    """캐시에서 동일 에러 유형의 분석 결과를 반환. 없으면 None."""
    key = _cache_key(error)
    with _cache_lock:
        cache = _load_cache()
        entry = cache.get(key)
        if not entry:
            return None
        # hit_count 갱신
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        entry["last_seen"] = datetime.now().isoformat()
        cache[key] = entry
        _save_cache(cache)
    logger.info("캐시 히트: %s (총 %d회)", key[:60], entry["hit_count"])
    return entry["analysis"]


def _cache_set(error, analysis: str, source: str) -> None:
    """분석 결과를 캐시에 저장. source는 'claude' 또는 'gemini'."""
    key = _cache_key(error)
    now = datetime.now().isoformat()
    with _cache_lock:
        cache = _load_cache()
        cache[key] = {
            "analysis": analysis,
            "error_code": error.error_code,
            "cause": error.cause,
            "example_description": error.description,
            "source": source,
            "hit_count": 0,
            "first_seen": now,
            "last_seen": now,
        }
        _save_cache(cache)
    logger.info("캐시 저장: %s", key[:60])


def _is_waapi_available(host: str = "127.0.0.1", port: int = 8080,
                        timeout: float = 2.0) -> bool:
    """WAAPI 포트(8080)가 열려 있는지 TCP 수준에서 확인한다.

    WebSocket 핸드셰이크 없이 포트 연결만 시도하므로 2초 이내에 결과를 반환한다.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
SELECTED_ERROR_PATH = os.path.join(_HERE, "selected_error.json")

_PROMPT_TEMPLATE = """아래 Wwise 런타임 에러를 분석하세요.
파일 탐색·인터넷 검색 없이 학습 지식만으로 즉시 답변하세요.

--- 에러 정보 ---
오브젝트 경로 : {object_path}
오브젝트명    : {object_name}
오브젝트 ID   : {object_id}
에러 유형     : {error_type}
에러 코드     : {error_code}
원본 메시지   : {description}
자동 분류 원인: {cause_auto}

--- 출력 형식 (각 항목 3문장 이내, 한국어) ---
1. 근본 원인 — 왜 이 에러가 발생했는지
2. 단계별 해결 방법 — Wwise 에디터 기준
3. WAAPI 자동 수정 가능 여부 — 가능하면 사용할 WAAPI 함수명 포함
"""


def _build_prompt(error) -> str:
    return _PROMPT_TEMPLATE.format(
        object_path=error.object_path or "(경로 없음)",
        object_name=error.object_name,
        object_id=error.object_id or "(없음)",
        error_type=error.error_type,
        error_code=error.error_code,
        description=error.description,
        cause_auto=error.cause,
    )


def _run_cli(cmd: list[str], cli_name: str, timeout: int = 120,
             on_progress=None, cwd: str | None = None) -> str:
    """CLI subprocess 실행 공통 처리.

    Windows에서 npm 설치 CLI는 .cmd 래퍼이므로 shell=True로 실행해야
    cmd.exe가 PATH에서 정상적으로 탐색한다.

    cwd: 작업 디렉터리 (None이면 _HERE 사용)
    on_progress: 진행 상황 문자열을 전달받는 콜백 (선택)
    """
    work_dir = cwd or _HERE
    try:
        kwargs = dict(
            stdin=subprocess.DEVNULL,  # GUI 앱에서 stdin 미지정 시 EOF 대기 → hang
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        if sys.platform == "win32":
            proc = subprocess.Popen(
                subprocess.list2cmdline(cmd), shell=True, cwd=work_dir, **kwargs
            )
        else:
            proc = subprocess.Popen(cmd, cwd=work_dir, **kwargs)

        stdout_buf = []
        stderr_buf = []
        quota_error: list[str] = []  # 쿼터 소진 메시지 저장용

        def _read_stderr():
            for raw in proc.stderr:
                line = raw.strip()
                if not line:
                    continue
                stderr_buf.append(line)
                # 쿼터 소진 감지 → 즉시 프로세스 종료
                if re.search(r"exhausted your capacity|quota.*exceeded|rate.*limit", line, re.IGNORECASE):
                    quota_error.append(line)
                    proc.kill()
                    return
                if on_progress:
                    m = re.search(r"Attempt (\d+) failed", line)
                    if m:
                        attempt = int(m.group(1))
                        on_progress(
                            f"{cli_name} 분석 중... "
                            f"(쿼터 초과 — {attempt}번째 재시도 중)"
                        )

        def _read_stdout():
            stdout_buf.append(proc.stdout.read())

        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err.start()
        t_out.start()
        t_out.join(timeout=timeout)

        if proc.poll() is None:
            proc.kill()
            t_err.join(timeout=2)
            return f"분석 시간 초과 ({timeout}초). 다시 시도해주세요."

        t_err.join(timeout=2)
        if quota_error:
            return (
                f"{cli_name} 모델 쿼터가 소진되었습니다.\n"
                f"잠시 후 다시 시도하거나 다른 모델을 사용하세요.\n"
                f"({quota_error[0]})"
            )
        stdout = stdout_buf[0] if stdout_buf else ""
        if proc.returncode == 0 and stdout.strip():
            return stdout.strip()
        err = "\n".join(stderr_buf)
        logger.error("%s 오류: %s", cli_name, err)
        return f"분석 실패: {err or '알 수 없는 오류'}"

    except FileNotFoundError:
        return f"{cli_name} CLI 를 찾을 수 없습니다. 설치 여부를 확인하세요."
    except Exception as e:
        logger.error("%s 분석 예외: %s", cli_name, e)
        return f"분석 중 오류 발생: {e}"


_NO_MCP_DIR = os.path.join(_HERE, "_no_mcp")
_NO_MCP_JSON = os.path.join(_NO_MCP_DIR, ".mcp.json")


def _ensure_no_mcp_dir():
    """MCP 서버 없는 격리 실행용 디렉터리를 준비한다.

    claude -p 를 _HERE 에서 실행하면 .mcp.json 의 sk-wwise 서버들을
    초기화하면서 WAAPI 연결 타임아웃으로 120초를 모두 소모한다.
    빈 .mcp.json 이 있는 별도 디렉터리에서 실행해 이를 방지한다.
    """
    os.makedirs(_NO_MCP_DIR, exist_ok=True)
    if not os.path.exists(_NO_MCP_JSON):
        with open(_NO_MCP_JSON, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {}}, f)


def _run_claude(prompt: str, cwd: str, timeout: int = 60) -> str:
    """프롬프트를 파일로 저장 후 PowerShell을 통해 Claude에 전달한다.

    Windows cmd.exe는 다중 행 한국어 인수를 지원하지 않아
    `claude -p PROMPT` 방식으로 전달하면 줄바꿈이 깨진다.
    PowerShell은 UTF-8 파일을 읽어 문자열로 변환 후 인수로 전달할 수 있다.
    """
    prompt_file = os.path.join(cwd, "_prompt.txt")
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)

        # PowerShell로 파일 내용을 읽어 claude -p 인수로 전달
        ps_cmd = (
            f"$p = Get-Content '{prompt_file}' -Raw -Encoding UTF8; "
            f"claude -p $p --max-turns 1"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.error("Claude 오류: %s", result.stderr)
        return f"분석 실패: {result.stderr or '알 수 없는 오류'}"

    except subprocess.TimeoutExpired:
        return f"분석 시간 초과 ({timeout}초). 다시 시도해주세요."
    except FileNotFoundError:
        return "Claude CLI 또는 PowerShell을 찾을 수 없습니다."
    except Exception as e:
        logger.error("Claude 분석 예외: %s", e)
        return f"분석 중 오류 발생: {e}"


def analyze(error) -> str:
    """Claude -p 로 에러를 분석해 결과 문자열을 반환한다.

    동일 에러 유형이 캐시에 있으면 AI 호출 없이 즉시 반환한다.
    Wwise 실행 중(WAAPI 포트 8080 응답):
      _HERE 에서 sk-wwise MCP 포함 실행 → WAAPI 도구 활용 가능.
    Wwise 꺼짐:
      _no_mcp/ 에서 MCP 없이 실행 → 텍스트 분석만 수행.
      (MCP 서버 초기화 타임아웃 방지)
    """
    _save(error)

    cached = _cache_get(error)
    if cached:
        return cached + "\n\n─── 캐시된 분석 결과입니다 (동일 에러 유형) ───"

    prompt = _build_prompt(error)
    if _is_waapi_available():
        result = _run_claude(prompt, cwd=_HERE)
    else:
        _ensure_no_mcp_dir()
        result = _run_claude(prompt, cwd=_NO_MCP_DIR)

    # 분석 성공 시에만 캐시 저장 (실패·타임아웃 메시지는 제외)
    if result and not result.startswith("분석 실패") and not result.startswith("분석 시간 초과"):
        _cache_set(error, result, source="claude")

    return result


# ------------------------------------------------------------------
# Gemini API 직접 호출 (CLI 우회 — OAuth 토큰 재사용)
# ------------------------------------------------------------------

_GEMINI_OAUTH_CREDS = Path.home() / ".gemini" / "oauth_creds.json"
_GEMINI_PROJECTS_JSON = Path.home() / ".gemini" / "projects.json"
try:
    from _gemini_secrets import GEMINI_CLIENT_ID as _GEMINI_CLIENT_ID
    from _gemini_secrets import GEMINI_CLIENT_SECRET as _GEMINI_CLIENT_SECRET
except ImportError:
    _GEMINI_CLIENT_ID = ""
    _GEMINI_CLIENT_SECRET = ""
_GEMINI_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Gemini CLI 내부 엔드포인트 (cloudcode-pa)
_GEMINI_API_URL = "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
# 쿼터 소진 시 순서대로 시도할 모델 목록
_GEMINI_FALLBACK_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
]


def _gemini_access_token() -> str:
    """~/.gemini/oauth_creds.json 에서 유효한 access_token을 반환한다.

    만료된 경우 refresh_token으로 갱신 후 파일에 저장한다.
    """
    import requests  # 함수 내 임포트 — 미설치 환경에서도 Claude 분석은 정상 동작

    creds = json.loads(_GEMINI_OAUTH_CREDS.read_text(encoding="utf-8"))
    expiry_ms = creds.get("expiry_date", 0)
    if time.time() * 1000 < expiry_ms - 60_000:  # 1분 여유
        return creds["access_token"]

    # 토큰 갱신
    resp = requests.post(_GEMINI_TOKEN_URI, data={
        "client_id": _GEMINI_CLIENT_ID,
        "client_secret": _GEMINI_CLIENT_SECRET,
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=10)
    resp.raise_for_status()
    new = resp.json()
    creds["access_token"] = new["access_token"]
    creds["expiry_date"] = int(time.time() * 1000) + new["expires_in"] * 1000
    _GEMINI_OAUTH_CREDS.write_text(
        json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Gemini OAuth 토큰 갱신 완료")
    return creds["access_token"]


def _get_gemini_project() -> str:
    """projects.json 에서 Wwise-Error-Detector 디렉토리에 해당하는 project ID를 반환한다."""
    try:
        projects = json.loads(_GEMINI_PROJECTS_JSON.read_text(encoding="utf-8"))
        key = str(Path(_HERE)).lower()
        for path, pid in projects.get("projects", {}).items():
            if path.lower() == key:
                return pid
    except Exception:
        pass
    return "wwise-error-detector"  # 폴백


def _gemini_api_call(model: str, prompt: str, timeout: int = 30) -> str:
    """Gemini CLI 내부 API(cloudcode-pa)를 직접 호출해 응답 텍스트를 반환한다.

    CLI 에이전트 루프를 완전히 우회하므로 수 초 내 응답한다.
    """
    import requests
    import uuid

    token = _gemini_access_token()
    project = _get_gemini_project()

    gemini_md = Path(_HERE) / "GEMINI.md"
    system_text = gemini_md.read_text(encoding="utf-8") if gemini_md.exists() else ""

    payload = {
        "model": model,
        "project": project,
        "user_prompt_id": str(uuid.uuid4()),
        "request": {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system_text}]} if system_text else None,
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
        },
    }

    resp = requests.post(
        _GEMINI_API_URL,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise _QuotaExceededError(resp.text)
    resp.raise_for_status()

    data = resp.json()
    # 응답 구조: { "response": { "candidates": [...] } }
    candidates = data.get("response", {}).get("candidates", [])
    if not candidates:
        raise ValueError(f"응답에 candidates 없음: {data}")
    return candidates[0]["content"]["parts"][0]["text"].strip()


class _QuotaExceededError(Exception):
    pass


def analyze_gemini(error, on_progress=None) -> str:
    """Gemini CLI(-p, --yolo, --max-turns 1)로 에러를 분석한다.

    동일 에러 유형이 캐시에 있으면 AI 호출 없이 즉시 반환한다.
    쿼터 소진 시 _GEMINI_FALLBACK_MODELS 순서대로 다음 모델로 자동 전환한다.
    """
    _save(error)

    cached = _cache_get(error)
    if cached:
        return cached + "\n\n─── 캐시된 분석 결과입니다 (동일 에러 유형) ───"

    prompt = _build_prompt(error)

    last_err = ""
    for model in _GEMINI_FALLBACK_MODELS:
        logger.info("Gemini CLI 분석 시작: %s", model)
        if on_progress:
            on_progress(f"Gemini 분석 중... ({model})")
        result = _run_cli(
            ["gemini", "-p", prompt, "--model", model, "--yolo", "--max-turns", "1"],
            f"Gemini({model})",
            on_progress=on_progress,
        )
        # 쿼터 소진은 _run_cli 내부에서 감지 후 메시지 반환
        if "쿼터가 소진" in result:
            last_err = result
            if on_progress:
                on_progress(f"쿼터 소진 — {model} → 다음 모델로 전환 중...")
            continue

        # 분석 성공 시에만 캐시 저장
        if result and not result.startswith("분석 실패") and not result.startswith("분석 시간 초과"):
            _cache_set(error, result, source="gemini")
        return result

    return f"Gemini 분석 실패: {last_err}"


def _save(error):
    """에러 데이터를 selected_error.json 에 저장."""
    data = {
        "saved_at": datetime.now().isoformat(),
        "id": error.id,
        "timestamp_ms": error.timestamp_ms,
        "object_name": error.object_name,
        "object_path": error.object_path,
        "object_id": error.object_id,
        "game_object_name": error.game_object_name,
        "error_type": error.error_type,
        "error_code": error.error_code,
        "description": error.description,
        "severity": error.severity,
        "cause_auto": error.cause,
        "solution_auto": error.solution,
        "fix_available": error.fix_available,
    }
    with open(SELECTED_ERROR_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
