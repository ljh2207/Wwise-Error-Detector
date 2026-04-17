"""
자동 수정 모듈

모든 수정은 Undo 그룹으로 감싸 단일 Ctrl+Z 로 되돌릴 수 있다.
각 핸들러는 (성공여부: bool, 메시지: str) 튜플을 반환한다.
"""
import logging
import re
from typing import Optional

from waapi_manager import WaapiManager
from error_classifier import WwiseError

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 에러 → 핸들러 매핑
# description 패턴 기반으로 핸들러를 선택한다.
# ------------------------------------------------------------------

_PATTERN_HANDLERS: list[tuple[str, callable]] = []


def _register(pattern: str):
    """패턴에 매칭되는 핸들러를 등록하는 데코레이터."""
    def decorator(fn):
        _PATTERN_HANDLERS.append((pattern, fn))
        return fn
    return decorator


# ------------------------------------------------------------------
# 공개 API
# ------------------------------------------------------------------

def apply_fix(waapi: WaapiManager, error: WwiseError) -> tuple[bool, str]:
    """
    에러에 맞는 수정 핸들러를 찾아 Undo 그룹 안에서 실행한다.
    """
    if not error.fix_available:
        return False, "이 에러는 자동 수정을 지원하지 않습니다."
    if not error.object_id:
        return False, "오브젝트 ID 가 없어 수정할 수 없습니다."

    handler = _find_handler(error)
    if not handler:
        return False, (
            f"수정 핸들러가 아직 구현되지 않았습니다.\n"
            f"에러: {error.description}"
        )

    waapi.call("ak.wwise.core.undo.beginGroup")
    try:
        success, msg = handler(waapi, error)
        if success:
            waapi.call(
                "ak.wwise.core.undo.endGroup",
                {"displayName": f"[ErrorDetector] {error.error_type} 자동 수정"},
            )
        else:
            waapi.call("ak.wwise.core.undo.cancelGroup")
        return success, msg
    except Exception as e:
        waapi.call("ak.wwise.core.undo.cancelGroup")
        logger.error("자동 수정 중 예외 발생: %s", e)
        return False, f"수정 중 예외 발생: {e}"


def describe_fix(error: WwiseError) -> str:
    """
    수정 미리보기 다이얼로그에 표시할 수정 내용 설명을 반환한다.
    """
    handler = _find_handler(error)
    if not handler:
        return "이 에러에 대한 자동 수정 방법이 정의되어 있지 않습니다."

    descs = {
        _fix_bus_not_found:
            "OutputBus 참조를 \\Master Audio Bus 로 재연결합니다.\n"
            "수정 후 Ctrl+Z 로 되돌릴 수 있습니다.",
        _fix_attenuation_not_found:
            "Attenuation 참조를 제거(None)합니다.\n"
            "Wwise 기본 감쇠 동작으로 동작합니다.\n"
            "수정 후 Ctrl+Z 로 되돌릴 수 있습니다.",
        _fix_rtpc_out_of_range:
            "RTPC 값 범위를 에러 메시지에서 파싱해 확장합니다.\n"
            "수정 후 Ctrl+Z 로 되돌릴 수 있습니다.",
        _fix_seek_out_of_range:
            "Seek 위치를 0 으로 초기화합니다.\n"
            "수정 후 Ctrl+Z 로 되돌릴 수 있습니다.",
        _fix_voice_limit:
            "MaxVoiceInstances 를 현재 값 + 2 로 늘립니다.\n"
            "수정 후 Ctrl+Z 로 되돌릴 수 있습니다.",
    }
    return descs.get(handler, "자동 수정이 적용됩니다.\n수정 후 Ctrl+Z 로 되돌릴 수 있습니다.")


# ------------------------------------------------------------------
# 내부 헬퍼
# ------------------------------------------------------------------

def _find_handler(error: WwiseError):
    combined = f"{error.description} {error.error_code}"
    for pattern, handler in _PATTERN_HANDLERS:
        if re.search(pattern, combined, re.IGNORECASE):
            return handler
    return None


def _get_object_info(waapi: WaapiManager, guid: str,
                     fields: list[str]) -> Optional[dict]:
    result = waapi.call(
        "ak.wwise.core.object.get",
        {"from": {"id": [guid]}},
        {"return": fields},
    )
    if result and result.get("return"):
        return result["return"][0]
    return None


def _get_master_bus_id(waapi: WaapiManager) -> Optional[str]:
    """
    프로젝트의 루트 Bus(최상위 버스)를 찾아 ID를 반환한다.
    이름이 'Master Audio Bus', 'Main Audio Bus' 등 프로젝트마다 다를 수 있어
    WAQL 로 WorkUnit 바로 아래 Bus 타입을 조회한다.
    """
    # WorkUnit 의 직접 자식 중 Bus 타입인 것이 루트 버스
    result = waapi.call(
        "ak.wwise.core.object.get",
        {"waql": "$ where type = \"Bus\" and parent.type = \"WorkUnit\""},
        {"return": ["id", "path", "name"]},
    )
    if result and result.get("return"):
        root_bus = result["return"][0]
        logger.info("루트 버스 발견: %s (%s)", root_bus.get("name"), root_bus.get("id"))
        return root_bus.get("id")

    # 폴백: 알려진 이름으로 시도
    for name in ["Master Audio Bus", "Main Audio Bus", "Master Bus"]:
        result = waapi.call(
            "ak.wwise.core.object.get",
            {"waql": f"$ where type = \"Bus\" and name = \"{name}\""},
            {"return": ["id"]},
        )
        if result and result.get("return"):
            return result["return"][0].get("id")
    return None


