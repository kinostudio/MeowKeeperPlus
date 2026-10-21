"""正式 Web App factory；Host 以此入口建立 FastAPI。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api import create_app as _create_api_app
from .events import EventHub
from .session import SessionManager
from .state import CoreStateFacade


def create_app(
    core_controller: Any | None = None,
    settings: Mapping[str, Any] | None = None,
    *,
    facade: CoreStateFacade | None = None,
    event_hub: EventHub | None = None,
    session_manager: SessionManager | None = None,
    integrations: Any | None = None,
    dashboard_network: Any | None = None,
    source_config: Any | None = None,
    host_restart: Any | None = None,
    telegram_credentials: Any | None = None,
    **kwargs: Any,
) -> Any:
    """建立正式 app；核心由 controller callback 邊界接入，不在 import 時啟動。"""

    if facade is None:
        store = getattr(core_controller, "store", None)
        event_directory = getattr(core_controller, "event_directory", "data/events")
        callbacks = core_controller.callbacks() if core_controller is not None and hasattr(core_controller, "callbacks") else None
        facade = CoreStateFacade(
            store=store,
            event_directory=event_directory,
            settings=settings,
            callbacks=callbacks,
            dashboard_network=dashboard_network,
        )
    app = _create_api_app(
        facade=facade,
        event_hub=event_hub,
        session_manager=session_manager,
        integrations=integrations,
        dashboard_network=dashboard_network,
        source_config=source_config,
        host_restart=host_restart,
        telegram_credentials=telegram_credentials,
        **kwargs,
    )
    app.state.core_controller = core_controller
    app.state.settings = dict(settings or {})
    return app


__all__ = ["create_app"]
