"""
AI 분석 엔진

동작 방식:
  "AI 분석" 버튼 클릭 시 selected_error.json 저장 후
  claude -p (print 모드) 를 subprocess 로 실행한다.
  Claude Code 세션 인증을 그대로 사용 — API 키 불필요.
  분석 결과는 호출한 쪽(대시보드)으로 문자열 반환.
"""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
SELECTED_ERROR_PATH = os.path.join(_HERE, "selected_error.json")

_PROMPT_TEMPLATE = """당신은 Wwise 오디오 미들웨어 전문가입니다.
아래 Wwise 런타임 에러를 분석하고 다음 항목을 한국어로 답하세요:

1. 근본 원인 (왜 이 에러가 발생했는지)
2. 단계별 해결 방법
3. 이 에러가 WAAPI 로 자동 수정 가능한지 여부와 방법

--- 에러 정보 ---
오브젝트 경로 : {object_path}
오브젝트명    : {object_name}
에러 유형     : {error_type}
에러 코드     : {error_code}
원본 메시지   : {description}
자동 분류 원인: {cause_auto}
"""


def _build_prompt(error) -> str:
    return _PROMPT_TEMPLATE.format(
        object_path=error.object_path or "(경로 없음)",
        object_name=error.object_name,
        error_type=error.error_type,
        error_code=error.error_code,
        description=error.description,
        cause_auto=error.cause,
    )


def _run_cli(cmd: list[str], cli_name: str, timeout: int = 60) -> str:
    """CLI subprocess 실행 공통 처리.

    Windows에서 npm 설치 CLI는 .cmd 래퍼이므로 shell=True로 실행해야
    cmd.exe가 PATH에서 정상적으로 탐색한다.
    """
    try:
        if sys.platform == "win32":
            # list2cmdline으로 인자를 올바르게 인용한 뒤 shell=True로 실행
            cmd_str = subprocess.list2cmdline(cmd)
            result = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                shell=True,
            )
        else:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        err = result.stderr.strip()
        logger.error("%s 오류: %s", cli_name, err)
        return f"분석 실패: {err or '알 수 없는 오류'}"
    except subprocess.TimeoutExpired:
        return f"분석 시간 초과 ({timeout}초). 다시 시도해주세요."
    except FileNotFoundError:
        return f"{cli_name} CLI 를 찾을 수 없습니다. 설치 여부를 확인하세요."
    except Exception as e:
        logger.error("%s 분석 예외: %s", cli_name, e)
        return f"분석 중 오류 발생: {e}"


def analyze(error) -> str:
    """Claude -p 로 에러를 분석해 결과 문자열을 반환한다."""
    _save(error)
    prompt = _build_prompt(error)
    return _run_cli(["claude", "-p", prompt], "Claude")


def analyze_gemini(error) -> str:
    """Gemini CLI 로 에러를 분석해 결과 문자열을 반환한다."""
    _save(error)
    prompt = _build_prompt(error)
    return _run_cli(["gemini", "-p", prompt], "Gemini")


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
