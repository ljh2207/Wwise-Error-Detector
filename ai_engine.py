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
import time
import subprocess
import sys
import threading
from datetime import datetime

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


def _cache_key(error, source: str | None = None) -> str:
    base = f"{error.error_code}|{_normalize_description(error.description)}"
    return f"{source}|{base}" if source else base


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _replace_object_refs(text: str, old_name: str, old_path: str,
                         new_name: str, new_path: str) -> str:
    """캐시된 분석 텍스트에서 이전 오브젝트 이름·경로를 현재 것으로 교체.

    AI는 오브젝트 이름을 공백 그대로 쓰기도 하고,
    파일명 형식(공백→언더스코어)으로 쓰기도 하므로 두 가지 모두 교체한다.
    예) "Footsteps Grass Walk 3"  → "Footsteps Grass Walk 4"
        "Footsteps_Grass_Walk_3" → "Footsteps_Grass_Walk_4"
    """
    if old_name and new_name and old_name != new_name:
        text = text.replace(old_name, new_name)
        old_file = old_name.replace(" ", "_")
        new_file = new_name.replace(" ", "_")
        if old_file != new_file:
            text = text.replace(old_file, new_file)
    if old_path and new_path and old_path != new_path:
        text = text.replace(old_path, new_path)
    return text


def _cache_get(error, source: str | None = None) -> str | None:
    """캐시에서 동일 에러 유형의 분석 결과를 반환. 없으면 None.

    source가 지정된 경우 해당 AI('claude' 또는 'gemini')가 저장한 결과만 반환한다.
    캐시된 텍스트 안의 원본 오브젝트 이름·경로를 현재 에러의 것으로 교체해 반환한다.
    """
    key = _cache_key(error, source)
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
    analysis = entry["analysis"]
    analysis = _replace_object_refs(
        analysis,
        old_name=entry.get("cached_object_name", ""),
        old_path=entry.get("cached_object_path", ""),
        new_name=error.object_name,
        new_path=error.object_path or "",
    )
    return analysis


