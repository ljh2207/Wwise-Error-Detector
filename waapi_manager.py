"""
WAAPI 연결 관리 모듈
Wwise Authoring API (WebSocket)에 대한 연결/해제/호출/구독을 담당한다.
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WaapiManager:
    def __init__(self, url: str = "ws://127.0.0.1:8080/waapi"):
        self.url = url
        self._client = None
        self._subscriptions: list = []

    # ------------------------------------------------------------------
    # 연결 / 해제
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        try:
            from waapi import WaapiClient, CannotConnectToWaapiException
            self._client = WaapiClient(url=self.url)
            logger.info("WAAPI 연결 성공: %s", self.url)
            return True
        except Exception as e:
            logger.error("WAAPI 연결 실패: %s", e)
            self._client = None
            return False

    def disconnect(self):
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()

        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        logger.info("WAAPI 연결 해제")

    # ------------------------------------------------------------------
    # WAAPI 호출
    # ------------------------------------------------------------------
    def call(self, uri: str, args: Optional[dict] = None,
             options: Optional[dict] = None):
        """WAAPI 함수 호출. 실패 시 None 반환."""
        if not self._client:
            logger.warning("WAAPI 미연결 상태에서 호출 시도: %s", uri)
            return None
        try:
            return self._client.call(uri, args or {}, options or {})
        except Exception as e:
            logger.error("WAAPI 호출 오류 [%s]: %s", uri, e)
            return None

    # ------------------------------------------------------------------
    # 토픽 구독
    # ------------------------------------------------------------------
    def subscribe(self, uri: str, callback: Callable,
                  options: Optional[dict] = None):
        """WAAPI 토픽 구독. 핸들러 반환 (unsubscribe에 사용)."""
        if not self._client:
            return None
        try:
            handler = self._client.subscribe(uri, callback, options or {})
            self._subscriptions.append(handler)
            return handler
        except Exception as e:
            logger.error("WAAPI 구독 오류 [%s]: %s", uri, e)
            return None

    def unsubscribe(self, handler):
        if handler in self._subscriptions:
            self._subscriptions.remove(handler)
        try:
            handler.unsubscribe()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._client is not None

    def ping(self) -> bool:
        result = self.call("ak.wwise.core.ping")
        return result is not None
