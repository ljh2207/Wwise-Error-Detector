"""
에러 분류 모듈
Capture Log에서 수신한 에러 메시지를 분석해 원인과 해결 방법을 분류한다.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ------------------------------------------------------------------
# 데이터 모델
# ------------------------------------------------------------------

@dataclass
class WwiseError:
    id: int
    timestamp_ms: int
    detected_at: datetime
    object_name: str
    object_path: Optional[str]       # WAAPI object.get 으로 조회
    object_id: Optional[str]         # GUID
    game_object_name: Optional[str]
    error_type: str                  # Capture Log 의 type 컬럼
    error_code: str                  # errorCodeName
    description: str                 # 원본 에러 메시지
    severity: str                    # "Error"
    cause: str                       # 분류된 원인
    solution: str                    # 해결 방법
    fix_available: bool = False      # 자동 수정 가능 여부
    fix_applied: bool = False
    ai_analyzed: bool = False
    ai_analysis: str = ""
    gemini_analyzed: bool = False
    gemini_analysis: str = ""


# ------------------------------------------------------------------
# 규칙 기반 에러 패턴
# (regex, 원인, 해결방법, 자동수정가능)
# ------------------------------------------------------------------

_PATTERNS: list[tuple[str, str, str, bool]] = [
    (
        r"No audio file|no.*audio.*source|audio source.*missing",
        "오디오 소스에 연결된 파일이 없음",
        "오디오 파일을 임포트하거나 해당 Sound 오브젝트에 소스를 다시 연결하세요",
        True
    ),
    (
        r"[Ff]ile with file [Ii][Dd].*not found|not found in path",
        "오디오 파일이 지정된 경로에 존재하지 않음",
        "SoundBank를 재생성하거나, 원본 오디오 파일을 다시 임포트하세요",
        False
    ),
    (
        r"could not be updated from Wwise|[Mm]edia.*could not be updated",
        "Wwise 프로젝트에서 미디어를 업데이트할 수 없음 (파일 누락 또는 손상)",
        "오디오 파일을 다시 임포트하고 SoundBank를 재생성하세요",
        False
    ),
    (
        r"[Mm]edia not found|missing media|media.*missing",
        "사운드 뱅크에 미디어가 포함되지 않음",
        "SoundBank를 재생성하거나, 해당 오브젝트의 SoundBank 포함 설정을 확인하세요",
        False
    ),
    (
        r"[Aa]ttenuation.*not found|[Mm]issing.*[Aa]ttenuation|[Aa]ttenuation.*missing",
        "참조된 Attenuation 오브젝트가 존재하지 않거나 삭제됨",
        "Attenuation 참조를 제거하거나 유효한 Attenuation 오브젝트로 재설정하세요",
        True
    ),
    (
        r"[Oo]utput [Bb]us.*not found|[Bb]us.*not found|[Mm]aster [Bb]us.*not found",
        "참조된 Output Bus가 존재하지 않거나 이름이 변경됨",
        "Output Bus를 Master Audio Bus 또는 유효한 버스로 재연결하세요",
        True
    ),
    (
        r"[Ee]vent not found|[Nn]o event|event.*missing",
        "호출하려는 이벤트가 SoundBank에 포함되지 않거나 존재하지 않음",
        "이벤트 이름/ID 확인 후 재생성하거나 SoundBank에 포함시키세요",
        False
    ),
    (
        r"[Rr][Tt][Pp][Cc].*out of range|value.*out of range|out of range.*[Rr][Tt][Pp][Cc]",
        "RTPC 값이 Game Parameter에 설정된 범위를 벗어남",
        "Game Parameter의 Min/Max 범위를 조정하거나 RTPC 연결 값을 재설정하세요",
        True
    ),
    (
        r"[Pp]lug-?[Ii]n.*not found|[Pp]lug-?[Ii]n.*missing|[Mm]issing.*[Pp]lug-?[Ii]n",
        "사용된 플러그인이 설치되어 있지 않거나 인식되지 않음",
        "플러그인 설치 여부를 확인하거나 기본 플러그인(Wwise 내장)으로 교체하세요",
        False
    ),
    (
        r"[Ss]eek.*invalid|[Ii]nvalid.*[Ss]eek|[Ss]eek.*out of range",
        "Seek 위치가 오디오 파일 총 길이를 초과함",
        "Seek 값이 오디오 파일 길이 이내인지 확인하고 조정하세요",
        True
    ),
    (
        r"[Ss]witch.*not found|[Ss]tate.*not found",
        "참조된 Switch 또는 State 값이 Group에 정의되어 있지 않음",
        "Switch/State Group에서 해당 값이 정의되어 있는지 확인하고 추가하세요",
        False
    ),
    (
        r"[Ss]tream.*error|[Ss]treaming.*fail|[Ss]tream.*fail",
        "오디오 스트리밍 중 파일 접근 오류 발생",
        "스트리밍 파일 경로 및 접근 권한을 확인하고 파일이 존재하는지 점검하세요",
        False
    ),
    (
        r"[Ss]ound [Ee]ngine.*not init|[Ee]ngine.*not init",
        "사운드 엔진이 초기화되지 않은 상태에서 호출됨",
        "사운드 엔진이 완전히 초기화된 이후에 해당 API를 호출하도록 순서를 조정하세요",
        False
    ),
    (
        r"[Mm]ax.*[Vv]oice|[Vv]oice.*[Ll]imit|[Vv]oice.*starv",
        "최대 Voice 수 초과 또는 Voice Starvation 발생",
        "Voice Limit 설정을 높이거나 우선순위가 낮은 사운드의 Priority를 조정하세요",
        True
    ),
    (
        r"[Cc]onversion.*fail|[Cc]onvert.*error|[Cc]onversion.*error",
        "오디오 파일 변환(Conversion) 중 오류 발생",
        "Conversion Settings를 확인하고 원본 파일이 지원되는 형식인지 점검하세요",
        False
    ),
]


def classify_error(description: str,
                   error_code: str = "") -> tuple[str, str, bool]:
    """에러 설명을 분석해 (원인, 해결방법, 자동수정가능) 튜플을 반환."""
    combined = f"{description} {error_code}"
    for pattern, cause, solution, fixable in _PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return cause, solution, fixable
    return (
        "알 수 없는 에러 (AI 분석 필요)",
        "AI 분석 버튼을 눌러 Claude의 진단을 받아보세요",
        False,
    )


# ------------------------------------------------------------------
# 에러 생성 헬퍼
# ------------------------------------------------------------------

_counter = 0


def reset_counter():
    global _counter
    _counter = 0


def make_error(raw_data: dict, object_path: Optional[str] = None) -> WwiseError:
    """WAAPI captureLog.itemAdded 데이터에서 WwiseError 생성."""
    global _counter
    _counter += 1

    desc = raw_data.get("description", "")
    code = raw_data.get("errorCodeName", "")
    cause, solution, fixable = classify_error(desc, code)

    return WwiseError(
        id=_counter,
        timestamp_ms=raw_data.get("time", 0),
        detected_at=datetime.now(),
        object_name=raw_data.get("objectName", ""),
        object_path=object_path,
        object_id=raw_data.get("objectId"),
        game_object_name=raw_data.get("gameObjectName"),
        error_type=raw_data.get("type", ""),
        error_code=code,
        description=desc,
        severity=raw_data.get("severity", "Error"),
        cause=cause,
        solution=solution,
        fix_available=fixable,
    )