def _cache_set(error, analysis: str, source: str) -> None:
    """분석 결과를 캐시에 저장. source는 'claude' 또는 'gemini'."""
    key = _cache_key(error, source)
    now = datetime.now().isoformat()
    with _cache_lock:
        cache = _load_cache()
        cache[key] = {
            "analysis": analysis,
            "error_code": error.error_code,
            "cause": error.cause,
            "example_description": error.description,
            "cached_object_name": error.object_name,
            "cached_object_path": error.object_path or "",
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
    from error_classifier import get_kb_entry
    base = _PROMPT_TEMPLATE.format(
        object_path=error.object_path or "(경로 없음)",
        object_name=error.object_name,
        object_id=error.object_id or "(없음)",
        error_type=error.error_type,
        error_code=error.error_code,
        description=error.description,
        cause_auto=error.cause,
    )
    kb = get_kb_entry(error.error_code)
    if kb:
        causes_text = "; ".join(kb.get("causes", [])) or "(없음)"
        solutions_text = "; ".join(kb.get("solutions", [])) or "(없음)"
        kb_section = (
            "\n--- 공식 Wwise 문서 (참고용) ---\n"
            f"제목: {kb['title']}\n"
            f"설명: {kb.get('description', '')}\n"
            f"유력한 원인: {causes_text}\n"
            f"권장 해결 단계: {solutions_text}\n\n"
            "위 공식 문서를 참고하되, 이 특정 오브젝트와 에러 상황에 맞게 분석하세요.\n"
        )
        base += kb_section
    return base


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


def _run_claude(prompt: str, cwd: str, timeout: int = 60,
                cancel_event: threading.Event | None = None) -> str:
    """프롬프트를 파일로 저장 후 PowerShell을 통해 Claude에 전달한다.

    Windows cmd.exe는 다중 행 한국어 인수를 지원하지 않아
    `claude -p PROMPT` 방식으로 전달하면 줄바꿈이 깨진다.
    PowerShell은 UTF-8 파일을 읽어 문자열로 변환 후 인수로 전달할 수 있다.
    """
    prompt_file = os.path.join(cwd, "_prompt.txt")
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)

        ps_cmd = (
            f"$p = Get-Content '{prompt_file}' -Raw -Encoding UTF8; "
            f"claude -p $p --max-turns 1"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        elapsed = 0.0
        poll_interval = 0.2
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                return ""
            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed >= timeout:
                proc.kill()
                return f"분석 시간 초과 ({timeout}초). 다시 시도해주세요."

        stdout, stderr = proc.communicate()
        if proc.returncode == 0 and stdout.strip():
            return stdout.strip()
        logger.error("Claude 오류: %s", stderr)
        return f"분석 실패: {stderr or '알 수 없는 오류'}"

    except FileNotFoundError:
        return "Claude CLI 또는 PowerShell을 찾을 수 없습니다."
    except Exception as e:
        logger.error("Claude 분석 예외: %s", e)
        return f"분석 중 오류 발생: {e}"


def analyze(error, cancel_event: threading.Event | None = None) -> str:
    """Claude -p 로 에러를 분석해 결과 문자열을 반환한다.

    동일 에러 유형이 캐시에 있으면 AI 호출 없이 즉시 반환한다.
    Wwise 실행 중(WAAPI 포트 8080 응답):
      _HERE 에서 sk-wwise MCP 포함 실행 → WAAPI 도구 활용 가능.
    Wwise 꺼짐:
      _no_mcp/ 에서 MCP 없이 실행 → 텍스트 분석만 수행.
      (MCP 서버 초기화 타임아웃 방지)
    """
    _save(error)

    cached = _cache_get(error, source="claude")
    if cached:
        return cached + "\n\n─── 캐시된 분석 결과입니다 (동일 에러 유형) ───"

    prompt = _build_prompt(error)
    if _is_waapi_available():
        result = _run_claude(prompt, cwd=_HERE, cancel_event=cancel_event)
    else:
        _ensure_no_mcp_dir()
        result = _run_claude(prompt, cwd=_NO_MCP_DIR, cancel_event=cancel_event)

    # 분석 성공 시에만 캐시 저장 (실패·타임아웃·취소 메시지는 제외)
    if result and not result.startswith("분석 실패") and not result.startswith("분석 시간 초과"):
        _cache_set(error, result, source="claude")

    return result


def _run_gemini(prompt: str, cwd: str, timeout: int = 60,
                cancel_event: threading.Event | None = None) -> str:
    """프롬프트를 파일로 저장 후 PowerShell을 통해 Gemini에 전달한다.

    _run_claude()와 동일한 방식 — Windows cmd.exe의 다중 행 한국어 인수
    깨짐 문제를 피하기 위해 파일 경유 후 PowerShell로 실행한다.
    """
    prompt_file = os.path.join(cwd, "_prompt_gemini.txt")
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)

        ps_cmd = (
            f"$p = Get-Content '{prompt_file}' -Raw -Encoding UTF8; "
            f"gemini -p $p --approval-mode plan"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        elapsed = 0.0
        poll_interval = 0.2
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                return ""
            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed >= timeout:
                proc.kill()
                return f"분석 시간 초과 ({timeout}초). 다시 시도해주세요."

        stdout, stderr = proc.communicate()
        if proc.returncode == 0 and stdout.strip():
            return stdout.strip()
        logger.error("Gemini 오류: %s", stderr)
        return f"분석 실패: {stderr or '알 수 없는 오류'}"

    except FileNotFoundError:
        return "Gemini CLI 또는 PowerShell을 찾을 수 없습니다."
    except Exception as e:
        logger.error("Gemini 분석 예외: %s", e)
        return f"분석 중 오류 발생: {e}"


def analyze_gemini(error, on_progress=None, cancel_event: threading.Event | None = None) -> str:
    """Gemini -p 로 에러를 분석해 결과 문자열을 반환한다.

    동일 에러 유형이 캐시에 있으면 AI 호출 없이 즉시 반환한다.
    Wwise 실행 중(WAAPI 포트 8080 응답):
      _HERE 에서 sk-wwise MCP 포함 실행 → WAAPI 도구 활용 가능.
    Wwise 꺼짐:
      _no_mcp/ 에서 MCP 없이 실행 → 텍스트 분석만 수행.
    """
    _save(error)

    cached = _cache_get(error, source="gemini")
    if cached:
        return cached + "\n\n─── 캐시된 분석 결과입니다 (동일 에러 유형) ───"

    prompt = _build_prompt(error)
    if on_progress:
        on_progress("Gemini 분석 중...")

    if _is_waapi_available():
        result = _run_gemini(prompt, cwd=_HERE, cancel_event=cancel_event)
    else:
        _ensure_no_mcp_dir()
        result = _run_gemini(prompt, cwd=_NO_MCP_DIR, cancel_event=cancel_event)

    if result and not result.startswith("분석 실패") and not result.startswith("분석 시간 초과"):
        _cache_set(error, result, source="gemini")

    return result


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
