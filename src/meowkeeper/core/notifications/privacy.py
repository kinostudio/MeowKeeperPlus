"""Fail-closed service boundary for camera privacy actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any

from .control_journal import CameraControlJournal, new_operation_id


class PrivacyService:
    """Delegate privacy changes to an explicitly configured camera adapter.

    The service deliberately does not infer a safe camera pose. An adapter must
    return an explicit successful result after applying and verifying the
    requested state; merely sending a movement command is not success.
    """

    def __init__(self, adapter: Any | None = None, *, journal: CameraControlJournal | None = None) -> None:
        self.adapter = adapter
        self.journal = journal
        self._operation_lock = Lock()

    def hide(self, *, camera_id: object = None) -> dict[str, Any]:
        return self._run("hide", camera_id=camera_id, unavailable="尚未設定經驗證的隱私位置；沒有移動攝影機。")

    def show(self, *, camera_id: object = None) -> dict[str, Any]:
        return self._run("show", camera_id=camera_id, unavailable="尚未設定經驗證的恢復位置；沒有移動攝影機。")

    def _run(self, action: str, *, camera_id: object, unavailable: str) -> dict[str, Any]:
        operation_id = new_operation_id()
        requested = {"action": action, "privacy_mode": action == "hide"}

        def finish(*, final_status: str, success: bool, message: str,
                   command_sent: bool | None, transport_result: str,
                   readback_attempted: bool, readback_result: object,
                   observed_change: bool | None, failure_reason: str | None,
                   status: str = "failed") -> dict[str, Any]:
            if self.journal is not None:
                self.journal.append(
                    operation_id=operation_id,
                    operation_type=f"privacy_{action}",
                    camera_id=camera_id,
                    requested_target=requested,
                    command_sent=command_sent,
                    transport_result=transport_result,
                    readback_attempted=readback_attempted,
                    readback_result=readback_result,
                    observed_change=observed_change,
                    final_status=final_status,
                    failure_reason=failure_reason,
                )
            return {
                "status": status,
                "final_status": final_status,
                "success": success,
                "verified": success,
                "privacy_mode": action == "hide" if success else None,
                "operation_id": operation_id,
                "diagnostic_url": f"/diagnostics?operation_id={operation_id}#xiaomi-control-diagnostics",
                "message_zh_tw": message,
            }

        operation: Callable[[], Any] | None = getattr(self.adapter, action, None) if self.adapter else None
        reader = getattr(self.adapter, "read_privacy_state", None)
        if not callable(operation) or not callable(reader):
            return finish(final_status="unsupported", success=False, message=unavailable,
                          command_sent=False, transport_result="not_supported",
                          readback_attempted=False, readback_result="not_attempted",
                          observed_change=None, failure_reason=unavailable, status="unavailable")
        if not self._operation_lock.acquire(blocking=False):
            message = "攝影機隱私操作進行中；沒有重複送出控制指令。"
            return finish(final_status="failed", success=False, message=message,
                          command_sent=False, transport_result="not_attempted",
                          readback_attempted=False, readback_result="not_attempted",
                          observed_change=None, failure_reason=message, status="busy")
        try:
            target = action == "hide"
            try:
                before = self._verified_state(reader())
            except TimeoutError:
                message = "讀取攝影機目前狀態逾時；沒有送出控制指令。"
                return finish(final_status="timeout", success=False, message=message,
                              command_sent=False, transport_result="not_attempted",
                              readback_attempted=True, readback_result="timeout",
                              observed_change=None, failure_reason=message)
            except Exception as exc:  # noqa: BLE001 - never serialize camera exception details
                message = f"讀取攝影機目前狀態失敗（{type(exc).__name__}）；沒有送出控制指令。"
                return finish(final_status="readback_failed", success=False, message=message,
                              command_sent=False, transport_result="not_attempted",
                              readback_attempted=True, readback_result="failed",
                              observed_change=None, failure_reason=message)
            if before is None and action == "hide":
                message = "攝影機目前隱私狀態無法驗證；沒有送出控制指令。"
                return finish(final_status="readback_failed", success=False, message=message,
                              command_sent=False, transport_result="not_attempted",
                              readback_attempted=True, readback_result="unverified",
                              observed_change=None, failure_reason=message)
            unchanged = before == target
            command_sent = False
            readback_result: object = {"verified": True, "privacy_mode": before} if before is not None else "unverified"
            if not unchanged:
                try:
                    result = operation()
                    command_sent = True
                except TimeoutError:
                    message = "控制請求逾時；是否已送達無法確認，隱私狀態未確認。"
                    return finish(final_status="timeout", success=False, message=message,
                                  command_sent=None, transport_result="timeout",
                                  readback_attempted=False, readback_result="not_attempted",
                                  observed_change=None, failure_reason=message)
                except Exception as exc:  # noqa: BLE001 - do not expose URLs or credentials
                    message = f"控制請求失敗（{type(exc).__name__}）；攝影機狀態未確認。"
                    return finish(final_status="failed", success=False, message=message,
                                  command_sent=None, transport_result="error",
                                  readback_attempted=False, readback_result="not_attempted",
                                  observed_change=None, failure_reason=message)
                if not isinstance(result, Mapping) or result.get("success") is not True:
                    message = "控制端未接受本次隱私操作；沒有確認狀態改變。"
                    return finish(final_status="failed", success=False, message=message,
                                  command_sent=True, transport_result="rejected",
                                  readback_attempted=False, readback_result="not_attempted",
                                  observed_change=None, failure_reason=message)
                try:
                    after = self._verified_state(reader())
                    readback_result = {"verified": after is not None, "privacy_mode": after}
                except TimeoutError:
                    message = "控制請求已送出，但攝影機狀態讀回逾時；目前狀態未確認。"
                    return finish(final_status="readback_failed", success=False, message=message,
                                  command_sent=True, transport_result="accepted",
                                  readback_attempted=True, readback_result="timeout",
                                  observed_change=None, failure_reason=message)
                except Exception as exc:  # noqa: BLE001 - camera errors must not leak URLs or credentials
                    message = f"控制請求已送出，但狀態讀回失敗（{type(exc).__name__}）；目前狀態未確認。"
                    return finish(final_status="readback_failed", success=False, message=message,
                                  command_sent=True, transport_result="accepted",
                                  readback_attempted=True, readback_result="failed",
                                  observed_change=None, failure_reason=message)
                if after is None:
                    message = "控制請求已送出，但攝影機沒有提供可驗證的狀態讀回；目前狀態未確認。"
                    return finish(final_status="readback_failed", success=False, message=message,
                                  command_sent=True, transport_result="accepted",
                                  readback_attempted=True, readback_result="unverified",
                                  observed_change=None, failure_reason=message)
                if after != target:
                    final_status = "confirmed_no_change" if before is not None and after == before else "failed"
                    message = "控制請求已送出，但讀回狀態仍未符合目標；未宣稱隱私模式已切換。"
                    return finish(final_status=final_status, success=False, message=message,
                                  command_sent=True, transport_result="accepted",
                                  readback_attempted=True, readback_result={"verified": True, "privacy_mode": after},
                                  observed_change=before is not None and before != after,
                                  failure_reason=message)
            return {
                **finish(
                    final_status="confirmed_success", success=True,
                    message=(
                    "隱私模式已啟用；已讀回相機狀態確認。"
                    if target else "已恢復攝影機畫面；已讀回相機狀態確認。"
                    ),
                    command_sent=command_sent,
                    transport_result="not_needed" if unchanged else "accepted",
                    readback_attempted=True,
                    readback_result=readback_result,
                    observed_change=False if unchanged else before is not None and before != target,
                    failure_reason=None,
                    status="unchanged" if unchanged else "ok",
                )
            }
        except Exception as exc:  # noqa: BLE001 - camera errors must not leak URLs or credentials
            message = f"攝影機隱私操作失敗（{type(exc).__name__}）；目前狀態未確認。"
            return finish(final_status="failed", success=False, message=message,
                          command_sent=command_sent, transport_result="error",
                          readback_attempted=True, readback_result="failed",
                          observed_change=None, failure_reason=message)
        finally:
            self._operation_lock.release()

    @staticmethod
    def _verified_state(result: Any) -> bool | None:
        if isinstance(result, Mapping) and result.get("verified") is True:
            mode = result.get("privacy_mode")
            if isinstance(mode, bool):
                return mode
        return None

__all__ = ["PrivacyService"]
