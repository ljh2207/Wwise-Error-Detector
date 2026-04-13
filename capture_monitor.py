"""
Capture Log 모니터링 모듈

설계 원칙:
  - WAAPI 구독 콜백은 Twisted reactor 스레드에서 실행된다.
  - Qt UI는 반드시 메인 스레드에서만 갱신되어야 한다.
  - 따라서 콜백에서는 queue 에 데이터를 넣기만 하고,
    Qt QTimer 가 주기적으로 큐를 드레인하며 object.get 호출 및 UI 갱신을 수행한다.
"""
import os
import queue
import re
import logging
from dataclasses import dataclass
from typing import Optional

from waapi_manager import WaapiManager

logger = logging.getLogger(__name__)


@dataclass
class _RawError:
    """Twisted 스레드에서 수집한 원시 데이터 (object.get 미조회 상태)."""
    time: int
    type: str
    severity: str
    object_name: str
    object_id: Optional[str]
    game_object_name: Optional[str]
    description: str
    error_code_name: str


class CaptureMonitor:
    def __init__(self, waapi: WaapiManager):
        self.waapi = waapi
        self._subscription = None
        # Twisted → Qt 메인 스레드 전달용 큐
        self.pending: queue.Queue[_RawError] = queue.Queue()
        self.is_monitoring: bool = False

    # ------------------------------------------------------------------
    # 모니터링 제어
    # ------------------------------------------------------------------

    def start_monitoring(self):
        """captureLog.itemAdded 토픽 구독 시작."""
        if self._subscription:
            return
        self._subscription = self.waapi.subscribe(
            "ak.wwise.core.profiler.captureLog.itemAdded",
            self._on_item,
        )
        self.is_monitoring = True
        logger.info("Capture Log 모니터링 시작")

    def stop_monitoring(self):
        """구독 해제."""
        if self._subscription:
            self.waapi.unsubscribe(self._subscription)
            self._subscription = None
        self.is_monitoring = False
        logger.info("Capture Log 모니터링 중지")

    # ------------------------------------------------------------------
    # Capture 제어 (Wwise Profiler Capture 직접 조작)
    # ------------------------------------------------------------------

    def start_capture(self) -> bool:
        result = self.waapi.call("ak.wwise.core.profiler.startCapture")
        if result is not None:
            logger.info("Profiler Capture 시작")
            return True
        logger.error("Profiler Capture 시작 실패")
        return False

    def stop_capture(self) -> bool:
        result = self.waapi.call("ak.wwise.core.profiler.stopCapture")
        if result is not None:
            logger.info("Profiler Capture 중지")
            return True
        logger.error("Profiler Capture 중지 실패")
        return False

    # ------------------------------------------------------------------
    # 오브젝트 경로 조회 (메인 스레드에서 호출)
    # ------------------------------------------------------------------

    def resolve_object_path(self, guid: str) -> Optional[str]:
        """GUID → Wwise 오브젝트 경로 조회. 메인 스레드 전용."""
        if not guid:
            return None
        result = self.waapi.call(
            "ak.wwise.core.object.get",
            {
                "from": {"id": [guid]},
                "options": {"return": ["path", "name", "type"]},
            },
        )
        logger.debug("resolve_object_path guid=%s → result=%s", guid, result)
        if result and result.get("return"):
            return result["return"][0].get("path")
        return None

    def resolve_object_from_filename(
        self, description: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """description에서 .wav 파일명을 추출해 해당 파일을 소스로 쓰는 Sound 오브젝트를 역추적.

        반환: (object_id, object_path, object_name)
        대상 패턴: "Media path/to/File.wav could not be updated"
        """
        m = re.search(r"[Mm]edia\s+(.+?\.wav)\b", description, re.IGNORECASE)
        if not m:
            return None, None, None

        stem = os.path.splitext(os.path.basename(m.group(1)))[0]

        result = self.waapi.call(
            "ak.wwise.core.object.get",
            {
                "waql": f'$ where type = "AudioFileSource" and name = "{stem}"',
                "options": {"return": ["id", "path", "name", "parent.id", "parent.path", "parent.name"]},
            },
        )
        if not (result and result.get("return")):
            return None, None, None

        src = result["return"][0]
        obj_id   = src.get("parent.id")   or src.get("id")
        obj_path = src.get("parent.path") or src.get("path")
        obj_name = src.get("parent.name") or src.get("name")
        logger.info("파일명 역추적 성공: %s → %s", stem, obj_path)
        return obj_id, obj_path, obj_name

    # ------------------------------------------------------------------
    # WAAPI 콜백 (Twisted reactor 스레드 — UI 접근 금지)
    # ------------------------------------------------------------------

    def _on_item(self, **kwargs):
        if kwargs.get("severity") != "Error":
            return
        raw = _RawError(
            time=kwargs.get("time", 0),
            type=kwargs.get("type", ""),
            severity=kwargs.get("severity", "Error"),
            object_name=kwargs.get("objectName", ""),
            object_id=kwargs.get("objectId"),
            game_object_name=kwargs.get("gameObjectName"),
            description=kwargs.get("description", ""),
            error_code_name=kwargs.get("errorCodeName", ""),
        )
        self.pending.put(raw)