# ------------------------------------------------------------------
# 수정 핸들러 구현
# ------------------------------------------------------------------

@_register(r"[Bb]us.*not found|[Oo]utput [Bb]us.*not found|[Mm]aster [Bb]us.*not found")
def _fix_bus_not_found(waapi: WaapiManager,
                       error: WwiseError) -> tuple[bool, str]:
    """OutputBus 참조를 Master Audio Bus 로 재연결."""
    master_id = _get_master_bus_id(waapi)
    if not master_id:
        return False, "Master Audio Bus 를 찾을 수 없습니다."

    result = waapi.call(
        "ak.wwise.core.object.setReference",
        {
            "object": error.object_id,
            "reference": "OutputBus",
            "value": master_id,
        },
    )
    if result is not None:
        return True, (
            f"OutputBus 를 \\Master Audio Bus 로 재연결했습니다.\n"
            f"오브젝트: {error.object_path or error.object_name}"
        )
    return False, "OutputBus 재연결 실패. WAAPI 오류가 발생했습니다."


@_register(r"[Aa]ttenuation.*not found|[Mm]issing.*[Aa]ttenuation|[Aa]ttenuation.*missing")
def _fix_attenuation_not_found(waapi: WaapiManager,
                                error: WwiseError) -> tuple[bool, str]:
    """Attenuation 참조를 제거해 기본 감쇠 동작으로 복구."""
    result = waapi.call(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": error.object_id,
                    "@Attenuation": None,
                }
            ]
        },
    )
    if result is not None:
        return True, (
            f"Attenuation 참조를 제거했습니다.\n"
            f"오브젝트: {error.object_path or error.object_name}\n"
            f"이제 Wwise 기본 감쇠 동작을 따릅니다."
        )
    return False, "Attenuation 참조 제거 실패."


@_register(r"[Rr][Tt][Pp][Cc].*out of range|value.*out of range")
def _fix_rtpc_out_of_range(waapi: WaapiManager,
                            error: WwiseError) -> tuple[bool, str]:
    """
    에러 메시지에서 현재 값과 범위를 파싱해 Game Parameter 범위를 확장.
    예: "RTPC value 1.8 out of range [0.0, 1.0]"
    """
    # 현재 값 파싱
    val_m = re.search(r"value\s+([-\d.]+)", error.description, re.IGNORECASE)
    # 범위 파싱
    range_m = re.search(r"\[([-\d.]+)\s*,\s*([-\d.]+)\]", error.description)

    if not (val_m and range_m):
        return False, (
            "에러 메시지에서 값/범위 정보를 파싱할 수 없습니다.\n"
            f"메시지: {error.description}"
        )

    current_val = float(val_m.group(1))
    range_min = float(range_m.group(1))
    range_max = float(range_m.group(2))

    # 벗어난 방향에 따라 범위 확장
    new_min = min(range_min, current_val)
    new_max = max(range_max, current_val)

    # Game Parameter 오브젝트 ID 필요 — objectId 가 Game Parameter 자체인 경우만 처리
    result = waapi.call(
        "ak.wwise.core.gameParameter.setRange",
        {
            "object": error.object_id,
            "min": new_min,
            "max": new_max,
        },
    )
    if result is not None:
        return True, (
            f"Game Parameter 범위를 [{new_min}, {new_max}] 로 확장했습니다.\n"
            f"기존: [{range_min}, {range_max}] | 현재 값: {current_val}"
        )
    return False, "Game Parameter 범위 조정 실패."


@_register(r"[Ss]eek.*invalid|[Ii]nvalid.*[Ss]eek|[Ss]eek.*out of range")
def _fix_seek_out_of_range(waapi: WaapiManager,
                            error: WwiseError) -> tuple[bool, str]:
    """Seek 위치를 0 으로 초기화."""
    result = waapi.call(
        "ak.wwise.core.object.setProperty",
        {
            "object": error.object_id,
            "property": "SeekPercent",
            "value": 0.0,
        },
    )
    if result is not None:
        return True, f"Seek 위치를 0% 로 초기화했습니다.\n오브젝트: {error.object_path or error.object_name}"
    return False, "Seek 위치 초기화 실패."


@_register(r"[Mm]ax.*[Vv]oice|[Vv]oice.*[Ll]imit|[Vv]oice.*starv")
def _fix_voice_limit(waapi: WaapiManager,
                     error: WwiseError) -> tuple[bool, str]:
    """MaxVoiceInstances 를 현재 값 + 2 로 증가."""
    info = _get_object_info(waapi, error.object_id, ["MaxVoiceInstances"])
    if not info:
        return False, "오브젝트 정보를 가져올 수 없습니다."

    current = info.get("MaxVoiceInstances", 1)
    new_val = int(current) + 2

    result = waapi.call(
        "ak.wwise.core.object.setProperty",
        {
            "object": error.object_id,
            "property": "MaxVoiceInstances",
            "value": new_val,
        },
    )
    if result is not None:
        return True, f"MaxVoiceInstances 를 {current} → {new_val} 로 조정했습니다."
    return False, "MaxVoiceInstances 조정 실패."
