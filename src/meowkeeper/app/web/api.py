"""FastAPI 版本化 Web API 與本地 Dashboard。

FastAPI 是可選 runtime 依賴；匯入本檔不會讓既有媒體 CLI 失效，只有呼叫
``create_app`` 時才會檢查 Web extra 是否已放入發布包。
"""

import hashlib
import inspect
import json
import logging
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import ApiAuthenticator, AuthError
from .contracts import SettingsValidationError
from .events import EventHub
from .session import SessionAuthError, SessionManager
from .state import CoreStateFacade


def create_app(
    *,
    facade: CoreStateFacade | None = None,
    authenticator: ApiAuthenticator | None = None,
    token: str | None = None,
    event_hub: EventHub | None = None,
    session_manager: SessionManager | None = None,
    template_directory: str | Path | None = None,
    static_directory: str | Path | None = None,
    integrations: Any | None = None,
    dashboard_network: Any | None = None,
    source_config: Any | None = None,
    host_restart: Any | None = None,
    telegram_credentials: Any | None = None,
    status_service: Any | None = None,
    vision_settings: Any | None = None,
) -> Any:
    """建立 FastAPI app；預設不繫結 host，也不啟動 server。"""

    try:
        from fastapi import (
            BackgroundTasks,
            Body,
            FastAPI,
            File,
            Form,
            HTTPException,
            Request,
            UploadFile,
            WebSocket,
            status,
        )
        from fastapi.responses import (
            FileResponse,
            HTMLResponse,
            JSONResponse,
            RedirectResponse,
            Response,
            StreamingResponse,
        )
        from fastapi.staticfiles import StaticFiles
        from fastapi.templating import Jinja2Templates
    except ImportError as exc:  # pragma: no cover - depends on optional release extra
        raise RuntimeError(
            "Web Dashboard 缺少 FastAPI runtime；請由建置者將 [runtime] 依賴放入發布包，"
            "終端使用者不需要自行執行 pip。"
        ) from exc

    if authenticator is None:
        if token:
            authenticator = ApiAuthenticator.from_token(token)
        else:
            try:
                authenticator = ApiAuthenticator.from_environment()
            except AuthError:
                # Session-only first setup remains possible; the ephemeral token is
                # never shown or persisted and cannot be used by another process.
                authenticator = ApiAuthenticator.generate()
    core = facade or CoreStateFacade()
    hub = event_hub or EventHub()
    sessions = session_manager or SessionManager()
    from meowkeeper.app.updates import UpdateManager
    update_root = Path(__file__).resolve().parents[4]
    if not (update_root / "build_manifest.json").is_file():
        installed = Path(r"C:\ProgramData\MeowKeeperPlus\App")
        if (installed / "build_manifest.json").is_file():
            update_root = installed
    update_data = core.store.path.parent if core.store is not None else update_root / "data"
    updater = UpdateManager(update_root, update_data)
    from meowkeeper.app.cpu_temperature_setup import CpuTemperatureSetup
    cpu_setup = CpuTemperatureSetup(update_root)
    package_root = Path(__file__).resolve().parent
    templates_path = Path(template_directory or package_root / "templates").resolve()
    static_path = Path(static_directory or package_root / "static").resolve()
    setup_redirect = token is None and not os.getenv("MEOWKEEPER_WEB_TOKEN", "").strip()
    resource_recorder = None
    resource_error = None
    if core.store is not None and str(core.store.path) != ":memory:":
        from meowkeeper.core.analytics.resource_history import ResourceHistory, ResourceRecorder
        try:
            data_root = core.store.path.resolve().parent
            resource_recorder = ResourceRecorder(
                ResourceHistory(data_root / "resource-history.db"), core,
                Path(__file__).resolve().parents[4], data_root,
            )
        except (OSError, sqlite3.Error):
            resource_error = "資源歷史儲存空間無法使用；影音監控不受影響。"

    @asynccontextmanager
    async def lifespan(_app: Any) -> Any:
        startup = core._callbacks.get("startup")
        if startup is not None:
            result = startup()
            if inspect.isawaitable(result):
                await result
        if resource_recorder is not None:
            resource_recorder.start()
        try:
            yield
        finally:
            await _app.state.preview_transcode.close()
            if resource_recorder is not None:
                resource_recorder.stop()
            shutdown = core._callbacks.get("shutdown")
            if shutdown is not None:
                result = shutdown()
                if inspect.isawaitable(result):
                    await result
            integration = getattr(_app.state, "integrations", None)
            provider = getattr(integration, "relay_provider", None)
            if provider is not None:
                provider.stop(revoke=False)
            jobs = getattr(integration, "connection_jobs", None)
            if jobs is not None:
                jobs.close()
            go2rtc = getattr(integration, "go2rtc", None)
            stop = getattr(go2rtc, "stop", None)
            if callable(stop):
                stop()

    app = FastAPI(title="喵喵守護者+", version="v1", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.core = core
    app.state.resource_recorder = resource_recorder
    app.state.authenticator = authenticator
    app.state.event_hub = hub
    app.state.sessions = sessions
    app.state.integrations = integrations
    app.state.dashboard_network = dashboard_network
    app.state.source_config = source_config
    app.state.host_restart = host_restart
    app.state.telegram_credentials = telegram_credentials
    app.state.vision_settings = vision_settings
    app.state.pet_embedding_jobs = {}
    app.state.pet_embedding_jobs_lock = threading.RLock()
    app.state.pet_comparison_lock = threading.Lock()
    app.state.pet_comparison_embedder = None
    app.state.full_backup_jobs = {}
    app.state.full_backup_jobs_lock = threading.RLock()
    from meowkeeper.app.web.viewers import LiveViewerTracker
    from meowkeeper.integrations.tailscale.aliases import TailscaleAliasStore
    app.state.live_viewer_tracker = LiveViewerTracker(dashboard_ttl=60.0, jpeg_ttl=15.0, maximum=256)
    alias_path = None
    if core.store is not None and str(getattr(core.store, "path", ":memory:")) != ":memory:":
        try:
            alias_path = Path(core.store.path).resolve().parent / "state" / "tailscale-device-aliases.json"
        except (OSError, TypeError, ValueError):
            alias_path = None
    app.state.tailscale_alias_store = TailscaleAliasStore(alias_path)
    from .preview_mse import PreviewMseSlots, proxy_mse_preview
    from .preview_rtc import SharedRtcSource, serve_rtc
    from .preview_transcode import SharedPreviewTranscode
    app.state.preview_mse_slots = PreviewMseSlots(maximum=4)
    app.state.preview_rtc_source = SharedRtcSource()
    app.state.preview_transcode = SharedPreviewTranscode()
    app.state.templates = Jinja2Templates(directory=str(templates_path))
    from .telemetry_help import load_help_terms
    app.state.templates.env.globals["help_terms"] = load_help_terms()
    from .release_info import release_information
    app.state.templates.env.globals["release_info"] = release_information()
    if static_path.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    def session_from_request(request: Request) -> Any:
        return sessions.authenticate(request.cookies.get("meowkeeper_session"))

    def require_auth(request: Request) -> str:
        candidate = request.headers.get("authorization") or request.headers.get("x-meowkeeper-token")
        if authenticator.check_token(candidate):
            return "token"
        record = session_from_request(request)
        if record is not None:
            # Only the actual ASGI socket peer is considered. Forwarded headers
            # are intentionally ignored so a browser cannot claim another device.
            client = getattr(request, "client", None)
            tracker = getattr(app.state, "live_viewer_tracker", None)
            if tracker is not None:
                tracker.touch_dashboard(record.session_id, getattr(client, "host", None))
            return "session"
        stale_session = request.cookies.get("meowkeeper_session")
        tracker = getattr(app.state, "live_viewer_tracker", None)
        if stale_session and tracker is not None:
            tracker.end_session(stale_session)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 Web Dashboard 登入。")

    def require_mutation(request: Request) -> str:
        auth_kind = require_auth(request)
        if auth_kind == "token":
            valid = authenticator.check_csrf(request.headers.get("x-meowkeeper-csrf"))
        else:
            record = session_from_request(request)
            valid = record is not None and request.headers.get("x-meowkeeper-csrf") == record.csrf_token
        if not valid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 驗證失敗。")
        return auth_kind

    def json_body(body: Any) -> dict[str, Any]:
        return body if isinstance(body, dict) else {}

    def vision_service():
        if app.state.vision_settings is None:
            raise HTTPException(status_code=503, detail="此執行環境尚未接入可選視覺服務設定；本機監控仍可使用。")
        return app.state.vision_settings

    @app.get("/api/v1/vision/settings")
    def vision_settings_read(request: Request):
        require_auth(request)
        try:
            return vision_service().status()
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(status_code=503, detail="視覺服務設定無法讀取；本機監控不受影響。") from None

    @app.get("/api/v1/vision/usage")
    def vision_usage_read(request: Request, hours: int = 24, limit: int = 100, offset: int = 0):
        require_auth(request)
        if hours not in {1, 24, 168, 720}:
            raise HTTPException(status_code=422, detail="用量區間只允許最近1小時、24小時、7天或30天。")
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="分析紀錄每頁只允許 1～200 筆。")
        if not 0 <= offset <= 1_000_000:
            raise HTTPException(status_code=422, detail="分析紀錄頁面位置超出允許範圍。")
        try:
            return vision_service().usage(hours=hours, limit=limit, offset=offset)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            raise HTTPException(status_code=503, detail="AI 用量紀錄暫時無法讀取；本機監控不受影響。") from None

    @app.put("/api/v1/vision/settings")
    def vision_settings_save(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        try:
            return vision_service().save(body)
        except ValueError as error:
            # These validation errors are static, user-facing messages from the
            # settings/provider validators; unlike raw OS errors they contain
            # no filesystem paths or credential material.
            raise HTTPException(status_code=422, detail=str(error)) from None
        except TypeError:
            raise HTTPException(status_code=422, detail="設定格式不正確；請確認欄位值及圖片外送同意。") from None
        except OSError:
            raise HTTPException(status_code=503, detail="設定無法讀寫本機資料；請確認 Host 執行帳號有權限存取應用程式資料。") from None
        except RuntimeError:
            raise HTTPException(status_code=503, detail="設定服務暫時無法完成保存；本機監控不受影響，請稍後重試。") from None

    @app.post("/api/v1/vision/health")
    def vision_connection_check(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if not isinstance(body, dict) or set(body) != {"confirm_metadata_request"} or body["confirm_metadata_request"] is not True:
            raise HTTPException(status_code=428, detail="請確認只將Key交給目前設定的服務讀取模型資訊，不送圖片。")
        try:
            return vision_service().check_connection()
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(status_code=422, detail="模型資訊檢查未完成；請先保存正確的服務、模型與Key，本機監控不受影響。") from None

    @app.post("/api/v1/vision/image-test")
    def vision_demo_image_test(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if not isinstance(body, dict) or set(body) != {"confirm_demo_upload"} or body["confirm_demo_upload"] is not True:
            raise HTTPException(status_code=428, detail="請明確確認只將程式繪製的示範插圖傳給目前設定的服務。")
        try:
            return vision_service().test_demo_image()
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
            raise HTTPException(status_code=422, detail="示範圖片測試未完成；未讀取居家畫面，本機監控不受影響。") from None

    @app.post("/api/v1/vision/count-tokens")
    def vision_count_tokens_probe(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if (not isinstance(body, dict) or set(body) != {"confirm_synthetic_count_tokens"}
                or body["confirm_synthetic_count_tokens"] is not True):
            raise HTTPException(status_code=428, detail="請確認只對程式繪製的合成圖片呼叫 Gemini Token 數探測。")
        try:
            return vision_service().count_tokens_demo()
        except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
            raise HTTPException(status_code=422, detail="Gemini Token 數探測未完成；未讀取居家影格。") from None

    @app.post("/api/v1/vision/key-stats/reset")
    def vision_key_stats_reset(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if (not isinstance(body, dict) or set(body) - {"confirm_reset", "key_id"}
                or body.get("confirm_reset") is not True
                or (body.get("key_id") is not None and not isinstance(body.get("key_id"), str))):
            raise HTTPException(status_code=428, detail="請明確確認要歸零 API Key 呼叫統計。")
        try:
            service = vision_service()
            service.reset_key_stats(body.get("key_id"))
            return service.status()
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(status_code=503, detail="API Key 呼叫統計暫時無法歸零；本機監控不受影響。") from None

    _local_ip_cache: set[str] = set()
    _local_ip_cache_at: float = 0.0

    def _get_local_ips() -> set[str]:
        nonlocal _local_ip_cache, _local_ip_cache_at
        now = time.monotonic()
        if _local_ip_cache and (now - _local_ip_cache_at < 30.0):
            return _local_ip_cache
        ips = {"127.0.0.1", "::1", "localhost"}
        try:
            import socket

            hostname = socket.gethostname()
            ips.add(socket.gethostbyname(hostname).lower())
            for ip in socket.gethostbyname_ex(hostname)[2]:
                ips.add(ip.lower())
            for info in socket.getaddrinfo(hostname, None):
                ips.add(info[4][0].lower())
        except Exception:
            pass
        _local_ip_cache = ips
        _local_ip_cache_at = now
        return ips

    def is_local_request(request: Request) -> bool:
        host = (request.client.host if request.client else "").lower()
        if host in {"127.0.0.1", "::1", "localhost"}:
            return True
        try:
            import ipaddress

            if ipaddress.ip_address(host).is_loopback:
                return True
        except ValueError:
            return False
        return host in _get_local_ips()

    def _is_trusted_credential_client(request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        return is_local_request(request)

    @app.post("/api/v1/vision/credential")
    def vision_credential_save(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if not _is_trusted_credential_client(request):
            raise HTTPException(status_code=403, detail="請在主機本機頁面或 HTTPS 安全連線保存金鑰，不透過未加密區網傳送。")
        service = vision_service()
        allowed_fields = {"key", "alias", "action", "key_id", "order", "clear", "reauth"}
        if not isinstance(body, dict) or set(body) - allowed_fields or body.get("reauth") is not True:
            raise HTTPException(status_code=428, detail="請明確確認修改目前服務的金鑰。")
        try:
            action = body.get("action")
            if body.get("clear") is True or action == "clear":
                service.clear_key()
            elif action == "delete":
                key_id = body.get("key_id")
                if not key_id or not isinstance(key_id, str):
                    raise ValueError("缺少要刪除的金鑰識別碼。")
                service.delete_key(key_id)
            elif action == "reorder":
                order = body.get("order")
                if not isinstance(order, list):
                    raise ValueError("金鑰排序資料格式不正確。")
                service.reorder_keys(order)
            elif action == "add":
                service.add_key(body.get("key"), alias=body.get("alias", ""))
            else:
                if body.get("key"):
                    service.add_key(body.get("key"), alias=body.get("alias", ""))
                else:
                    raise ValueError("未提供有效的金鑰操作。")
            return service.status()
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error) if isinstance(error, ValueError) else "金鑰未保存；請確認 Windows 安全儲存可用與服務已設定。") from None

    def pet_registry() -> Any:
        from meowkeeper.core.identity.pet_registry import PetRegistry

        return PetRegistry(core.event_directory.parent / "pets")

    def pet_payload(profile: Any, *, job: dict[str, Any] | None = None) -> dict[str, Any]:
        current_job = job or {}
        return {
            "pet_id": profile.pet_id,
            "display_name": profile.display_name,
            "coat_description": profile.coat_description,
            "pattern_description": profile.pattern_description,
            "reference_image_count": len(profile.reference_images),
            "reference_images": [{"image_id": Path(item).name,
                                  "url": f"/api/v1/pets/{profile.pet_id}/images/{Path(item).name}"}
                                 for item in profile.reference_images],
            "embedding_count": profile.embedding_count,
            **pet_registry().gallery_summary(profile.pet_id),
            "identity_model_version": profile.identity_model_version,
            "enabled": profile.enabled,
            "semantic_traits": getattr(profile, "semantic_traits", []) or (profile.effective_traits() if hasattr(profile, "effective_traits") else []),
            "embedding_status": current_job.get(
                "status",
                "ready" if profile.embedding_count > 0 else "not_started",
            ),
            "embedding_message_zh_tw": current_job.get("message_zh_tw"),
        }

    def pet_job(pet_id: str) -> dict[str, Any] | None:
        with app.state.pet_embedding_jobs_lock:
            item = app.state.pet_embedding_jobs.get(pet_id)
            return dict(item) if isinstance(item, dict) else None

    def set_pet_job(pet_id: str, **values: Any) -> dict[str, Any]:
        with app.state.pet_embedding_jobs_lock:
            current = dict(app.state.pet_embedding_jobs.get(pet_id) or {})
            current.update(values)
            app.state.pet_embedding_jobs[pet_id] = current
            return dict(current)

    def build_pet_embeddings(pet_id: str) -> None:
        """在背景工作中建立 DINOv2 特徵，不阻塞影音／通知主流程。"""

        set_pet_job(pet_id, status="running", message_zh_tw="正在建立身分特徵；影音監控不會停止。")
        try:
            from meowkeeper.core.identity.color_features import extract_hsv_histogram
            from meowkeeper.core.identity.dinov2_embedder import shared_cpu_embedder

            registry = pet_registry()
            profile = registry.get_pet(pet_id)
            if profile is None:
                raise ValueError("找不到寵物登錄資料。")
            embedder = shared_cpu_embedder()
            start = min(profile.embedding_count, len(profile.reference_images))
            for relative_path in profile.reference_images[start:]:
                image_path = registry.root_dir / pet_id / relative_path
                jpeg = image_path.read_bytes()
                registry.add_embedding(
                    pet_id,
                    embedder.embed(jpeg),
                    color_vector=extract_hsv_histogram(jpeg),
                    model_version=embedder.model_version,
                )
            refreshed = registry.get_pet(pet_id)
            count = refreshed.embedding_count if refreshed is not None else 0
            set_pet_job(
                pet_id,
                status="ready" if count else "unavailable",
                message_zh_tw=("身分特徵已建立。" if count else "沒有可用的身分特徵。"),
            )
        except Exception as exc:  # noqa: BLE001 - background failure must not stop Host
            set_pet_job(
                pet_id,
                status="unavailable",
                message_zh_tw=f"目前無法建立身分特徵（{type(exc).__name__}）；照片仍已保存，可稍後重試。",
            )

    def queue_pet_embeddings(pet_id: str, background_tasks: Any) -> dict[str, Any]:
        with app.state.pet_embedding_jobs_lock:
            current = pet_job(pet_id)
            if current and current.get("status") in {"queued", "running"}:
                return current
            queued = set_pet_job(
                pet_id,
                status="queued",
                message_zh_tw="已排入身分特徵建立工作；不會阻塞影音監控。",
            )
            background_tasks.add_task(build_pet_embeddings, pet_id)
            return queued

    async def read_reference_images(images: list[Any]) -> tuple[list[bytes], list[str]]:
        """Validate uploaded JPGs once for both enrollment and later additions."""

        if len(images) == 0 or len(images) > 32:
            raise HTTPException(status_code=422, detail="請上傳 1 至 32 張 JPG 參考照片。")
        try:
            from meowkeeper.core.identity.quality_filter import ReferenceQualityFilter

            quality = ReferenceQualityFilter()
            accepted: list[bytes] = []
            rejected: list[str] = []
            for index, image in enumerate(images, start=1):
                data = await image.read()
                if len(data) > 8 * 1024 * 1024:
                    rejected.append(f"第 {index} 張：檔案超過 8 MB。")
                    continue
                if not data.startswith(b"\xff\xd8\xff"):
                    rejected.append(f"第 {index} 張：只接受可解碼的 JPG。")
                    continue
                try:
                    result = quality.assess(data)
                except RuntimeError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                if not result.accepted or result.duplicate:
                    rejected.append(f"第 {index} 張：{result.reason}")
                    continue
                accepted.append(data)
            return accepted, rejected
        except HTTPException:
            raise
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"參考照片讀取失敗：{type(exc).__name__}。") from exc



    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return core.health()

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return core.health()

    @app.get("/api/v1/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        record = session_from_request(request)
        return {"configured": sessions.configured, "authenticated": record is not None, "username": record.username if record else None}

    @app.post("/api/v1/auth/login")
    def auth_login(request: Request, body: Any = Body(default=None)) -> Any:  # noqa: B008
        payload = json_body(body)
        try:
            record = sessions.login(str(payload.get("username", "")), str(payload.get("password", "")))
        except SessionAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response = JSONResponse(content={"status": "ok", "username": record.username, "expires_at": record.expires_at})
        secure_cookie = request.url.scheme.lower() == "https"
        response.set_cookie("meowkeeper_session", record.session_id, httponly=True, samesite="strict", secure=secure_cookie, max_age=sessions.timeout_seconds, path="/")
        response.set_cookie("meowkeeper_csrf", record.csrf_token, httponly=False, samesite="strict", secure=secure_cookie, max_age=sessions.timeout_seconds, path="/")
        return response

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request) -> Any:
        if sessions.configured:
            return RedirectResponse("/login", status_code=307)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="首次設定只允許 localhost。")
        setup_token = sessions.begin_setup()
        response = app.state.templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={"page_title": "首次設定", "setup_token": setup_token},
        )
        secure_cookie = request.url.scheme.lower() == "https"
        response.set_cookie("meowkeeper_setup_csrf", setup_token, httponly=False, samesite="strict", secure=secure_cookie, max_age=900, path="/")
        return response

    @app.post("/api/v1/auth/setup")
    def auth_setup(request: Request, body: Any = Body(default=None)) -> Any:  # noqa: B008
        if sessions.configured:
            raise HTTPException(status_code=409, detail="管理者已存在，不能再次初始化。")
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="首次設定只允許 localhost。")
        payload = json_body(body)
        csrf = request.headers.get("x-meowkeeper-csrf") or str(payload.get("setup_token", ""))
        cookie_csrf = request.cookies.get("meowkeeper_setup_csrf")
        if cookie_csrf and csrf != cookie_csrf:
            raise HTTPException(status_code=403, detail="首次設定的 CSRF 驗證失敗。")
        try:
            sessions.complete_setup(str(payload.get("username", "")), str(payload.get("password", "")), csrf)
        except SessionAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "message_zh_tw": "管理者已建立，請前往登入頁。"}

    @app.post("/api/v1/auth/logout")
    def auth_logout(request: Request) -> Any:
        session_id = request.cookies.get("meowkeeper_session")
        sessions.logout(session_id)
        tracker = getattr(app.state, "live_viewer_tracker", None)
        if session_id and tracker is not None:
            tracker.end_session(session_id)
        response = JSONResponse(content={"status": "logged_out"})
        response.delete_cookie("meowkeeper_session")
        response.delete_cookie("meowkeeper_csrf")
        return response

    def get_gallery_status() -> dict[str, Any]:
        now = time.monotonic()
        cached = getattr(app.state, "_cached_gallery_status", None)
        cached_at = getattr(app.state, "_cached_gallery_status_at", 0.0)
        if cached is not None and now - cached_at < 15.0:
            return dict(cached)
        try:
            reg = pet_registry()
            pets = reg.list_pets()
            pet_count = len(pets)
            feedback_total = 0
            ref_total = 0
            pet_summaries = {}
            for p in pets:
                summary = reg.gallery_summary(p.pet_id)
                fb = summary.get("feedback_feature_count", 0)
                feedback_total += fb
                ref_total += len(p.reference_images)
                pet_summaries[p.pet_id] = {
                    "name": p.display_name,
                    "feedback_count": fb,
                    "reference_count": len(p.reference_images),
                    "calibrated": fb > 0,
                }
            res = {
                "pet_count": pet_count,
                "feedback_feature_count": feedback_total,
                "reference_image_count": ref_total,
                "calibrated": feedback_total > 0,
                "pets": pet_summaries,
            }
        except Exception:
            res = {
                "pet_count": 0,
                "feedback_feature_count": 0,
                "reference_image_count": 0,
                "calibrated": False,
                "pets": {},
            }
        app.state._cached_gallery_status = res
        app.state._cached_gallery_status_at = now
        return res

    @app.get("/api/v1/status")
    def status_view(request: Request) -> dict[str, Any]:
        require_auth(request)
        snapshot = core.snapshot()
        metrics = snapshot.setdefault("metrics", {})
        if resource_recorder is not None:
            metrics["telemetry"] = resource_recorder.live_snapshot()
        metrics["identity_gallery"] = get_gallery_status()
        return snapshot

    @app.post("/api/v1/monitor/start")
    def monitor_start(request: Request) -> dict[str, Any]:
        require_mutation(request)
        try:
            return core.start()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/inference/retry")
    def inference_retry(request: Request) -> dict[str, Any]:
        require_mutation(request)
        return core.retry_inference()

    @app.post("/api/v1/monitor/stop")
    def monitor_stop(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        try:
            if payload.get("confirm") is True:
                integration = getattr(app.state, "integrations", None)
                provider = getattr(integration, "relay_provider", None)
                if provider is not None and provider.running:
                    provider.stop(revoke=False)
            return core.stop(confirm=bool(payload.get("confirm")))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/monitor/restart")
    def monitor_restart(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        try:
            return core.restart(confirm=bool(payload.get("confirm")))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/host/shutdown")
    def host_shutdown(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        if json_body(body).get("confirm") is not True:
            raise HTTPException(status_code=400, detail="完整關閉需要明確確認。")
        callback = getattr(app.state, "host_shutdown", None)
        if not callable(callback):
            raise HTTPException(status_code=503, detail="Host 關閉服務尚未接線。")
        return dict(callback())

    @app.post("/api/v1/host/restart")
    def host_restart(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """Request a graceful re-exec of the current repository Host process.

        The callback is installed only by ``tools/run_host.py``.  Keeping the
        operation behind the authenticated mutation boundary prevents a page
        or unauthenticated client from restarting the Windows service.
        """

        require_mutation(request)
        payload = json_body(body)
        if not bool(payload.get("confirm")):
            raise HTTPException(status_code=428, detail="重新啟動 Host 需要明確確認。")
        callback = getattr(app.state, "host_restart", None)
        if not callable(callback):
            return {"status": "not_configured", "message_zh_tw": "目前 Host 未提供重新啟動控制。"}
        try:
            result = callback()
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"Host 重新啟動未排程：{type(exc).__name__}。") from exc
        return dict(result) if isinstance(result, dict) else {"status": "restarting"}

    @app.get("/api/v1/sources")
    def sources(request: Request) -> dict[str, Any]:
        require_auth(request)
        source = core.snapshot().get("metrics", {}).get("source", {})
        return {"items": [source], "count": 1}

    @app.get("/api/v1/source/config")
    def source_config_view(request: Request) -> dict[str, Any]:
        require_auth(request)
        service = getattr(app.state, "source_config", None)
        if service is None or not callable(getattr(service, "snapshot", None)):
            return {"status": "not_configured", "source": {"source_type": "mock", "configured": False}}
        try:
            return {"status": "ok", "source": dict(service.snapshot())}
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"來源設定無法讀取：{type(exc).__name__}。") from exc

    @app.put("/api/v1/source/config")
    def source_config_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改影音來源需要再次確認。")
        service = getattr(app.state, "source_config", None)
        if service is None or not callable(getattr(service, "validate_update", None)):
            raise HTTPException(status_code=503, detail="來源設定服務尚未接入。")
        try:
            config = service.validate_update(payload)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        callback = core._callbacks.get("source_config")
        if callback is not None:
            result = dict(callback(config.url if config.configured else None))
            if result.get("status") == "rejected":
                raise HTTPException(status_code=409, detail=result.get("message_zh_tw", "核心拒絕來源設定。"))
        try:
            saved = service.save(config)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"來源設定保存失敗：{type(exc).__name__}。") from exc
        return {
            "status": "saved",
            "source": saved,
            "restart_required": True,
            "message_zh_tw": "來源設定已保存；請按開始監控，或重新啟動 Host 以套用。",
        }

    @app.post("/api/v1/sources/test")
    def source_test(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        callback = core._callbacks.get("source_test")  # façade owns callback boundary
        if callback is None:
            return {"status": "not_checked", "message_zh_tw": "尚未接入來源測試 callback。"}
        return dict(callback(json_body(body)))

    @app.get("/api/v1/settings/behavior-zones")
    def behavior_zones_get(request: Request) -> dict[str, Any]:
        require_auth(request)
        callback = core._callbacks.get("behavior_zones")
        if callback is None:
            raise HTTPException(status_code=503, detail="行為區域尚未接入核心。")
        try:
            return dict(callback())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="區域資料庫忙碌，請稍後再試。") from exc

    @app.put("/api/v1/settings/behavior-zones")
    def behavior_zones_put(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and payload.get("reauth") is not True:
            raise HTTPException(status_code=428, detail="保存區域需要再次確認。")
        callback = core._callbacks.get("behavior_zones")
        if callback is None:
            raise HTTPException(status_code=503, detail="行為區域尚未接入核心。")
        try:
            return dict(callback(payload))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="區域保存失敗，原設定未變更；請稍後再試。") from exc

    @app.get("/api/v1/telemetry/cpu-temperature")
    def cpu_temperature_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        if resource_recorder is None:
            raise HTTPException(status_code=503, detail="CPU溫度背景採樣尚未接入。")
        return resource_recorder.cpu_monitor.snapshot()

    @app.get("/api/v1/telemetry/cpu-temperature/setup")
    def cpu_temperature_setup_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        return {**cpu_setup.status(), "local_request": is_local_request(request)}

    @app.post("/api/v1/telemetry/cpu-temperature/setup")
    def cpu_temperature_setup_launch(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="請在監控主機本機開啟儀表板，才能啟動驅動授權視窗；遠端仍可閱讀指引。")
        if json_body(body).get("confirm") is not True:
            raise HTTPException(status_code=428, detail="請先確認此操作會開啟驅動安裝與管理員授權視窗。")
        try:
            return cpu_setup.launch()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/analytics/resources")
    def resource_statistics(request: Request, hours: int = 24) -> dict[str, Any]:
        require_auth(request)
        if resource_recorder is None:
            raise HTTPException(status_code=503, detail=resource_error or "資源歷史尚未接入。")
        try:
            data = resource_recorder.history.query(hours)
            data["warning"] = resource_recorder.last_error
            data["cpu_temperature"] = resource_recorder.cpu_monitor.snapshot()
            data["cpu_temperature_hour"] = resource_recorder.history.hour_summary()["statistics"]["cpu_temperature_c"]
            return data
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="資源歷史暫時無法讀取，請稍後再試。") from exc

    @app.get("/api/v1/analytics/dashboard")
    def dashboard_activity(request: Request) -> dict[str, Any]:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        try:
            return core.store.dashboard_activity(now=datetime.now(timezone.utc))
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="事件統計暫時無法讀取。") from exc

    @app.get("/api/v1/analytics/semantics")
    def semantic_statistics(request: Request, limit: int = 100, hours: int | None = None,
                            start: str | None = None, end: str | None = None, provider: str | None = None,
                            model: str | None = None, validity: str | None = None, input_mode: str | None = None) -> dict[str, Any]:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        from meowkeeper.core.analytics.semantic_performance import summarize_semantic_performance
        try:
            return summarize_semantic_performance(core.store,limit=limit,hours=hours,start=start,end=end,
                                                  provider=provider,model=model,validity_filter=validity,input_mode=input_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="進階分析統計暫時無法讀取。") from exc

    @app.get("/api/v1/system/status")
    def full_system_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        if status_service is None:
            raise HTTPException(status_code=503, detail="完整系統狀態尚未接入；請使用更新後的 Host。")
        snapshot = status_service.snapshot()
        if resource_recorder is not None:
            snapshot["latest_telemetry"] = resource_recorder.live_snapshot()
        return snapshot

    def review_service() -> Any:
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件回報資料庫尚未接入。")
        from meowkeeper.core.events.web_review import WebEventReview
        return WebEventReview(core.store, pet_registry())

    def web_reviewer_ref(request: Request) -> str:
        record = session_from_request(request)
        identity = record.username if record is not None else "api-admin"
        return hashlib.sha256(("web-admin:" + identity).encode("utf-8")).hexdigest()

    @app.get("/api/v1/events/{event_id}/semantic")
    def event_semantic_view(request: Request, event_id: str) -> dict[str, Any]:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件 AI 候選資料庫尚未接入。")
        if core.store.get_media_event(event_id) is None:
            raise HTTPException(status_code=404, detail="找不到此事件。")
        return core.store.event_semantics(event_id)

    @app.get("/api/v1/events/{event_id}/review")
    def web_review_view(request: Request, event_id: str) -> dict[str, Any]:
        require_auth(request)
        try:
            return review_service().view(event_id, web_reviewer_ref(request))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="此事件目前無法回報，請確認為真實模型貓咪事件。") from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="回報歷史暫時無法讀取。") from exc

    def sync_feedback_learning(
        *,
        max_new_samples: int = 16,
        upgrade_pending_consent: bool = False,
    ) -> dict[str, Any]:
        if core.store is None:
            return {"new_features": 0, "published_samples": 0}
        try:
            from meowkeeper.core.identity.dinov2_embedder import shared_cpu_embedder
            from meowkeeper.core.identity.feedback_learning import FeedbackIdentityLearner

            if app.state.pet_comparison_embedder is None:
                app.state.pet_comparison_embedder = shared_cpu_embedder()
            learner = FeedbackIdentityLearner(
                core.store,
                pet_registry(),
                app.state.pet_comparison_embedder,
            )
            res = learner.refresh(
                max_new_samples=max_new_samples,
                upgrade_pending_consent=upgrade_pending_consent,
            )
            app.state._cached_gallery_status_at = 0.0
            return res
        except Exception:  # noqa: BLE001 - optional embedder may be absent in unit tests
            return {"new_features": 0, "published_samples": 0}

    @app.post("/api/v1/events/{event_id}/review")
    def web_review_submit(request: Request, event_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        from meowkeeper.core.events.web_review import ReviewConflict
        try:
            payload = json_body(body)
            result = review_service().submit(event_id, web_reviewer_ref(request), payload)
            if payload.get("track_ref"):
                result["learning"] = sync_feedback_learning(max_new_samples=16)
            return result
        except ReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail="回報欄位不正確；請選擇已登錄寵物與有效動作。") from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="回報暫時無法保存；歷史未覆寫，可重試。") from exc

    @app.post("/api/v1/events/{event_id}/reanalyze")
    def event_reanalyze(request: Request, event_id: str, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        if not isinstance(body, dict) or set(body) != {"request_id", "confirm_saved_crops"} or body["confirm_saved_crops"] is not True:
            raise HTTPException(status_code=428, detail="請確認重用這筆事件已保存的裁切時序圖送給目前已同意的服務。")
        callback = core._callbacks.get("reanalyze_event")
        if callback is None:
            raise HTTPException(status_code=503, detail="此執行環境尚未接入手動分析；原影片與人工回報仍可使用。")
        try:
            review_service()._event(event_id)
            return callback(event_id,str(body["request_id"]))
        except (ValueError, TypeError, KeyError, sqlite3.Error):
            raise HTTPException(status_code=422, detail="未重新外送：請確認已啟用分析並同意目前服務、距上次30秒，且仍保留有效裁切時序證據。舊證據不存在時不會拿現在的畫面代替。") from None

    @app.get("/api/v1/vision/runtime")
    def vision_runtime(request: Request):
        require_auth(request)
        callback = core._callbacks.get("semantic_runtime")
        if callback:
            return callback()
        if core.store is None:
            raise HTTPException(status_code=503,detail="分析帳本尚未接入。")
        from meowkeeper.storage.semantic_jobs import SemanticJobJournal
        return {**SemanticJobJournal(core.store).runtime(),"worker_active":False,"pressure_reason":None}

    @app.post("/api/v1/events/{event_id}/analysis/{job_id}/cancel")
    def cancel_analysis(request: Request, event_id: str, job_id: str):
        require_mutation(request)
        try:
            review_service()._event(event_id)
            callback = core._callbacks.get("cancel_semantic_job")
            if callback:
                return callback(event_id,job_id)
            from meowkeeper.storage.semantic_jobs import SemanticJobJournal
            active = SemanticJobJournal(core.store).request_cancel(event_id,job_id)
            return {"status":"cancel_requested" if active else "already_finished"}
        except (ValueError,TypeError,KeyError,sqlite3.Error):
            raise HTTPException(status_code=422,detail="取消未完成；請核對此事件的分析工作後重新載入。") from None

    @app.post("/api/v1/events/{event_id}/validity")
    def event_validity_review(request: Request, event_id: str, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        from meowkeeper.core.events.web_review import ReviewConflict
        try:
            return review_service().review_validity(event_id,web_reviewer_ref(request),json_body(body))
        except ReviewConflict as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc
        except (ValueError,TypeError,KeyError,sqlite3.Error):
            raise HTTPException(status_code=422,detail="有效性覆核未保存；請重新載入並選擇有效、無效或看不清。") from None

    @app.post("/api/v1/events/{event_id}/semantic/review")
    def semantic_candidate_review(request: Request, event_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        from meowkeeper.core.events.web_review import ReviewConflict
        try:
            return review_service().review_candidate(event_id, web_reviewer_ref(request), json_body(body))
        except ReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail="候選覆核無效，請重新載入並對照影片。") from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="覆核暫時無法保存；原始候選與歷史仍保留。") from exc

    def scene_context():
        callback = core._callbacks.get("behavior_zones")
        if callback is None or core.store is None:
            raise HTTPException(status_code=503, detail="目前尚未接入來源視角登錄服務。")
        from meowkeeper.core.behavior.scene_views import SceneViewRegistry
        return SceneViewRegistry(core.store), callback()["source_ref"]

    @app.get("/api/v1/scene-registry")
    def scene_registry_read(request: Request):
        require_auth(request)
        try:
            scenes, source = scene_context()
            return {"source_ref":source, "alignment":scenes.state(source), "views":scenes.list_views(source), "objects":scenes.objects(source)}
        except (ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail="請先設定來源，才能讀取此來源的物件與視角。") from exc

    @app.post("/api/v1/scene-registry/views")
    def scene_registry_view_save(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        try:
            scenes, source = scene_context()
            if set(payload) != {"source_ref", "name", "expected_epoch", "confirm_current_frame"} or payload["source_ref"] != source or payload["confirm_current_frame"] is not True:
                raise ValueError("請明確確認目前畫面與來源。")
            path = core.request_snapshot(high_quality=False)
            if path is None or not path.is_file() or datetime.now(timezone.utc).timestamp()-path.stat().st_mtime > 5 or path.stat().st_size > 4*1024*1024:
                raise ValueError("尚無新鮮共享影格，請先啟動監控。")
            view_id = scenes.register(source, payload["name"], path.read_bytes(), payload["expected_epoch"])
            return {"status":"saved", "view_id":view_id, "message_zh_tw":"已保存目前參考視角；背景無法對位或鏡頭轉動時，區域推論會停用。這不是角度讀回。"}
        except (ValueError, OSError, TypeError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail="無法校正：請確認畫面新鮮穩定、背景有足夠特徵，並重新載入來源版本。") from exc

    @app.post("/api/v1/scene-registry/views/upload")
    async def scene_registry_reference_upload(request: Request, name: str = Form(...), expected_epoch: int = Form(...),
                                               image: UploadFile = File(...)):  # noqa: B008
        require_mutation(request)
        try:
            scenes, source = scene_context()
            content = await image.read(4*1024*1024+1)
            if len(content) > 4*1024*1024:
                raise ValueError("參考照片超過4MB。")
            view_id = scenes.register(source,name,content,expected_epoch,verified_current=False)
            return {"status":"saved_unverified","view_id":view_id,
                    "message_zh_tw":"參考圖已存於本機，請直接拖曳框選物件。此圖尚未與目前攝影機對位，不會自動當作現在的位置。"}
        except (ValueError,OSError,TypeError,sqlite3.Error) as exc:
            raise HTTPException(status_code=422,detail="參考圖未保存；請使用4MB內的單張JPG、PNG或WebP，確認名稱並重新載入來源。") from exc
        finally:
            await image.close()

    def scene_position_adapter():
        integration=integration_or_not_configured()
        adapter=getattr(integration,"privacy_adapter",None)
        if adapter is None:
            raise ValueError("尚未接入位置讀回服務。")
        device=adapter.device()
        if not isinstance(device,str) or not device:
            raise ValueError("尚無活動攝影機。")
        return adapter,device,hashlib.sha256(device.encode()).hexdigest()

    def verified_scene_pose(adapter,device,not_before=None):
        from meowkeeper.core.notifications.position_presets import PositionPresetService
        reading=adapter.read(device)
        if reading.get("position_readback_verified") is not True:
            raise ValueError("未取得本次實際位置讀回。")
        pose=PositionPresetService._position(reading)
        stamp=datetime.fromisoformat(str(reading["state"]["position"].get("updated_at")).replace("Z","+00:00"))
        if stamp.tzinfo is None or not -2<=(datetime.now(timezone.utc)-stamp).total_seconds()<=10:
            raise ValueError("位置讀回已過期。")
        if not_before is not None and stamp<not_before:
            raise ValueError("不是這次移動後的位置讀回。")
        return pose

    def current_scene_frame(not_before=None):
        path=core.request_snapshot(high_quality=False)
        if path is None or not path.is_file() or not 0<=datetime.now(timezone.utc).timestamp()-path.stat().st_mtime<=5 or path.stat().st_size>4*1024*1024:
            raise ValueError("尚無新鮮共享影格。")
        if not_before is not None and path.stat().st_mtime<not_before.timestamp():
            raise ValueError("尚未取得移動後的共享影格，未以舊圖判定到位。")
        return path.read_bytes()

    @app.post("/api/v1/scene-registry/views/{view_id}/position")
    def bind_scene_position(request: Request, view_id: str, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        try:
            payload=json_body(body)
            if set(payload)!={"expected_epoch","preset_label"} or type(payload["expected_epoch"]) is not int:
                raise ValueError("欄位無效。")
            scenes,source=scene_context();adapter,device,reference=scene_position_adapter()
            pose=verified_scene_pose(adapter,device)
            if adapter.device()!=device or scene_context()[1]!=source:
                raise ValueError("來源已切換。")
            scenes.align(source,view_id,current_scene_frame(),payload["expected_epoch"])
            scenes.bind_position(source,view_id,pose,reference,payload["expected_epoch"],payload["preset_label"])
            return {"status":"bound","message_zh_tw":"真實位置讀回與背景對位已通過，已綁定此參考圖。"}
        except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
            raise HTTPException(status_code=422,detail="位置未綁定：需活動攝影機、本次有效座標及一致背景；未移動鏡頭、未猜測位置。") from None

    @app.post("/api/v1/scene-registry/views/{view_id}/restore-position")
    def restore_scene_position(request: Request, view_id: str):
        require_mutation(request)
        try:
            scenes,source=scene_context();adapter,device,reference=scene_position_adapter()
            target=scenes.bound_position(source,view_id,reference)
            scenes.invalidate(source,"movement")
            requested_at=datetime.now(timezone.utc)
            response=adapter.move(device,**target)
            if response.get("success") is not True or adapter.device()!=device or scene_context()[1]!=source:
                raise ValueError("移動未確認。")
            import time
            deadline=time.monotonic()+8
            for _attempt in range(4):
                if adapter.device()!=device or scene_context()[1]!=source:
                    raise ValueError("核對期間來源已切換。")
                try:
                    epoch=scenes.state(source)["epoch"]
                    actual=verified_scene_pose(adapter,device,requested_at)
                    if actual!=target:
                        raise ValueError("馬達尚未到位。")
                    scenes.align(source,view_id,current_scene_frame(requested_at),epoch)
                    return {"status":"aligned","message_zh_tw":"位置讀回與背景核對一致；已恢復此方向的物件位置判斷。"}
                except (ValueError,OSError):
                    if time.monotonic()>=deadline:
                        break
                    time.sleep(.25)  # 僅Web核對worker；不持有影音或STOP的鎖。
            raise ValueError("沒有取得移動後的一致位置與畫面。")
        except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
            raise HTTPException(status_code=422,detail="未確認到位；區域推論保持停用。請檢查即時畫面，勿將指令送出當成定位成功。") from None

    @app.get("/api/v1/scene-registry/objects/{object_id}/crop")
    def scene_object_crop(request: Request, object_id: str):
        require_auth(request)
        try:
            scenes,source=scene_context()
            return Response(content=scenes.object_crop(source,object_id),media_type="image/jpeg",headers={"Cache-Control":"no-store"})
        except (ValueError,TypeError,OSError,sqlite3.Error):
            raise HTTPException(status_code=404,detail="找不到此來源的物件參考裁切。") from None

    @app.get("/api/v1/scene-registry/reference-collage")
    def scene_reference_collage(request: Request):
        require_auth(request)
        try:
            scenes, source = scene_context()
            result = scenes.reference_context(source)
            if result is None:
                raise HTTPException(status_code=404, detail="目前來源尚無已啟用的物件標記，無法製作參考拼貼。")
            image, labels = result
            return Response(content=image, media_type="image/jpeg", headers={
                "Cache-Control": "no-store", "X-Scene-Reference-Count": str(len(labels)),
            })
        except HTTPException:
            raise
        except (ValueError,TypeError,OSError,sqlite3.Error):
            raise HTTPException(status_code=422,detail="參考拼貼暫時無法產生；原始參考照片仍保存在本機。") from None

    @app.post("/api/v1/scene-registry/objects")
    def scene_registry_object_save(request: Request, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        try:
            scenes, source = scene_context()
            return scenes.save_object(source, json_body(body))
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail="物件未保存；請確認視角、版本、名稱與畫面內邊界。") from exc

    @app.post("/api/v1/scene-registry/views/{view_id}/align")
    def scene_registry_align(request: Request, view_id: str, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        try:
            payload = json_body(body)
            scenes, source = scene_context()
            if set(payload) != {"expected_epoch"} or type(payload["expected_epoch"]) is not int:
                raise ValueError("視角版本不正確。")
            path = core.request_snapshot(high_quality=False)
            if path is None or not path.is_file() or datetime.now(timezone.utc).timestamp()-path.stat().st_mtime > 5 or path.stat().st_size > 4*1024*1024:
                raise ValueError("目前沒有新鮮共享畫面。")
            scenes.align(source, view_id, path.read_bytes(), payload["expected_epoch"])
            return {"status":"aligned", "message_zh_tw":"已由背景幾何對位核對目前視角；未送出PTZ移動指令。"}
        except (ValueError, OSError, TypeError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail="畫面與參考視角無法對位，或鏡頭已移動；區域推論維持停用。") from exc

    @app.get("/api/v1/scene-registry/views/{view_id}/reference")
    def scene_registry_reference(request: Request, view_id: str):
        require_auth(request)
        scenes, source = scene_context()
        with scenes.store._connection_lock:
            row = scenes.store.connection.execute("SELECT reference_jpeg FROM scene_views WHERE source_ref=? AND view_id=?", (source, view_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到此來源的參考視角。")
        return Response(bytes(row[0]), media_type="image/jpeg", headers={"Cache-Control":"no-store"})

    @app.get("/api/v1/analytics/behavior")
    def behavior_statistics(request: Request, start: str, end: str) -> dict[str, Any]:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="行為資料庫尚未接入。")
        from meowkeeper.core.analytics.behavior_summary import summarize_behavior
        try:
            return summarize_behavior(core.store, start, end)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def identity_view(media_event_id: str | None = None) -> dict:
        from urllib.parse import quote

        rows = core.store.identity_evidence(media_event_id) if core.store else []
        registry = pet_registry()
        for row in rows:
            row.pop("unknown_probability", None)  # 舊欄位不是校準機率，不提供給介面。
            pred_pet = row.get("predicted_pet_id")
            if pred_pet and registry:
                try:
                    summary = registry.gallery_summary(pred_pet)
                    if summary.get("feedback_feature_count", 0) > 0:
                        row["calibration_verified"] = True
                except Exception:
                    pass
            if not row.get("calibration_verified"):
                row["unknown_reason"] = row.get("unknown_reason") or "calibration_required"
                row["decision"] = "unknown" 
            for candidate in row.get("candidates", []):
                for reference in candidate.get("reference_matches", []):
                    try:
                        registry.reference_image_path(candidate["pet_id"], reference["reference_image_id"])
                        reference["thumbnail_url"] = (
                            f"/api/v1/pets/{quote(candidate['pet_id'], safe='')}/images/"
                            f"{quote(reference['reference_image_id'], safe='')}")
                    except (OSError, ValueError, KeyError):
                        reference["thumbnail_url"] = None
            row["crop_url"] = f"/api/v1/identity/tracks/{quote(row['behavior_event_id'], safe='')}/crop"
            if core.store is not None:
                with core.store._connection_lock:
                    annotations = core.store.connection.execute(
                        "SELECT reviewer_ref,dimension,label,learning_consent FROM behavior_event_annotations WHERE event_id=?",
                        (row["behavior_event_id"],),
                    ).fetchall()
                    learning = core.store.connection.execute(
                        "SELECT learning_status FROM identity_feedback_evidence WHERE event_id=?", (row["behavior_event_id"],),
                    ).fetchone()
                grouped = {}
                for annotation in annotations:
                    review = grouped.setdefault(annotation["reviewer_ref"], {})
                    review[annotation["dimension"]] = annotation["label"]
                    if annotation["dimension"] == "identity":
                        review["learning_consent"] = bool(annotation["learning_consent"])
                row["human_reviews"] = list(grouped.values())
                row["crop_exists"] = learning is not None
                row["learning_status"] = learning[0] if learning else "crop_not_saved_or_expired"
        return {"experimental": True, "tracks": rows, "event_id": media_event_id,
                "pets": [{"pet_id": pet.pet_id, "name": pet.display_name} for pet in registry.list_pets(enabled_only=True)],
                "state": "available" if rows else "not_analyzed",
                "message_zh_tw": "AI 候選、人工確認與特徵更新分開保存；相似度不是準確率。"}

    @app.get("/api/v1/updates")
    def updates_status(request: Request) -> dict:
        require_auth(request)
        return updater.snapshot()

    @app.post("/api/v1/updates/{action}")
    def updates_action(request: Request, action: str, background_tasks: BackgroundTasks) -> dict:
        require_mutation(request)
        if action not in {"check", "prepare"}:
            raise HTTPException(status_code=400, detail="更新操作不合法。")
        background_tasks.add_task(updater.check if action == "check" else updater.prepare)
        return {"status": "queued", "message_zh_tw": "更新操作已排入；不會停止監控或自動安裝。"}

    @app.get("/api/v1/identity/summary")
    def identity_summary(request: Request) -> dict:
        require_auth(request)
        view = identity_view()
        view["tracks"] = view["tracks"][:1]
        return view

    @app.get("/api/v1/events/{event_id}/identity")
    def event_identity(request: Request, event_id: str) -> dict:
        require_auth(request)
        if core.get_event(event_id) is None:
            raise HTTPException(status_code=404, detail="找不到已授權事件。")
        return identity_view(event_id)

    @app.get("/api/v1/identity/tracks/{event_id}/crop")
    def identity_crop(request: Request, event_id: str) -> Any:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=404, detail="身分裁切未保存或已清理。")
        with core.store._connection_lock:
            row = core.store.connection.execute(
                "SELECT crop_jpeg FROM identity_feedback_evidence WHERE event_id=?", (event_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="身分裁切未保存或已清理；推論摘要仍保留。")
        return Response(bytes(row[0]), media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-store"})

    @app.post("/api/v1/pets/compare-photo")
    async def pets_compare_photo(request: Request, image: UploadFile = File(...)) -> Any:  # noqa: B008
        require_mutation(request)
        content = await image.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024 or not content.startswith(b"\xff\xd8\xff"):
            raise HTTPException(status_code=422, detail="請上傳不超過 2 MB 的單隻貓 JPG 裁切。")
        from meowkeeper.core.identity.quality_filter import ReferenceQualityFilter

        try:
            quality = ReferenceQualityFilter().assess(content)
            if not quality.accepted:
                raise HTTPException(status_code=422, detail=quality.reason)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail="照片無法解碼或品質不足。") from exc
        if not app.state.pet_comparison_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="另一張照片正在比對，請稍後再試。")

        def compare():
            from meowkeeper.core.identity.dinov2_embedder import shared_cpu_embedder
            from meowkeeper.core.identity.photo_comparison import compare_photo
            from meowkeeper.core.identity.base import IdentityThresholds

            if app.state.pet_comparison_embedder is None:
                app.state.pet_comparison_embedder = shared_cpu_embedder()
            th = IdentityThresholds(
                accepted_threshold=float(core.settings().get("identity_accepted_threshold", 0.62)),
                minimum_margin=float(core.settings().get("identity_minimum_margin", 0.05)),
            )
            return compare_photo(pet_registry(), app.state.pet_comparison_embedder, content, thresholds=th)

        try:
            from starlette.concurrency import run_in_threadpool

            return await run_in_threadpool(compare)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            raise HTTPException(status_code=503, detail="身分模型或特徵尚未可用；請先建立寵物特徵。") from exc
        finally:
            app.state.pet_comparison_lock.release()

    @app.get("/api/v1/analytics/cat-feedback")
    def cat_feedback_statistics(request: Request) -> dict[str, Any]:
        require_auth(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        try:
            return core.store.cat_feedback_summary()
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail=f"貓咪回饋統計暫時無法讀取：{type(exc).__name__}。") from exc

    @app.get("/api/v1/pets")
    def pets_list(request: Request) -> dict[str, Any]:
        require_auth(request)
        registry = pet_registry()
        return {
            "items": [pet_payload(profile, job=pet_job(profile.pet_id)) for profile in registry.list_pets()],
            "root": "data/pets",
            "message_zh_tw": "寵物登錄資料只保存在本機；身分辨識仍屬實驗性。",
        }

    @app.get("/api/v1/pets/track-candidates")
    def pets_track_candidates(
        request: Request,
        limit: int = 24,
        filter_mode: str = "all",
    ) -> dict[str, Any]:
        """列出近期已通過清晰度與單貓不重疊篩選的攝影機軌跡裁切，供一鍵加入寵物特徵庫。"""
        require_auth(request)
        if core.store is None:
            return {"items": [], "pets": [], "total_eligible": 0, "total_evidence": 0}
        from io import BytesIO
        from urllib.parse import quote

        from PIL import Image

        bounded_limit = max(1, min(120, int(limit)))
        registry = pet_registry()
        pets = [
            {"pet_id": p.pet_id, "display_name": p.display_name}
            for p in registry.list_pets(enabled_only=True)
        ]
        pet_names = {p["pet_id"]: p["display_name"] for p in pets}
        target_pet_id = filter_mode.split(":", 1)[1] if filter_mode.startswith("pet:") else None
        with core.store._connection_lock:
            total_evidence = int(
                core.store.connection.execute(
                    "SELECT COUNT(*) FROM identity_feedback_evidence WHERE learning_status NOT IN ('blurred','invalid_crop','checksum_failed')"
                ).fetchone()[0]
            )
            total_eligible = int(
                core.store.connection.execute(
                    "SELECT COUNT(*) FROM identity_feedback_evidence WHERE learning_status = 'eligible'"
                ).fetchone()[0]
            )
            if target_pet_id:
                rows = core.store.connection.execute(
                    """SELECT e.event_id, e.crop_jpeg, e.captured_at, e.created_at, e.learning_status,
                              e.feature_json IS NOT NULL AS has_feature,
                              b.track_id, b.behavior_state, b.motion_state
                       FROM identity_feedback_evidence e
                       JOIN behavior_event_annotations a ON a.event_id = e.event_id AND a.dimension = 'identity' AND a.label = ?
                       LEFT JOIN behavior_events b ON b.event_id = e.event_id
                       WHERE e.learning_status NOT IN ('blurred', 'invalid_crop', 'checksum_failed')
                       ORDER BY e.captured_at DESC
                       LIMIT 120""",
                    (target_pet_id,),
                ).fetchall()
            else:
                rows = core.store.connection.execute(
                    """SELECT e.event_id, e.crop_jpeg, e.captured_at, e.created_at, e.learning_status,
                              e.feature_json IS NOT NULL AS has_feature,
                              b.track_id, b.behavior_state, b.motion_state
                       FROM identity_feedback_evidence e
                       LEFT JOIN behavior_events b ON b.event_id = e.event_id
                       WHERE e.learning_status NOT IN ('blurred', 'invalid_crop', 'checksum_failed')
                       ORDER BY e.captured_at DESC
                       LIMIT 120"""
                ).fetchall()
            event_ids = [r["event_id"] for r in rows]
            annotations_by_event: dict[str, list[Any]] = {}
            ai_candidates_by_event: dict[str, list[dict[str, Any]]] = {}
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                for ann in core.store.connection.execute(
                    f"SELECT event_id, reviewer_ref, dimension, label, learning_consent FROM behavior_event_annotations WHERE event_id IN ({placeholders})",
                    event_ids,
                ).fetchall():
                    annotations_by_event.setdefault(ann["event_id"], []).append(ann)
                for inf in core.store.connection.execute(
                    f"SELECT event_id, note FROM identity_inference_history WHERE event_id IN ({placeholders}) AND note LIKE '%identity_evidence_v1%' ORDER BY id DESC",
                    event_ids,
                ).fetchall():
                    if inf["event_id"] in ai_candidates_by_event:
                        continue
                    try:
                        parsed = json.loads(inf["note"])
                        cands = parsed.get("candidates", []) if isinstance(parsed, dict) else []
                        ai_candidates_by_event[inf["event_id"]] = [
                            {
                                "pet_id": str(c.get("pet_id") or ""),
                                "name": str(c.get("name") or ""),
                                "score": round(float(c.get("pet_score") or 0.0), 3),
                            }
                            for c in cands[:2]
                            if isinstance(c, dict) and c.get("name")
                        ]
                    except (ValueError, TypeError, OverflowError):
                        ai_candidates_by_event.setdefault(inf["event_id"], [])

        items: list[dict[str, Any]] = []
        for row in rows:
            eid = row["event_id"]
            crop_bytes = bytes(row["crop_jpeg"] or b"")
            if not crop_bytes:
                continue
            try:
                with Image.open(BytesIO(crop_bytes)) as img:
                    width, height = img.size
            except (OSError, ValueError):
                logging.getLogger(__name__).debug("略過無法讀取的軌跡裁切。")
                continue
            if min(width, height) < 96:
                continue
            anns = annotations_by_event.get(eid, [])
            by_rev: dict[str, dict[str, Any]] = {}
            for a in anns:
                rev = by_rev.setdefault(a["reviewer_ref"], {})
                rev[a["dimension"]] = a["label"]
                if a["dimension"] == "identity":
                    rev["learning_consent"] = bool(a["learning_consent"])
            assigned_pet_id: str | None = None
            learning_consent = False
            if any(r.get("cat_presence") == "not_cat" for r in by_rev.values()):
                assigned_pet_id = "not_cat"
            else:
                identities = [
                    r.get("identity")
                    for r in by_rev.values()
                    if r.get("cat_presence") == "cat" and r.get("identity") not in {None, "unknown", ""}
                ]
                if len(set(identities)) == 1:
                    assigned_pet_id = identities[0]
                    learning_consent = any(
                        r.get("learning_consent") and r.get("identity") == assigned_pet_id
                        for r in by_rev.values()
                    )
            if target_pet_id and assigned_pet_id != target_pet_id:
                continue
            if filter_mode == "unlabeled" and assigned_pet_id is not None:
                continue
            if filter_mode == "enrolled" and (assigned_pet_id is None or assigned_pet_id == "not_cat"):
                continue
            items.append(
                {
                    "event_id": eid,
                    "track_id": row["track_id"],
                    "captured_at": row["captured_at"],
                    "width": width,
                    "height": height,
                    "learning_status": row["learning_status"],
                    "has_feature": bool(row["has_feature"]),
                    "assigned_pet_id": assigned_pet_id,
                    "assigned_pet_name": (
                        "非貓／已略過"
                        if assigned_pet_id == "not_cat"
                        else pet_names.get(assigned_pet_id or "", assigned_pet_id)
                    ),
                    "learning_consent": learning_consent,
                    "ai_candidates": ai_candidates_by_event.get(eid, []),
                    "crop_url": f"/api/v1/identity/tracks/{quote(eid, safe='')}/crop",
                }
            )
            if len(items) >= bounded_limit:
                break

        return {
            "items": items,
            "pets": pets,
            "total_eligible": total_eligible,
            "total_evidence": total_evidence,
        }

    @app.post("/api/v1/pets/track-candidates/{event_id}/assign")
    def pets_track_candidate_assign(
        request: Request,
        event_id: str,
        body: Any = Body(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        """從寵物檔案頁一鍵將攝影機軌跡裁切指派給指定貓咪，並立即同步寫入 feedback_embeddings.npz。"""
        require_mutation(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        payload = json_body(body)
        pet_id = str(payload.get("pet_id") or "").strip()
        registry = pet_registry()
        enabled_pets = {p.pet_id: p.display_name for p in registry.list_pets(enabled_only=True)}
        if pet_id not in enabled_pets and pet_id not in {"not_cat", "clear"}:
            raise HTTPException(status_code=422, detail="請選擇有效的已登錄寵物、非貓或清除標註。")

        reviewer = web_reviewer_ref(request)
        with core.store._connection_lock:
            evidence = core.store.connection.execute(
                "SELECT event_id, captured_at FROM identity_feedback_evidence WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if evidence is None:
            raise HTTPException(status_code=404, detail="找不到此軌跡截圖，可能已被清理。")

        def apply_assignment() -> None:
            # 確保 behavior_events 存在對應紀錄以維持外鍵與查詢一致性
            existing_be = core.store.connection.execute(
                "SELECT 1 FROM behavior_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing_be is None:
                captured = str(evidence["captured_at"] or datetime.now(timezone.utc).isoformat())
                core.store.connection.execute(
                    """INSERT OR IGNORE INTO behavior_events(
                           event_id, pet_id, track_id, behavior_state, motion_state,
                           event_tags, started_at, ended_at, duration_seconds,
                           raw_confidence, smoothed_confidence, inference_method,
                           model_version, rule_profile_version, source_id, camera_preset
                       ) VALUES (?, NULL, 'camera', 'resting', 'stationary', '[]', ?, ?, 0.0, 0.8, 0.8, 'rule', 'v02', 'v02', 'camera', NULL)""",
                    (event_id, captured, captured),
                )
            core.store.connection.execute(
                "DELETE FROM behavior_event_annotations WHERE event_id=? AND dimension IN ('cat_presence', 'identity')",
                (event_id,),
            )
            if pet_id == "clear":
                core.store.connection.execute(
                    "UPDATE identity_feedback_evidence SET learning_status='pending' WHERE event_id=?",
                    (event_id,),
                )
            elif pet_id == "not_cat":
                core.store.connection.execute(
                    """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                       VALUES (?, ?, 'cat_presence', 'not_cat', 0)""",
                    (event_id, reviewer),
                )
                core.store.connection.execute(
                    "UPDATE identity_feedback_evidence SET learning_status='unconfirmed_or_conflicting' WHERE event_id=?",
                    (event_id,),
                )
            else:
                core.store.connection.execute(
                    """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                       VALUES (?, ?, 'cat_presence', 'cat', 0)""",
                    (event_id, reviewer),
                )
                core.store.connection.execute(
                    """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                       VALUES (?, ?, 'identity', ?, 1)""",
                    (event_id, reviewer, pet_id),
                )
                core.store.connection.execute(
                    "UPDATE identity_feedback_evidence SET created_at=CURRENT_TIMESTAMP WHERE event_id=?",
                    (event_id,),
                )

        core.store._transaction(apply_assignment)
        learning = sync_feedback_learning(max_new_samples=16, upgrade_pending_consent=True)
        with core.store._connection_lock:
            updated_row = core.store.connection.execute(
                "SELECT learning_status, feature_json IS NOT NULL AS has_feature FROM identity_feedback_evidence WHERE event_id=?",
                (event_id,),
            ).fetchone()
        updated_status = updated_row["learning_status"] if updated_row else "unknown"
        has_feature = bool(updated_row["has_feature"]) if updated_row else False

        if pet_id == "clear":
            msg = "已清除此軌跡截圖的身分標註，並同步更新特徵庫。"
        elif pet_id == "not_cat":
            msg = "已標記為非貓／略過，不會納入任何寵物的特徵庫。"
        elif updated_status == "eligible":
            msg = f"已將此攝影機實拍裁切設為「{enabled_pets[pet_id]}」，並立即提取向量寫入回饋特徵庫！"
        elif updated_status == "blurred":
            msg = f"已標註為「{enabled_pets[pet_id]}」，但此裁切清晰度不足（微幅模糊），系統自動略過入庫以保護辨識品質。"
        else:
            msg = f"已標註為「{enabled_pets[pet_id]}」（狀態：{updated_status}）。"

        return {
            "status": "assigned",
            "event_id": event_id,
            "pet_id": None if pet_id == "clear" else pet_id,
            "pet_name": enabled_pets.get(pet_id),
            "learning_status": updated_status,
            "has_feature": has_feature,
            "learning": learning,
            "pets": [pet_payload(p, job=pet_job(p.pet_id)) for p in registry.list_pets()],
            "message_zh_tw": msg,
        }

    @app.delete("/api/v1/pets/track-candidates/{event_id}")
    def pets_track_candidate_delete(
        request: Request,
        event_id: str,
    ) -> dict[str, Any]:
        """刪除此筆攝影機軌跡截圖與相關標註，若已入庫則同步自 feedback_embeddings.npz 撤回。"""
        require_mutation(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        with core.store._connection_lock:
            evidence = core.store.connection.execute(
                "SELECT event_id FROM identity_feedback_evidence WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if evidence is None:
            raise HTTPException(status_code=404, detail="找不到此軌跡截圖，可能已被刪除。")

        def apply_delete() -> None:
            core.store.connection.execute(
                "DELETE FROM identity_feedback_evidence WHERE event_id=?",
                (event_id,),
            )
            core.store.connection.execute(
                "DELETE FROM behavior_event_annotations WHERE event_id=?",
                (event_id,),
            )
            core.store.connection.execute(
                "UPDATE behavior_events SET identity_status='unknown', pet_id=NULL WHERE event_id=?",
                (event_id,),
            )

        core.store._transaction(apply_delete)
        learning = sync_feedback_learning(max_new_samples=16, upgrade_pending_consent=True)
        registry = pet_registry()
        return {
            "status": "deleted",
            "event_id": event_id,
            "learning": learning,
            "pets": [pet_payload(p, job=pet_job(p.pet_id)) for p in registry.list_pets()],
            "message_zh_tw": "已成功刪除此軌跡截圖，並同步更新特徵庫。",
        }

    @app.post("/api/v1/pets/track-candidates/batch")
    def pets_track_candidates_batch(
        request: Request,
        body: Any = Body(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        """批次更新或刪除近期攝影機軌跡截圖，並在結束時一次性同步寫入 feedback_embeddings.npz。"""
        require_mutation(request)
        if core.store is None:
            raise HTTPException(status_code=503, detail="事件資料庫尚未接入。")
        payload = json_body(body)
        operations = payload.get("operations", [])
        if not isinstance(operations, list) or not operations:
            raise HTTPException(status_code=422, detail="批次操作清單不可為空。")

        reviewer = web_reviewer_ref(request)
        registry = pet_registry()
        enabled_pets = {p.pet_id: p.display_name for p in registry.list_pets(enabled_only=True)}

        def apply_batch() -> None:
            for op in operations:
                if not isinstance(op, dict):
                    continue
                action = str(op.get("action") or "").strip()
                event_id = str(op.get("event_id") or "").strip()
                if not event_id:
                    continue

                if action == "delete":
                    core.store.connection.execute(
                        "DELETE FROM identity_feedback_evidence WHERE event_id=?",
                        (event_id,),
                    )
                    core.store.connection.execute(
                        "DELETE FROM behavior_event_annotations WHERE event_id=?",
                        (event_id,),
                    )
                    core.store.connection.execute(
                        "UPDATE behavior_events SET identity_status='unknown', pet_id=NULL WHERE event_id=?",
                        (event_id,),
                    )
                elif action in ("assign", "clear"):
                    pet_id = str(op.get("pet_id") or "").strip()
                    existing_be = core.store.connection.execute(
                        "SELECT 1 FROM behavior_events WHERE event_id=?",
                        (event_id,),
                    ).fetchone()
                    if existing_be is None:
                        ev_row = core.store.connection.execute(
                            "SELECT captured_at FROM identity_feedback_evidence WHERE event_id=?",
                            (event_id,),
                        ).fetchone()
                        captured = str((ev_row["captured_at"] if ev_row else None) or datetime.now(timezone.utc).isoformat())
                        core.store.connection.execute(
                            """INSERT OR IGNORE INTO behavior_events(
                                   event_id, pet_id, track_id, behavior_state, motion_state,
                                   event_tags, started_at, ended_at, duration_seconds,
                                   raw_confidence, smoothed_confidence, inference_method,
                                   model_version, rule_profile_version, source_id, camera_preset
                               ) VALUES (?, NULL, 'camera', 'resting', 'stationary', '[]', ?, ?, 0.0, 0.8, 0.8, 'rule', 'v02', 'v02', 'camera', NULL)""",
                            (event_id, captured, captured),
                        )
                    core.store.connection.execute(
                        "DELETE FROM behavior_event_annotations WHERE event_id=? AND dimension IN ('cat_presence', 'identity')",
                        (event_id,),
                    )
                    if pet_id == "clear" or action == "clear":
                        core.store.connection.execute(
                            "UPDATE identity_feedback_evidence SET learning_status='pending' WHERE event_id=?",
                            (event_id,),
                        )
                    elif pet_id == "not_cat":
                        core.store.connection.execute(
                            """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                               VALUES (?, ?, 'cat_presence', 'not_cat', 0)""",
                            (event_id, reviewer),
                        )
                        core.store.connection.execute(
                            "UPDATE identity_feedback_evidence SET learning_status='unconfirmed_or_conflicting' WHERE event_id=?",
                            (event_id,),
                        )
                    elif pet_id in enabled_pets:
                        core.store.connection.execute(
                            """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                               VALUES (?, ?, 'cat_presence', 'cat', 0)""",
                            (event_id, reviewer),
                        )
                        core.store.connection.execute(
                            """INSERT INTO behavior_event_annotations(event_id, reviewer_ref, dimension, label, learning_consent)
                               VALUES (?, ?, 'identity', ?, 1)""",
                            (event_id, reviewer, pet_id),
                        )
                        core.store.connection.execute(
                            "UPDATE identity_feedback_evidence SET created_at=CURRENT_TIMESTAMP WHERE event_id=?",
                            (event_id,),
                        )

        core.store._transaction(apply_batch)
        learning = sync_feedback_learning(max_new_samples=64, upgrade_pending_consent=True)
        return {
            "status": "batch_applied",
            "operations_count": len(operations),
            "learning": learning,
            "pets": [pet_payload(p, job=pet_job(p.pet_id)) for p in registry.list_pets()],
            "message_zh_tw": f"已成功批次套用 {len(operations)} 筆軌跡變更，並同步更新特徵庫！",
        }

    @app.post("/api/v1/pets")
    async def pets_create(
        request: Request,
        background_tasks: BackgroundTasks,
        display_name: str = Form(...),
        pet_id: str = Form(""),
        coat_description: str = Form(""),
        pattern_description: str = Form(""),
        images: list[UploadFile] = File(...),  # noqa: B008
    ) -> dict[str, Any]:
        require_mutation(request)
        name = display_name.strip()
        if not name or len(name) > 80:
            raise HTTPException(status_code=422, detail="寵物名稱必須為 1 至 80 個字元。")
        try:
            accepted, rejected = await read_reference_images(images)
            if not accepted:
                raise HTTPException(status_code=422, detail="沒有合格的參考照片；請使用清楚、未過度模糊的貓咪 JPG。")

            registry = pet_registry()
            profile = registry.register_pet(
                name,
                coat_description=coat_description.strip()[:200],
                pattern_description=pattern_description.strip()[:200],
                pet_id=pet_id.strip() or None,
            )
            for index, data in enumerate(accepted, start=1):
                registry.add_reference_image(profile.pet_id, data, filename=f"reference_{index:04d}.jpg")
            queued = queue_pet_embeddings(profile.pet_id, background_tasks)
            refreshed = registry.get_pet(profile.pet_id) or profile
            return {
                "status": "created",
                "pet": pet_payload(refreshed, job=queued),
                "accepted_reference_images": len(accepted),
                "rejected": rejected,
                "message_zh_tw": "寵物已登錄；照片已保存，身分特徵正在背景建立。",
            }
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/v1/pets/{pet_id}")
    def pets_update(request: Request, pet_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """更新名稱與外觀描述，不改動既有照片或身分特徵。"""

        require_mutation(request)
        payload = json_body(body)
        allowed = {"display_name", "coat_description", "pattern_description", "enabled", "semantic_traits"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise HTTPException(status_code=422, detail=f"不可更新的欄位：{', '.join(unknown)}。")
        values: dict[str, Any] = {}
        for field in ("display_name", "coat_description", "pattern_description"):
            if field in payload:
                value = payload[field]
                if not isinstance(value, str):
                    raise HTTPException(status_code=422, detail=f"{field} 必須是文字。")
                if field == "display_name" and not value.strip():
                    raise HTTPException(status_code=422, detail="寵物名稱不可為空白。")
                if len(value.strip()) > (80 if field == "display_name" else 200):
                    raise HTTPException(status_code=422, detail=f"{field} 長度超過限制。")
                values[field] = value
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise HTTPException(status_code=422, detail="enabled 必須是布林值。")
            values["enabled"] = payload["enabled"]
        if "semantic_traits" in payload:
            raw_traits = payload["semantic_traits"]
            if isinstance(raw_traits, str):
                import re
                values["semantic_traits"] = [t.strip() for t in re.split(r"[,、/ ]+", raw_traits) if t.strip()]
            elif isinstance(raw_traits, list):
                values["semantic_traits"] = [str(t).strip() for t in raw_traits if str(t).strip()]
            else:
                raise HTTPException(status_code=422, detail="semantic_traits 必須是文字或字串清單。")
        try:
            profile = pet_registry().update_profile(pet_id, **values)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到指定的寵物登錄。") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "status": "updated",
            "pet": pet_payload(profile, job=pet_job(profile.pet_id)),
            "message_zh_tw": "寵物資料已更新；既有身分特徵與照片未變更。",
        }

    @app.get("/api/v1/pets/{pet_id}/images/{image_id}")
    def pet_reference_image(request: Request, pet_id: str, image_id: str) -> Any:
        require_auth(request)
        try:
            content = pet_registry().reference_image_path(pet_id, image_id).read_bytes()
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=404, detail="找不到可讀取的寵物參考照片。") from exc
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    def check_pet_photo_job(pet_id: str) -> None:
        if (pet_job(pet_id) or {}).get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="身分特徵正在建立，請完成後再修改照片。")

    def change_pet_photo(pet_id: str, image_id: str, background_tasks: BackgroundTasks,
                         content: bytes | None = None) -> tuple[Any, dict[str, Any]]:
        with app.state.pet_embedding_jobs_lock:
            check_pet_photo_job(pet_id)
            registry = pet_registry()
            registry.change_reference_image(pet_id, image_id, content)
            profile = registry.get_pet(pet_id)
            job = queue_pet_embeddings(pet_id, background_tasks) if profile.reference_images else set_pet_job(
                pet_id, status="not_started", message_zh_tw="已無參考照片；請新增照片才能建立身分特徵。")
            return profile, job

    @app.post("/api/v1/pets/{pet_id}/images/{image_id}/crop")
    def pet_crop_reference(request: Request, pet_id: str, image_id: str, background_tasks: BackgroundTasks,
                           body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        payload=json_body(body)
        if set(payload)!={"polygon","confirm_single_cat"} or payload["confirm_single_cat"] is not True:
            raise HTTPException(status_code=428,detail="請確認只圈選這隻寵物；兩隻貓需分別登錄。")
        from meowkeeper.core.identity.reference_crop import crop_reference
        try:
            with app.state.pet_embedding_jobs_lock:
                check_pet_photo_job(pet_id)
                registry=pet_registry();path=registry.reference_image_path(pet_id,image_id)
                original=path.read_bytes();cropped=crop_reference(original,payload["polygon"])
                archive=path.parent.parent / "reference_originals"
                if archive.is_symlink():
                    raise ValueError("原圖保存目錄不安全。")
                archive.mkdir(exist_ok=True)
                backup=archive / image_id
                if not backup.exists():
                    with backup.open("xb") as output:
                        output.write(original)
                profile,job=change_pet_photo(pet_id,image_id,background_tasks,cropped)
            return {"pet":pet_payload(profile,job=job),"message_zh_tw":"已保存單貓裁切並排入特徵重建；原圖另存本機reference_originals，人工回饋歷史不變。"}
        except (ValueError,TypeError,KeyError,OSError) as exc:
            raise HTTPException(status_code=422,detail="裁切未保存；請確認照片仍存在，並圈選寬高至少96像素的單隻貓。") from exc

    @app.delete("/api/v1/pets/{pet_id}/images/{image_id}")
    def pet_delete_image(request: Request, pet_id: str, image_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
        require_mutation(request)
        check_pet_photo_job(pet_id)
        try:
            profile, job = change_pet_photo(pet_id, image_id, background_tasks)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=422, detail="照片刪除失敗，請重新整理並確認照片仍存在。") from exc
        return {"pet": pet_payload(profile, job=job), "message_zh_tw": "照片已刪除；參考特徵將重新建立，歷史回饋保留。"}

    @app.put("/api/v1/pets/{pet_id}/images/{image_id}")
    async def pet_replace_image(request: Request, pet_id: str, image_id: str, background_tasks: BackgroundTasks,
                                images: list[UploadFile] = File(...)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        check_pet_photo_job(pet_id)
        if len(images) != 1:
            raise HTTPException(status_code=422, detail="更換照片請選擇一張 JPG。")
        accepted, _rejected = await read_reference_images(images)
        if not accepted:
            raise HTTPException(status_code=422, detail="照片不合格，原照片未修改。")
        try:
            profile, job = change_pet_photo(pet_id, image_id, background_tasks, accepted[0])
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=422, detail="照片更換失敗，請重新整理後再試。") from exc
        return {"pet": pet_payload(profile, job=job),
                "message_zh_tw": "照片已更換；參考特徵將重新建立，歷史回饋保留。"}

    @app.post("/api/v1/pets/{pet_id}/images")
    async def pets_add_images(
        request: Request,
        pet_id: str,
        background_tasks: BackgroundTasks,
        images: list[UploadFile] = File(...),  # noqa: B008
    ) -> dict[str, Any]:
        """追加參考照片，完成後只補建尚未存在的 embedding。"""

        require_mutation(request)
        registry = pet_registry()
        try:
            profile = registry.get_pet(pet_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if profile is None:
            raise HTTPException(status_code=404, detail="找不到指定的寵物登錄。")
        if len(profile.reference_images) + len(images) > 32:
            raise HTTPException(status_code=422, detail="每隻寵物最多保存 32 張參考照片。")
        accepted, rejected = await read_reference_images(images)
        if not accepted:
            raise HTTPException(status_code=422, detail="沒有合格的新增照片；請使用清楚、未過度模糊的貓咪 JPG。")
        with app.state.pet_embedding_jobs_lock:
            check_pet_photo_job(pet_id)
            profile = registry.get_pet(pet_id)
            if len(profile.reference_images) + len(accepted) > 32:
                raise HTTPException(status_code=422, detail="每隻寵物最多保存 32 張參考照片。")
            for data in accepted:
                registry.add_reference_image(profile.pet_id, data)
            queued = queue_pet_embeddings(profile.pet_id, background_tasks)
            refreshed = registry.get_pet(profile.pet_id) or profile
        return {
            "status": "images_added",
            "pet": pet_payload(refreshed, job=queued),
            "accepted_reference_images": len(accepted),
            "rejected": rejected,
            "message_zh_tw": "新增照片已保存；差額身分特徵正在背景建立。",
        }

    @app.post("/api/v1/pets/{pet_id}/embeddings")
    def pets_rebuild_embeddings(
        request: Request,
        pet_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        require_mutation(request)
        try:
            profile = pet_registry().get_pet(pet_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if profile is None:
            raise HTTPException(status_code=404, detail="找不到指定的寵物登錄。")
        queued = queue_pet_embeddings(profile.pet_id, background_tasks)
        return {"status": "queued", "pet": pet_payload(profile, job=queued)}

    @app.get("/api/v1/events/stream")
    def event_stream(request: Request) -> Any:
        require_auth(request)
        return StreamingResponse(
            hub.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/events")
    def events(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
        telegram_status: str | None = None,
    ) -> dict[str, Any]:
        require_auth(request)
        try:
            items = core.list_events(
                limit=limit,
                offset=offset,
                event_type=event_type,
                telegram_status=telegram_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items, "limit": limit, "offset": offset, "next_offset": offset + len(items)}

    @app.post("/api/v1/events/simulate")
    def events_simulate(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        return core.simulate_event(str(payload.get("event_type", "cat_detected")))

    @app.get("/api/v1/events/{event_id}/snapshot")
    def event_snapshot(request: Request, event_id: str) -> Any:
        require_auth(request)
        if core.get_event(event_id) is None:
            raise HTTPException(status_code=404, detail="找不到已授權事件。")
        root = core.event_directory.resolve()
        picture = (root / f"event_{event_id}.jpg").resolve()
        if root not in picture.parents or not picture.is_file():
            raise HTTPException(status_code=404, detail="目前沒有此事件的對照圖片。")
        return FileResponse(str(picture), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    review_video_lock = threading.Lock()
    annotated_export_lock = threading.Lock()

    @app.post("/api/v1/events/{event_id}/annotated-video")
    def annotated_event_video(request: Request, event_id: str, compare: bool = False) -> Any:
        import subprocess
        import sys

        require_mutation(request)
        path = core.event_file(event_id)
        if path is None:
            raise HTTPException(status_code=404, detail="找不到已授權的事件影片。")
        if not annotated_export_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="已有標記匯出工作，請稍後重試。")
        temporary = None
        try:
            import psutil

            if psutil.virtual_memory().available < 2 * 1024 ** 3:
                raise HTTPException(status_code=503, detail="可用記憶體不足 2 GB，暫停選用標記匯出以保護監控；原片保留。")
            temporary = tempfile.TemporaryDirectory(prefix="meowkeeper-marked-export-")

            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            output = Path(temporary.name) / "annotated.mp4"
            command = [str(executable), "-m", "meowkeeper.core.media.annotated_export", str(path), str(output)]
            if compare:
                command.append("--compare")
            completed = subprocess.run(command, shell=False, check=False, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=330,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode != 0 or not output.is_file():
                raise HTTPException(status_code=503, detail="標記匯出失敗：請確認影片為 60 秒內、模型可用且記憶體足夠；原檔保留。")
            telemetry = json.loads(completed.stdout.strip().splitlines()[-1])
            allowed = {"status", "sample_count", "analysis_ms", "encoding_ms", "total_ms",
                       "baseline_encoding_ms", "notification_ms", "annotation", "telegram_sent"}
            if not isinstance(telemetry, dict) or set(telemetry) != allowed or telemetry.get("status") != "passed":
                raise ValueError("匯出工作程序未回報有效結果。")
            from starlette.background import BackgroundTask

            return FileResponse(str(output), media_type="video/mp4", filename="meowkeeper-ai-marked.mp4",
                                headers={"Cache-Control": "no-store", "X-MeowKeeper-Export": json.dumps(telemetry)},
                                background=BackgroundTask(temporary.cleanup))
        except subprocess.TimeoutExpired as error:
            if temporary is not None:
                temporary.cleanup()
            raise HTTPException(status_code=503, detail="標記匯出逾時，已停止匯出程序；原片保留。") from error
        except (OSError, ValueError, IndexError) as error:
            if temporary is not None:
                temporary.cleanup()
            raise HTTPException(status_code=503, detail="標記匯出暫存或結果驗證失敗；原片保留，請稍後重試。") from error
        except BaseException:
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            annotated_export_lock.release()

    @app.get("/api/v1/events/{event_id}/video")
    def event_video(request: Request, event_id: str, playback: bool = False, native: bool = False) -> Any:
        require_auth(request)
        path = core.event_file(event_id)
        if path is None:
            raise HTTPException(status_code=404, detail="找不到已授權的事件影片。")
        if native:
            # Let browsers decode the preserved source first. Unsupported codecs
            # can explicitly fall back to the derived playback copy below.
            return FileResponse(str(path), media_type="video/mp4", filename=path.name,
                                content_disposition_type="inline",
                                headers={"Cache-Control": "no-store", "X-MeowKeeper-Playback": "original"})
        if not playback:
            return FileResponse(str(path), media_type="video/mp4", filename=path.name)
        from meowkeeper.core.media.clip_assembler import ClipAssembler
        from meowkeeper.core.media.media_validation import validate_media
        from meowkeeper.core.media.review_playback_cache import ReviewPlaybackCache

        cache = ReviewPlaybackCache(core.event_directory.resolve().parent / "cache" / "review-playback")
        cached = cache.get(path, event_id)
        if cached is not None:
            return FileResponse(str(cached), media_type="video/mp4", filename="playback.mp4",
                                content_disposition_type="inline",
                                headers={"Cache-Control": "no-store", "X-MeowKeeper-Playback": "local-cache"})
        if not review_video_lock.acquire(blocking=False):
            raise HTTPException(status_code=503, detail="另一段回報影片正在準備，請稍後重試。")
        temporary = None
        try:
            # Another request may have prepared this exact event while this
            # request waited for the process-wide transcode slot.
            cached = cache.get(path, event_id)
            if cached is not None:
                return FileResponse(str(cached), media_type="video/mp4", filename="playback.mp4",
                                    content_disposition_type="inline",
                                    headers={"Cache-Control": "no-store", "X-MeowKeeper-Playback": "local-cache"})
            assembler = ClipAssembler(timeout_seconds=30)
            validation = validate_media(path, ffprobe_path=assembler.ffprobe_path)
            if not validation.valid:
                raise HTTPException(status_code=422, detail="原始事件影片無法讀取，請勿猜測回報。")
            if validation.telegram_compatible:
                return FileResponse(str(path), media_type="video/mp4",
                                    filename=path.name, content_disposition_type="inline")
            temporary = tempfile.TemporaryDirectory(prefix="meowkeeper-review-")
            result = assembler.transcode_for_telegram(path, Path(temporary.name) / "playback.mp4")
            if result.path is None:
                raise HTTPException(status_code=503, detail="瀏覽器相容影片準備失敗，原始影片仍保留。")
            from starlette.background import BackgroundTask

            cached = cache.store(path, event_id, result.path)
            if cached is not None:
                temporary.cleanup()
                temporary = None
                return FileResponse(str(cached), media_type="video/mp4", filename="playback.mp4",
                                    content_disposition_type="inline",
                                    headers={"Cache-Control": "no-store", "X-MeowKeeper-Playback": "prepared"})

            return FileResponse(str(result.path), media_type="video/mp4",
                                filename="playback.mp4",
                                content_disposition_type="inline",
                                background=BackgroundTask(temporary.cleanup))
        except BaseException:
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            review_video_lock.release()

    @app.get("/api/v1/events/{event_id}")
    def event_detail(request: Request, event_id: str) -> dict[str, Any]:
        require_auth(request)
        item = core.get_event(event_id)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到事件。")
        return item

    @app.post("/api/v1/events/{event_id}/label")
    def event_label(request: Request, event_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        item = core.get_event(event_id)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到事件。")
        payload = json_body(body)
        label = str(payload.get("label", "")).strip()
        if not label or len(label) > 80:
            raise HTTPException(status_code=400, detail="事件標籤不可為空白或過長。")
        is_correct = payload.get("is_correct")
        if is_correct is not None and not isinstance(is_correct, bool):
            raise HTTPException(status_code=400, detail="事件正確性標記必須是布林值。")
        if core.store is not None:
            core.store.add_event_label(event_id, label, is_correct=is_correct, note=payload.get("note"))
        hub.publish("event_label", {"event_id": event_id, "label": label})
        return {"status": "accepted", "event_id": event_id, "label": label}

    @app.post("/api/v1/live/save")
    def live_save(request: Request, body: Any = Body(default=None)) -> Any:  # noqa: B008
        require_mutation(request)
        kind = json_body(body).get("kind")
        if kind not in {"photo", "video"}:
            raise HTTPException(status_code=400, detail="請選擇截圖或保存前 15 秒影片。")
        result = core.save_capture(kind)
        return JSONResponse(content=result, status_code=200 if result.get("status") == "saved" else 503)

    @app.post("/api/v1/live/recent")
    def live_recent(request: Request, body: Any = Body(default=None)) -> Any:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        try:
            pre = float(payload.get("pre_seconds", 5.0))
            post = float(payload.get("post_seconds", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="/live 秒數格式不正確。") from exc
        if not 0.0 <= pre <= 120.0 or not 0.0 <= post <= 30.0:
            raise HTTPException(status_code=400, detail="/live 秒數超出允許範圍。")
        result = core.request_recent_clip(pre, post)
        path = getattr(result, "path", result if isinstance(result, (str, Path)) else None)
        if path:
            candidate = Path(path).resolve()
            root = core.event_directory.resolve()
            if candidate.is_file() and (candidate == root or root in candidate.parents):
                return FileResponse(str(candidate), media_type="video/mp4", filename=candidate.name)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_available", "message_zh_tw": "目前尚未接入可用的環形快取片段服務。"},
        )

    @app.get("/api/v1/snapshot")
    def snapshot(request: Request) -> Any:
        require_auth(request)
        path = core.request_snapshot(high_quality=True)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="目前沒有可授權的快照。")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=503, detail="快照暫時無法讀取；請稍後重試。") from exc
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/snapshot")
    def snapshot_post(request: Request) -> Any:
        return snapshot(request)

    @app.get("/api/v1/preview/status")
    def preview_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        result = core.preview_status()
        integration = integration_or_not_configured()
        if integration is not None and result.get("source_is_xiaomi") is True:
            try:
                for device in integration.devices():
                    if (device.get("selected") is True
                            and device.get("stream_name") == result.get("stream_name")
                            and isinstance(device.get("quality"), Mapping)):
                        quality = device["quality"]
                        if type(quality.get("width")) is int and type(quality.get("height")) is int:
                            result["original_source"] = {
                                key: quality[key] for key in ("width", "height", "fps", "video_codec", "verified_at", "historical", "name")
                                if key in quality
                            }
                        break
            except (AttributeError, OSError, RuntimeError, ValueError):
                # A stored source-profile lookup is optional; it must not block
                # the shared JPEG/MSE preview when setup state is unavailable.
                pass
        return result

    @app.get("/api/v1/preview/jpeg")
    def preview_jpeg(request: Request) -> Any:
        require_auth(request)
        record = session_from_request(request)
        tracker = getattr(app.state, "live_viewer_tracker", None)
        client = getattr(request, "client", None)
        path = core.request_snapshot(high_quality=False)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="目前沒有既有 ingest JPEG 預覽。")
        try:
            from meowkeeper.core.media.shared_file import read_shared_bytes

            content = read_shared_bytes(path)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="共享預覽正在更新或無法讀取；請稍後重試。") from exc
        if record is not None and tracker is not None:
            tracker.touch_jpeg(
                request.headers.get("x-meowkeeper-viewer-id"),
                record.session_id,
                getattr(client, "host", None),
            )
        # Close the file before sending to slow LAN clients, allowing Windows
        # to replace the ingest cache without a long-lived FileResponse lock.
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/api/v1/preview/webrtc")
    async def preview_webrtc(websocket: WebSocket) -> None:
        integration = getattr(app.state, "integrations", None)
        await serve_rtc(websocket, core=core, sessions=sessions,
                        go2rtc=getattr(integration, "go2rtc", None),
                        slots=app.state.preview_mse_slots, source=app.state.preview_rtc_source,
                        viewer_tracker=app.state.live_viewer_tracker)

    @app.websocket("/api/v1/preview/mse")
    async def preview_mse(websocket: WebSocket) -> None:
        """Proxy only the active, local Xiaomi stream; never expose go2rtc API."""

        integration = getattr(app.state, "integrations", None)
        go2rtc = getattr(integration, "go2rtc", None)
        await proxy_mse_preview(
            websocket,
            core=core,
            sessions=sessions,
            go2rtc=go2rtc,
            slots=app.state.preview_mse_slots,
            viewer_tracker=app.state.live_viewer_tracker,
            transcode=app.state.preview_transcode,
        )

    @app.get("/api/v1/settings")
    def settings_view(request: Request) -> dict[str, Any]:
        require_auth(request)
        return {"settings": core.settings()}

    @app.get("/api/v1/network/dashboard")
    def dashboard_network_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        network = getattr(app.state, "dashboard_network", None)
        if network is None or not callable(getattr(network, "snapshot", None)):
            return core.snapshot().get("dashboard", {})
        return dict(network.snapshot())

    @app.put("/api/v1/network/dashboard")
    def dashboard_network_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改區網 Dashboard 設定需要再次確認。")
        enabled = payload.get("lan_access_enabled")
        raw_port = payload.get("dashboard_port", payload.get("port"))
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail="允許區網存取必須是布林值。")
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Dashboard Port 必須是整數。") from exc
        try:
            settings = core.update_settings({"lan_access_enabled": enabled, "dashboard_port": port})
        except SettingsValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        network = getattr(app.state, "dashboard_network", None)
        snapshot = dict(network.snapshot()) if network is not None and callable(getattr(network, "snapshot", None)) else core.snapshot().get("dashboard", {})
        return {"settings": settings, "network": snapshot, "status": "restart_required" if snapshot.get("restart_required") else "applied"}

    @app.post("/api/v1/network/firewall")
    def dashboard_firewall(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.get("reauth", False)):
            raise HTTPException(status_code=428, detail="修改 Windows 防火牆需要再次確認。")
        action = str(payload.get("action", "")).strip().lower()
        network = getattr(app.state, "dashboard_network", None)
        if network is None or not callable(getattr(network, "firewall_action", None)):
            return {"status": "not_checked", "enabled": False, "message_zh_tw": "目前沒有可用的 Dashboard 防火牆服務。"}
        try:
            result = dict(network.firewall_action(action))
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": result.get("status", "not_checked"), "firewall": result}

    @app.put("/api/v1/settings")
    def settings_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改重要設定需要再次確認。")
        try:
            updated = core.update_settings(payload)
        except SettingsValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"AI／行為設定保存失敗：{type(exc).__name__}。") from exc
        restart_required = bool(set(payload) & {
            "inference_device",
            "analyzer_mode",
            "confidence_threshold",
            "identity_accepted_threshold",
            "identity_minimum_margin",
            "analysis_fps",
            "analysis_width",
            "activity_fps",
            "identity_frame_width",
            "preview_frame_width",
            "cooldown_seconds",
            "event_pre_seconds",
            "event_post_seconds",
            "retention_seconds",
            "ring_maximum_size_mb",
            "telegram_startup_notice",
        })
        return {
            "settings": updated,
            "restart_required": restart_required,
            "message_zh_tw": (
                "執行設定已保存；請重新啟動 Host 後套用到下一個監控 session。"
                if restart_required
                else "設定已保存。"
            ),
        }

    @app.post("/api/v1/settings/reset")
    def settings_reset(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        if auth_kind == "session" and not bool(json_body(body).get("reauth", False)):
            raise HTTPException(status_code=428, detail="恢復預設值需要再次確認。")
        defaults = {
            "confidence_threshold": 0.45,
            "identity_accepted_threshold": 0.62,
            "identity_minimum_margin": 0.05,
            "inference_device": "auto",
            "analysis_fps": 5,
            "analysis_width": 640,
            "activity_fps": 5,
            "identity_frame_width": 1280,
            "preview_frame_width": 1920,
            "cooldown_seconds": 180.0,
            "event_pre_seconds": 5.0,
            "event_post_seconds": 10.0,
            "retention_seconds": 60.0,
            "ring_maximum_size_mb": 512.0,
            "dashboard_auto_open": False,
            "telegram_startup_notice": False,
        }
        if not core.settings().get("analyzer_override", False):
            defaults["analyzer_mode"] = "yolo"
        core.update_settings(defaults)
        return {
            "settings": core.settings(),
            "status": "reset",
            "restart_required": True,
            "message_zh_tw": "已恢復 AI／行為預設值；請重新啟動 Host 後套用。",
        }

    @app.get("/api/v1/telegram/status")
    def telegram_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        return core.snapshot().get("metrics", {}).get("telegram", {"status": "not_checked"})

    def telegram_credential_store() -> Any | None:
        store = getattr(app.state, "telegram_credentials", None)
        if store is not None:
            return store
        try:
            from meowkeeper.security.credentials import TelegramCredentialStore

            store = TelegramCredentialStore()
        except (ImportError, OSError, RuntimeError):
            return None
        app.state.telegram_credentials = store
        return store

    @app.post("/api/v1/settings/backup/export")
    def settings_backup_export(request: Request, body: Any = Body(default=None)) -> Any:  # noqa: B008
        require_mutation(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="加密備份只能從本機 Dashboard 匯出。")
        password = json_body(body).get("password")
        if not isinstance(password, str) or len(password) < 6:
            raise HTTPException(status_code=422, detail="請輸入至少 6 個字元的備份密碼。")
        config_path = getattr(getattr(app.state, "source_config", None), "path", None)
        store = telegram_credential_store()
        if config_path is None or store is None:
            raise HTTPException(status_code=503, detail="設定或 Windows 憑證庫尚未接入，無法完整備份。")
        from meowkeeper.security.encrypted_backup import encrypt_settings

        try:
            path = Path(config_path)
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if not isinstance(config, dict):
                raise TypeError
            credentials = store.read_telegram()
            encrypted = encrypt_settings({
                "config": config,
                "telegram": {
                    "bot_token": credentials.bot_token,
                    "chat_ids": credentials.chat_ids,
                    "recipients_json": credentials.recipients_json,
                },
            }, password)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="設定備份失敗；請確認設定檔、憑證庫與密碼長度。") from exc
        return JSONResponse(
            content=encrypted,
            headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="MeowKeeperPlus-settings.encrypted.json"'},
        )

    @app.post("/api/v1/settings/backup/import")
    def settings_backup_import(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="加密備份只能從本機 Dashboard 匯入。")
        values = json_body(body)
        if values.get("confirm") is not True:
            raise HTTPException(status_code=428, detail="匯入會覆蓋目前設定與 Telegram 憑證，請明確確認。")
        password, envelope = values.get("password"), values.get("backup")
        if not isinstance(password, str) or not isinstance(envelope, dict):
            raise HTTPException(status_code=422, detail="請選擇加密 JSON 檔並輸入備份密碼。")
        config_path = getattr(getattr(app.state, "source_config", None), "path", None)
        store = telegram_credential_store()
        if config_path is None or store is None:
            raise HTTPException(status_code=503, detail="設定或 Windows 憑證庫尚未接入，無法匯入。")
        from meowkeeper.app.source_config import SourceConfig, _atomic_write
        from meowkeeper.security.encrypted_backup import decrypt_settings

        try:
            restored = decrypt_settings(envelope, password)
            config, telegram = restored.get("config"), restored.get("telegram")
            if not isinstance(config, dict) or not isinstance(telegram, dict):
                raise TypeError("備份內容缺少設定或 Telegram 區段。")
            media = config.get("media") or {}
            if not isinstance(media, dict):
                raise TypeError("備份中的來源設定格式不正確。")
            source_type = str(media.get("source_type") or "mock").lower()
            youtube = media.get("youtube") or {}
            rtsp = media.get("rtsp") or {}
            if not isinstance(youtube, dict) or not isinstance(rtsp, dict):
                raise TypeError("備份中的來源設定格式不正確。")
            url = (youtube.get("url") if source_type == "youtube" else rtsp.get("url") if source_type == "rtsp" else media.get("url")) or ""
            SourceConfig(source_type=source_type, url=str(url), max_height=int(youtube.get("max_height", 720))).validate()
            token = telegram.get("bot_token", "")
            chat_ids = telegram.get("chat_ids", "")
            recipients_json = telegram.get("recipients_json", "")
            if not all(isinstance(item, str) for item in (token, chat_ids, recipients_json)):
                raise ValueError("備份中的 Telegram 憑證格式不正確。")
            recipients = json.loads(recipients_json) if recipients_json else None
            if recipients is not None and not isinstance(recipients, list):
                raise ValueError("備份中的通知對象格式不正確。")
            if not token and (chat_ids or recipients_json):
                raise ValueError("備份缺少 Bot Token。")
            path = Path(config_path)
            previous_config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if not isinstance(previous_config, dict):
                raise TypeError("目前設定檔格式不正確。")
            _atomic_write(path, config)
            try:
                if token:
                    store.save_telegram(token, chat_ids, recipients=recipients)
                else:
                    store.clear_telegram()
            except Exception:
                _atomic_write(path, previous_config)
                raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="匯入失敗；密碼、檔案格式或本機憑證保存有誤，請檢查後重試。") from exc
        return {"status": "restored", "restart_required": True, "message_zh_tw": "設定與 Telegram 憑證已匯入；請重新啟動 Host 後套用。"}

    def _full_backup_context() -> tuple[Path, Path, Path, Any | None, Any | None, Any | None]:
        """Resolve the live Host data boundaries without exposing them to clients."""

        config_value = getattr(getattr(app.state, "source_config", None), "path", None)
        config_path = Path(config_value or "config.json").resolve()
        controller = getattr(app.state, "core_controller", None)
        facade = getattr(app.state, "core", None)
        store = getattr(controller, "store", None) or getattr(facade, "store", None)
        event_directory = getattr(controller, "event_directory", None) or getattr(facade, "event_directory", None)
        data_root = Path(event_directory or config_path.parent / "data").resolve().parent
        database_value = getattr(store, "path", None) if store is not None else None
        database_path = Path(database_value or data_root / "meowkeeper.db").resolve()
        connection = getattr(store, "connection", None)
        lock = getattr(store, "_connection_lock", None)
        return config_path, data_root, database_path, store, connection, lock

    def _full_backup_telegram_payload(store: Any | None) -> dict[str, Any] | None:
        if store is None:
            return None
        credentials = store.read_telegram()
        recipients: Any = None
        if credentials.recipients_json:
            recipients = json.loads(credentials.recipients_json)
        return {
            "bot_token": credentials.bot_token,
            "chat_ids": credentials.chat_ids,
            "recipients": recipients,
        }

    _backup_phase_labels = {
        "queued": "等待背景工作",
        "snapshot": "建立一致性資料庫快照",
        "inventory": "整理備份項目",
        "indexing": "逐項建立完整性索引",
        "packing": "逐項打包 ZIP 內容",
        "encrypting": "加密備份內容",
        "writing_archive": "完成加密 ZIP 封裝",
        "reading_archive": "讀取 ZIP 內容",
        "decrypting": "解密並驗證密碼",
        "extracting": "逐項解壓到暫存區",
        "verifying": "逐項校驗檔案完整性",
        "restoring": "逐項套用資料",
        "completed": "處理完成",
        "failed": "處理失敗",
    }
    _backup_progress_ranges = {
        "snapshot": (0, 5), "inventory": (5, 8), "indexing": (8, 35),
        "packing": (35, 58), "encrypting": (58, 90), "writing_archive": (90, 98),
        "reading_archive": (5, 15), "decrypting": (15, 42), "extracting": (42, 63),
        "verifying": (63, 82), "restoring": (82, 98),
    }

    def _backup_safe_error(exc: Exception) -> str:
        from meowkeeper.security.full_backup import FullBackupError

        if isinstance(exc, FullBackupError):
            return str(exc)
        if isinstance(exc, zipfile.BadZipFile):
            return "ZIP 檔案不完整或結構損毀；尚未套用備份資料。"
        if isinstance(exc, json.JSONDecodeError):
            return "備份索引格式錯誤或內容不完整；尚未套用備份資料。"
        if isinstance(exc, sqlite3.Error):
            return "SQLite 資料庫讀寫失敗；請確認資料庫可用及磁碟空間。"
        if isinstance(exc, OSError):
            if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
                return "檔案存取被 Windows 拒絕；請確認 MeowKeeper+ 資料夾權限及檔案未被占用。"
            if getattr(exc, "errno", None) == 28 or getattr(exc, "winerror", None) == 112:
                return "磁碟空間不足；請先釋出空間後重試。"
            if isinstance(exc, FileNotFoundError):
                return "備份檔或本機資料在處理期間消失；請確認檔案仍存在後重試。"
            return "本機儲存裝置讀寫失敗；請確認磁碟、資料夾權限與可用空間。"
        if isinstance(exc, (TypeError, ValueError)):
            return f"資料格式或欄位不符合備份規格（{type(exc).__name__}）；請查看處理階段並確認是否需要重新匯入。"
        return f"處理時發生系統錯誤（{type(exc).__name__}）；請檢查本機診斷記錄。"

    def _set_backup_job(job_id: str, **values: Any) -> dict[str, Any]:
        with app.state.full_backup_jobs_lock:
            current = app.state.full_backup_jobs.get(job_id)
            if current is None:
                return {}
            current.update(values)
            return dict(current)

    def _backup_job_snapshot(job_id: str) -> dict[str, Any] | None:
        with app.state.full_backup_jobs_lock:
            job = app.state.full_backup_jobs.get(job_id)
            if job is None:
                return None
            return {
                "job_id": job_id,
                "operation": job["operation"],
                "status": job["status"],
                "phase_zh_tw": _backup_phase_labels.get(job.get("phase"), "處理中"),
                "percent": job.get("percent", 0),
                "completed": job.get("completed", 0),
                "total": job.get("total", 0),
                "bytes_done": job.get("bytes_done"),
                "bytes_total": job.get("bytes_total"),
                "current_item_label": job.get("current_item_label"),
                "items": list(job.get("items", [])),
                "items_omitted": job.get("items_omitted", 0),
                "message_zh_tw": job.get("message_zh_tw", ""),
                "error_reason_zh_tw": job.get("error_reason_zh_tw"),
                "failed_during_zh_tw": job.get("failed_during_zh_tw"),
                "archive_bytes": job.get("archive_bytes"),
                "file_count": job.get("file_count"),
                "restored_files": job.get("restored_files"),
            }

    def _create_backup_job(operation: str, temporary_root: Path) -> str:
        now = datetime.now(timezone.utc).timestamp()
        with app.state.full_backup_jobs_lock:
            stale = [
                key for key, value in app.state.full_backup_jobs.items()
                if value.get("status") not in {"queued", "running"}
                and now - value.get("updated_at", now) > 7200
            ]
            for key in stale:
                old = app.state.full_backup_jobs.pop(key, {})
                old_root = old.get("temporary_root")
                if old_root:
                    shutil.rmtree(old_root, ignore_errors=True)
            active_count = sum(value.get("status") in {"queued", "running"} for value in app.state.full_backup_jobs.values())
            if active_count >= 1:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise HTTPException(status_code=409, detail="目前已有完整備份工作；請等待完成後再操作，避免匯入與匯出互相干擾。")
            job_id = secrets.token_urlsafe(24)
            app.state.full_backup_jobs[job_id] = {
                "operation": operation,
                "status": "queued",
                "phase": "queued",
                "percent": 0,
                "completed": 0,
                "total": 0,
                "bytes_done": None,
                "bytes_total": None,
                "current_item_label": None,
                "items": [],
                "items_omitted": 0,
                "message_zh_tw": "工作已排入；頁面會持續顯示處理進度。",
                "temporary_root": str(temporary_root),
                "created_at": now,
                "updated_at": now,
            }
            return job_id

    def _backup_progress_callback(job_id: str):
        def update(progress: dict[str, Any]) -> None:
            phase = str(progress.get("phase") or "queued")
            lower, upper = _backup_progress_ranges.get(phase, (0, 98))
            completed = int(progress.get("completed") or 0)
            total = int(progress.get("total") or 0)
            bytes_done = progress.get("bytes_done")
            bytes_total = progress.get("bytes_total")
            if bytes_total and bytes_total > 0:
                byte_fraction = min(1.0, max(0.0, bytes_done / bytes_total))
                if total > 0 and progress.get("item_label") and completed > 0:
                    fraction = min(1.0, max(0.0, (completed - 1 + byte_fraction) / total))
                else:
                    fraction = byte_fraction
            elif total > 0:
                fraction = min(1.0, max(0.0, completed / total))
            else:
                fraction = 0.0
            percent = round(lower + (upper - lower) * fraction)
            now = datetime.now(timezone.utc).timestamp()
            with app.state.full_backup_jobs_lock:
                job = app.state.full_backup_jobs.get(job_id)
                if job is None:
                    return
                job.update({
                    "status": "running" if phase != "completed" else "completed",
                    "phase": phase,
                    "percent": 100 if phase == "completed" else percent,
                    "completed": completed,
                    "total": total,
                    "bytes_done": bytes_done,
                    "bytes_total": bytes_total,
                    "current_item_label": progress.get("item_label"),
                    "message_zh_tw": _backup_phase_labels.get(phase, "正在處理備份資料…"),
                    "updated_at": now,
                })
                if progress.get("item_complete") and progress.get("item_label"):
                    items = job.setdefault("items", [])
                    event = {
                        "phase_zh_tw": _backup_phase_labels.get(phase, "處理項目"),
                        "label": progress["item_label"],
                        "completed": completed,
                        "total": total,
                    }
                    if len(items) < 500:
                        items.append(event)
                    else:
                        job["items_omitted"] = int(job.get("items_omitted", 0)) + 1

        return update

    def _run_full_backup_export(job_id: str, password: str) -> None:
        from meowkeeper.security.full_backup import create_full_backup

        job = app.state.full_backup_jobs.get(job_id) or {}
        temporary_root = Path(job.get("temporary_root") or tempfile.mkdtemp(prefix="meowkeeper-full-export-"))
        try:
            config_path, data_root, database_path, _database_store, connection, lock = _full_backup_context()
            credential_store = telegram_credential_store()
            if credential_store is None:
                raise RuntimeError("Windows 憑證庫尚未可用。")
            payload = _full_backup_telegram_payload(credential_store)
            output, info = create_full_backup(
                config_path=config_path,
                data_root=data_root,
                database_path=database_path,
                password=password,
                telegram=payload,
                database_connection=connection,
                database_lock=lock,
                output_directory=temporary_root,
                on_progress=_backup_progress_callback(job_id),
            )
            _set_backup_job(
                job_id, status="completed", phase="completed", percent=100,
                message_zh_tw="備份已加密並完成；正在準備下載。",
                output_path=str(output), archive_bytes=int(info["archive_bytes"]),
                file_count=int(info["file_count"]), updated_at=datetime.now(timezone.utc).timestamp(),
            )
        except Exception as exc:  # noqa: BLE001 - background work reports a safe reason to the UI
            current = _backup_job_snapshot(job_id) or {}
            _set_backup_job(
                job_id, status="failed", phase="failed",
                message_zh_tw="完整資料匯出未完成。", error_reason_zh_tw=_backup_safe_error(exc),
                failed_during_zh_tw=current.get("phase_zh_tw", "備份處理"),
                updated_at=datetime.now(timezone.utc).timestamp(),
            )
            shutil.rmtree(temporary_root, ignore_errors=True)

    def _run_full_backup_import(
        job_id: str, password: str, upload_path: Path, credential_store: Any,
    ) -> None:
        from meowkeeper.security.full_backup import restore_full_backup

        job = app.state.full_backup_jobs.get(job_id) or {}
        temporary_root = Path(job.get("temporary_root") or upload_path.parent)
        try:
            config_path, data_root, database_path, _database_store, connection, lock = _full_backup_context()
            result = restore_full_backup(
                backup_path=upload_path,
                password=password,
                config_path=config_path,
                data_root=data_root,
                database_path=database_path,
                telegram_store=credential_store,
                database_connection=connection,
                database_lock=lock,
                on_progress=_backup_progress_callback(job_id),
            )
            _set_backup_job(
                job_id, status="completed", phase="completed", percent=100,
                message_zh_tw=result.get("message_zh_tw", "完整資料已還原。"),
                restored_files=int(result.get("restored_files") or 0),
                updated_at=datetime.now(timezone.utc).timestamp(),
            )
        except Exception as exc:  # noqa: BLE001 - background work reports a safe reason to the UI
            current = _backup_job_snapshot(job_id) or {}
            reason = _backup_safe_error(exc)
            if current.get("phase_zh_tw") == "逐項套用資料":
                reason += " 失敗前已完成的項目可能已寫入；請檢查資料狀態後再重試。"
            _set_backup_job(
                job_id, status="failed", phase="failed",
                message_zh_tw="完整資料匯入未完成。", error_reason_zh_tw=reason,
                failed_during_zh_tw=current.get("phase_zh_tw", "備份處理"),
                updated_at=datetime.now(timezone.utc).timestamp(),
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    @app.post("/api/v1/settings/backup/full-export")
    def settings_full_backup_export(
        request: Request, background_tasks: BackgroundTasks, body: Any = Body(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        require_mutation(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="完整資料備份只能從本機 Dashboard 匯出。")
        password = json_body(body).get("password")
        if not isinstance(password, str) or len(password) < 6:
            raise HTTPException(status_code=422, detail="請輸入至少 6 個字元的備份密碼。")
        from meowkeeper.security.full_backup import FullBackupError, _key

        try:
            _key(password, os.urandom(16))
        except FullBackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        credential_store = telegram_credential_store()
        if credential_store is None:
            raise HTTPException(status_code=503, detail="Windows 憑證庫尚未可用，無法建立完整備份。")
        temporary_root = Path(tempfile.mkdtemp(prefix="meowkeeper-full-export-"))
        job_id = _create_backup_job("export", temporary_root)
        background_tasks.add_task(_run_full_backup_export, job_id, password)
        return {"job_id": job_id, "status": "queued", "message_zh_tw": "完整備份已排入背景處理。"}

    @app.post("/api/v1/settings/backup/full-import")
    async def settings_full_backup_import(
        request: Request,
        background_tasks: BackgroundTasks,
        file: Any = File(...),  # noqa: B008
        password: str = Form(...),
        confirm: str = Form(...),
    ) -> dict[str, Any]:
        require_mutation(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="完整資料備份只能從本機 Dashboard 匯入。")
        if len(password) < 6:
            raise HTTPException(status_code=422, detail="請輸入至少 6 個字元的備份密碼。")
        if str(confirm).lower() != "true":
            raise HTTPException(status_code=428, detail="完整匯入會還原事件、回饋與寵物資料，請明確確認。")
        from meowkeeper.security.full_backup import MAX_BACKUP_BYTES, FullBackupError, _key

        try:
            _key(password, os.urandom(16))
        except FullBackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        credential_store = telegram_credential_store()
        if credential_store is None:
            raise HTTPException(status_code=503, detail="Windows 憑證庫尚未可用，無法還原完整備份。")
        temporary_root = Path(tempfile.mkdtemp(prefix="meowkeeper-full-import-"))
        upload_path = temporary_root / "backup.zip"
        try:
            total = 0
            with upload_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BACKUP_BYTES:
                        raise FullBackupError("完整備份超過允許大小。")
                    destination.write(chunk)
        except (FullBackupError, OSError) as exc:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise HTTPException(status_code=422, detail=_backup_safe_error(exc)) from exc
        finally:
            await file.close()
        job_id = _create_backup_job("import", temporary_root)
        _set_backup_job(job_id, phase="reading_archive", message_zh_tw="上傳完成，開始檢查 ZIP。")
        background_tasks.add_task(_run_full_backup_import, job_id, password, upload_path, credential_store)
        return {"job_id": job_id, "status": "queued", "message_zh_tw": "備份檔已上傳，開始逐項驗證與還原。"}

    @app.get("/api/v1/settings/backup/jobs/{job_id}")
    def settings_full_backup_job(request: Request, job_id: str) -> dict[str, Any]:
        require_auth(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="完整資料備份只能從本機 Dashboard 檢視。")
        result = _backup_job_snapshot(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="找不到備份工作，可能已逾時清理；請重新開始。")
        return result

    @app.get("/api/v1/settings/backup/jobs/{job_id}/download")
    def settings_full_backup_download(request: Request, job_id: str) -> Any:
        require_auth(request)
        if not is_local_request(request):
            raise HTTPException(status_code=403, detail="完整資料備份只能從本機 Dashboard 下載。")
        with app.state.full_backup_jobs_lock:
            job = app.state.full_backup_jobs.get(job_id)
            if job is None or job.get("operation") != "export":
                raise HTTPException(status_code=404, detail="找不到可下載的完整備份。")
            if job.get("status") != "completed" or not job.get("output_path"):
                raise HTTPException(status_code=409, detail="完整備份尚未完成。")
            output = Path(job["output_path"])
            temporary_root = Path(job["temporary_root"])
        if not output.is_file():
            raise HTTPException(status_code=410, detail="備份暫存檔已清理；請重新匯出。")
        from starlette.background import BackgroundTask

        def cleanup_export() -> None:
            shutil.rmtree(temporary_root, ignore_errors=True)
            _set_backup_job(
                job_id, status="completed", message_zh_tw="備份 ZIP 已下載。",
                output_path=None, updated_at=datetime.now(timezone.utc).timestamp(),
            )

        return FileResponse(
            output,
            media_type="application/zip",
            filename=output.name,
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(cleanup_export),
        )

    def telegram_credential_status() -> dict[str, Any]:
        env_token = bool(os.getenv("MEOWKEEPER_TELEGRAM_BOT_TOKEN", "").strip())
        env_chat_ids = bool(os.getenv("MEOWKEEPER_TELEGRAM_CHAT_IDS", "").strip())
        result: dict[str, Any] = {
            "credential_backend": "environment",
            "bot_token_configured": env_token,
            "chat_ids_configured": env_chat_ids,
        }
        store = telegram_credential_store()
        if store is None:
            result["secret_source"] = "環境變數"
            return result
        try:
            stored = dict(store.status())
        except Exception:  # noqa: BLE001 - status must never expose backend details
            stored = {"backend": "unavailable", "bot_token_configured": False, "chat_ids_configured": False}
        stored_token = bool(stored.get("bot_token_configured"))
        stored_chat_ids = bool(stored.get("chat_ids_configured"))
        result.update(
            {
                "credential_backend": str(stored.get("backend") or "unavailable"),
                "bot_token_configured": env_token or stored_token,
                "chat_ids_configured": env_chat_ids or stored_chat_ids,
                "recipient_count": int(stored.get("recipient_count") or 0),
                "recipients": list(stored.get("recipients") or []),
            }
        )
        sources = []
        if stored_token or stored_chat_ids:
            sources.append("Windows Credential Manager")
        if env_token or env_chat_ids:
            sources.append("環境變數")
        result["secret_source"] = "／".join(sources) if sources else "尚未設定"
        return result

    @app.get("/api/v1/telegram/config")
    def telegram_config_view(request: Request) -> dict[str, Any]:
        require_auth(request)
        service = getattr(app.state, "source_config", None)
        path = getattr(service, "path", None)
        if path is None:
            return {"enabled": False, "restart_required": False, **telegram_credential_status()}
        from meowkeeper.app.source_config import load_telegram_enabled

        try:
            enabled = load_telegram_enabled(path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"Telegram 設定無法讀取：{type(exc).__name__}。") from exc
        return {
            "enabled": enabled,
            "restart_required": False,
            **telegram_credential_status(),
        }

    @app.put("/api/v1/telegram/config")
    def telegram_config_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改 Telegram 啟用狀態需要再次確認。")
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail="Telegram 啟用狀態必須是布林值。")
        service = getattr(app.state, "source_config", None)
        path = getattr(service, "path", None)
        if path is None:
            raise HTTPException(status_code=503, detail="設定保存服務尚未接入。")
        from meowkeeper.app.source_config import save_telegram_enabled

        try:
            save_telegram_enabled(path, enabled)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"Telegram 設定保存失敗：{type(exc).__name__}。") from exc
        return {
            "enabled": enabled,
            "restart_required": True,
            **telegram_credential_status(),
            "message_zh_tw": "Telegram 啟用狀態已保存；請重新啟動 Host 後測試。",
        }

    @app.post("/api/v1/telegram/credentials")
    def telegram_credentials_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改 Telegram 憑證需要再次確認。")
        store = telegram_credential_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="目前無法使用 Windows Credential Manager；請保留使用本機環境變數方式。",
            )
        try:
            if payload.get("clear") is True:
                store.clear_telegram()
                return {
                    "status": "cleared",
                    "restart_required": True,
                    **telegram_credential_status(),
                    "message_zh_tw": "Telegram 本機憑證已清除；請重新啟動 Host。",
                }
            token = payload.get("bot_token")
            chat_ids = payload.get("chat_ids")
            if not isinstance(token, str) or not isinstance(chat_ids, str):
                raise TypeError("請填寫 Bot Token 與至少一個 Chat ID。")
            recipients = payload.get("recipients")
            if recipients is None:
                store.save_telegram(token, chat_ids)
            else:
                store.save_telegram(token, chat_ids, recipients=recipients)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "status": "saved",
            "restart_required": True,
            **telegram_credential_status(),
            "message_zh_tw": "Telegram 憑證已安全保存至本機；請重新啟動 Host 後測試。",
        }

    @app.post("/api/v1/telegram/recipients")
    def telegram_recipients_update(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """保存通知對象清單；Bot Token 只留在 Credential Manager，不會經過此頁回傳。"""

        auth_kind = require_mutation(request)
        payload = json_body(body)
        if auth_kind == "session" and not bool(payload.pop("reauth", False)):
            raise HTTPException(status_code=428, detail="修改 Telegram 通知對象需要再次確認。")
        recipients = payload.get("recipients")
        if recipients is None:
            raise HTTPException(status_code=422, detail="請提供至少一個通知對象。")
        store = telegram_credential_store()
        if store is None:
            raise HTTPException(status_code=503, detail="目前無法使用 Windows Credential Manager。")
        try:
            store.save_recipients(recipients)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "status": "saved",
            "restart_required": True,
            **telegram_credential_status(),
            "message_zh_tw": "Telegram 通知對象已保存；請啟用 Telegram 並重新啟動 Host 後套用。",
        }

    @app.post("/api/v1/telegram/test")
    def telegram_test(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        callback = core._callbacks.get("telegram_test")
        if callback is None:
            return {"status": "not_checked", "message_zh_tw": "Telegram 測試 callback 尚未接入。"}
        return dict(callback(json_body(body)))

    @app.get("/api/v1/tailscale/status")
    def tailscale_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        callback = core._callbacks.get("tailscale_status")
        if callback is None:
            result = dict(core.snapshot().get("metrics", {}).get("tailscale", {"status": "not_checked"}))
        else:
            result = dict(callback())
        devices = result.get("devices") if isinstance(result.get("devices"), list) else []
        summary = app.state.live_viewer_tracker.snapshot(
            devices,
            app.state.tailscale_alias_store.snapshot(),
        )
        result["device_summary"] = {key: value for key, value in summary.items() if key != "devices"}
        # Do not return Tailscale IPs or stable identifiers in the general status
        # shape. The device details API is separately authenticated below.
        result.pop("devices", None)
        return result

    @app.get("/api/v1/tailscale/devices")
    def tailscale_devices(request: Request) -> dict[str, Any]:
        require_auth(request)
        callback = core._callbacks.get("tailscale_status")
        result = dict(callback()) if callback is not None else {}
        devices = result.get("devices") if isinstance(result.get("devices"), list) else []
        aliases = app.state.tailscale_alias_store.snapshot()
        summary = app.state.live_viewer_tracker.snapshot(devices, aliases)
        return {
            "status": result.get("status", "not_checked"),
            "device_list_available": bool(result.get("device_list_available", False)),
            "checked_at": result.get("checked_at"),
            "summary": summary,
        }

    @app.put("/api/v1/tailscale/devices/{device_ref}/alias")
    def tailscale_device_alias(request: Request, device_ref: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        callback = core._callbacks.get("tailscale_status")
        current = dict(callback()) if callback is not None else {}
        devices = current.get("devices") if isinstance(current.get("devices"), list) else []
        if not any(isinstance(item, dict) and item.get("device_ref") == device_ref for item in devices):
            raise HTTPException(status_code=404, detail="此裝置不在目前可確認的 Tailscale 清單中；別名未保存。")
        payload = json_body(body)
        if set(payload) != {"alias"}:
            raise HTTPException(status_code=400, detail="請提供本機顯示別名；清除時請明確傳入 null。")
        try:
            aliases = app.state.tailscale_alias_store.set_alias(device_ref, payload.get("alias"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": "saved", "aliases": aliases}

    @app.post("/api/v1/tailscale/setup")
    def tailscale_setup(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        callback = core._callbacks.get("tailscale_setup")
        if callback is None:
            return {"status": "not_checked", "message_zh_tw": "只提供官方 CLI 引導，尚未自動安裝或登入。"}
        return dict(callback(json_body(body)))

    @app.post("/api/v1/tailscale/serve")
    def tailscale_serve(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        if payload.get("funnel"):
            raise HTTPException(status_code=400, detail="MeowKeeper+ 不允許由此 API 開啟 Tailscale Funnel。")
        callback = core._callbacks.get("tailscale_serve")
        if callback is None:
            return {"status": "not_checked", "message_zh_tw": "Serve 僅能由使用者明確確認後操作。"}
        return dict(callback(payload))

    def integration_or_not_configured() -> Any:
        integration = getattr(app.state, "integrations", None)
        if integration is None:
            return None
        return integration

    @app.get("/api/v1/tray/status")
    def tray_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        return {"status": "ok", "host": core.health(), "tray": {"backend": "pystray", "localhost_only": True}}

    @app.post("/api/v1/tray/notify")
    def tray_notify(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        payload = json_body(body)
        message = str(payload.get("message_zh_tw", "")).strip()
        if not message or len(message) > 200:
            raise HTTPException(status_code=400, detail="Tray 通知內容不可為空白或過長。")
        return {"status": "accepted", "message_zh_tw": message}

    @app.get("/api/v1/integrations/go2rtc/status")
    def go2rtc_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        if integration is None:
            return {"state": "not_configured", "installed": False, "healthy": False, "message_zh_tw": "尚未建立 go2rtc 整合服務。"}
        return dict(integration.go2rtc.status().to_dict())

    @app.post("/api/v1/integrations/go2rtc/install")
    def go2rtc_install(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 go2rtc 整合服務。")
        return dict(integration.go2rtc.install().to_dict())

    @app.post("/api/v1/integrations/go2rtc/start")
    def go2rtc_start(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 go2rtc 整合服務。")
        return dict(integration.go2rtc.start().to_dict())

    @app.post("/api/v1/integrations/go2rtc/stop")
    def go2rtc_stop(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 go2rtc 整合服務。")
        return dict(integration.go2rtc.stop().to_dict())

    @app.post("/api/v1/integrations/go2rtc/restart")
    def go2rtc_restart(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 go2rtc 整合服務。")
        return dict(integration.go2rtc.restart().to_dict())

    @app.get("/api/v1/cameras")
    def cameras(request: Request) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        return {"items": integration.devices() if integration is not None else [], "setup": integration.status() if integration is not None else {"setup_stage": "not_installed"}}

    @app.get("/api/v1/cameras/xiaomi/setup/status")
    def xiaomi_setup_status(request: Request) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        return integration.status() if integration is not None else {"setup_stage": "not_installed", "message_zh_tw": "尚未建立 Xiaomi 設定服務。"}

    @app.post("/api/v1/cameras/xiaomi/setup/prepare")
    def xiaomi_prepare(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.prepare()

    @app.post("/api/v1/cameras/xiaomi/setup/open-login")
    def xiaomi_open_login(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.open_login()

    @app.post("/api/v1/cameras/xiaomi/setup/retry-login")
    def xiaomi_retry_login(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        retry = getattr(integration, "request_explicit_login_retry", None)
        if not callable(retry):
            raise HTTPException(status_code=501, detail="目前 Xiaomi 整合尚未提供可恢復登入流程。")
        return dict(retry())

    @app.post("/api/v1/cameras/xiaomi/setup/login-diagnostic")
    def xiaomi_login_diagnostic(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """Record a locally captured, secret-redacted go2rtc login result.

        The endpoint accepts evidence from the localhost integration only; the
        service masks the response body and stderr before writing state.  It
        never accepts or submits Xiaomi credentials.
        """

        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        record = getattr(integration, "record_login_diagnostic", None)
        if not callable(record):
            raise HTTPException(status_code=501, detail="目前 Xiaomi 整合尚未提供登入診斷流程。")
        payload = json_body(body)
        raw_status = payload.get("http_status")
        try:
            http_status = int(raw_status) if raw_status not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="HTTP status 格式不正確。") from exc
        return dict(
            record(
                body=payload.get("response_body", payload.get("body")),
                http_status=http_status,
                content_type=str(payload.get("content_type"))[:120] if payload.get("content_type") else None,
                stderr=payload.get("stderr", ""),
                login_error=payload.get("login_error"),
                network_error=payload.get("network_error"),
                phase=str(payload.get("phase") or "generic"),
                expected_uid=payload.get("expected_uid"),
            )
        )

    @app.post("/api/v1/cameras/xiaomi/setup/capture-login-diagnostic")
    def xiaomi_capture_login_diagnostic(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        capture = getattr(integration, "capture_go2rtc_login_diagnostic", None)
        go2rtc = getattr(integration, "go2rtc", None)
        if not callable(capture) or go2rtc is None:
            raise HTTPException(status_code=501, detail="目前 Xiaomi 整合尚未提供 localhost 登入診斷。")
        from meowkeeper.integrations.go2rtc.client import Go2RtcClient

        client = Go2RtcClient(base_url=str(getattr(go2rtc, "api_url", "http://127.0.0.1:1984")))
        return dict(capture(client))

    @app.post("/api/v1/cameras/xiaomi/setup/refresh")
    def xiaomi_refresh(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.refresh()

    @app.get("/api/v1/cameras/xiaomi/devices")
    def xiaomi_devices(request: Request) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        return {"items": integration.devices() if integration is not None else []}

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/connect", status_code=202)
    def xiaomi_connect(request: Request, device_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None or not hasattr(integration, "connection_jobs"):
            raise HTTPException(status_code=503, detail="接入工作尚未就緒。")
        try:
            return integration.connection_jobs.start(device_id, json_body(body).get("connection_mode", "local"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/v1/cameras/xiaomi/setup/operations/{job_id}")
    def xiaomi_connection_job(request: Request, job_id: str) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        if integration is None or not hasattr(integration, "connection_jobs"):
            raise HTTPException(status_code=503, detail="接入工作尚未就緒。")
        try:
            return integration.connection_jobs.snapshot(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def relay_provider_context():
        integration = integration_or_not_configured()
        provider = getattr(integration, "relay_provider", None)
        if provider is None:
            raise HTTPException(status_code=503, detail="此版本尚未提供同程式中繼。")
        from meowkeeper.integrations.xiaomi.relay_firewall import RelayFirewall
        provider.network = core._callbacks.get("tailscale_status", dict)
        firewall = RelayFirewall(integration.root, provider.network)
        provider.firewall = firewall.verified
        return provider, firewall

    @app.get("/api/v1/cameras/xiaomi/relay-provider")
    def xiaomi_relay_provider_status(request: Request):
        require_auth(request)
        provider, _ = relay_provider_context()
        return provider.status()

    @app.post("/api/v1/cameras/xiaomi/relay-provider/{action}")
    def xiaomi_relay_provider_action(request: Request, action: str, body: Any = Body(default=None)):  # noqa: B008
        require_mutation(request)
        provider, firewall = relay_provider_context()
        payload = json_body(body)
        try:
            if action in {"stop", "revoke"}:
                return provider.stop()
            if action == "firewall":
                peer = next((p for p in provider.network().get("devices", [])
                             if p.get("device_ref") == payload.get("peer_ref") and not p.get("is_self")), {})
                ip = next((ip for ip in peer.get("tailnet_ips", []) if ":" not in ip), None)
                if not ip:
                    raise ValueError("請先選擇允許連線的 Tailscale 電腦。")
                return firewall.authorize(ip)
            if action == "enable":
                # Work runs outside the request. The single provider busy flag
                # prevents double-clicks from opening extra servers/producers.
                if provider.running or provider.busy:
                    return provider.status()
                def enable_provider():
                    try:
                        result = provider.enable(str(payload.get("device_id") or ""), str(payload.get("peer_ref") or ""))
                        with provider.lock:
                            provider.pending_code = result.get("pairing_code")
                            provider.pending_until = __import__("time").time() + 600
                    except (OSError, RuntimeError, ValueError) as exc:
                        provider.message = str(exc) if isinstance(exc, ValueError) else "中繼準備失敗，請檢查本機連線與防火牆。"
                with provider.lock:
                    if getattr(provider, "starting", False):
                        return provider.status()
                    provider.starting = True
                    generation = provider.generation
                def run_provider():
                    try:
                        if generation == provider.generation:
                            enable_provider()
                    finally:
                        provider.starting = False
                threading.Thread(target=run_provider, daemon=True, name="relay-prepare").start()
                return {"success": False, "busy": True, "message_zh_tw": "正在驗證畫面並準備中繼…"}
            if action == "pairing":
                import time
                with provider.lock:
                    code = getattr(provider, "pending_code", "")
                    if not provider.running or not code or time.time() >= getattr(provider, "pending_until", 0):
                        raise ValueError("配對碼已過期或已領取；請停止後重新啟用中繼產生新碼。")
                    provider.pending_code = ""
                    return {"success": True, "pairing_code": code, "expires_in_seconds": max(0, int(provider.pending_until - time.time()))}
            raise ValueError("不支援的中繼操作。")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc) if isinstance(exc, ValueError) else "中繼操作未完成。") from None

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/probe")
    def xiaomi_probe(request: Request, device_id: str) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.probe_device(device_id)

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/firewall-diagnostic")
    def xiaomi_firewall_diagnostic(request: Request, device_id: str) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        diagnose = getattr(integration, "firewall_diagnostic", None)
        if not callable(diagnose):
            raise HTTPException(status_code=501, detail="目前版本尚未提供小米 UDP 防火牆診斷。")
        return dict(diagnose(device_id))

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/activate")
    def xiaomi_activate(request: Request, device_id: str) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.activate(device_id)

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/remote-pair")
    def xiaomi_remote_pair(request: Request, device_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        payload = json_body(body)
        code = payload.get("pairing_code")
        if not isinstance(code, str) or len(code) > 4096:
            raise HTTPException(status_code=400, detail="遠端配對碼格式不正確。")
        return integration.pair_remote(device_id, code)

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/activate-remote")
    def xiaomi_activate_remote(request: Request, device_id: str) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.activate_remote(device_id)

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/profile")
    def xiaomi_live_profile(request: Request, device_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """Switch the active Xiaomi stream profile in place.

        This endpoint deliberately accepts only the four bounded live-view
        choices.  It never accepts a source URL and never starts another
        camera ingest.
        """

        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        switch_profile = getattr(integration, "set_live_profile", None)
        if not callable(switch_profile):
            raise HTTPException(status_code=503, detail="目前版本尚未提供 Xiaomi 即時畫質切換。")
        payload = json_body(body)
        return dict(switch_profile(device_id, str(payload.get("profile") or "")))

    @app.get("/api/v1/cameras/xiaomi/control/diagnostics")
    def xiaomi_control_diagnostics(request: Request, operation_id: str | None = None) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        read = getattr(integration, "control_diagnostics", None)
        if not callable(read):
            return {"items": [], "retained_count": 0, "limit": 50, "persistent": False,
                    "message_zh_tw": "目前 Host 尚未提供攝影機控制診斷紀錄。"}
        return dict(read(operation_id=operation_id))

    @app.get("/api/v1/cameras/xiaomi/devices/{device_id}/ptz")
    def xiaomi_ptz_status(request: Request, device_id: str, refresh: bool = False,
                          operation_id: str | None = None) -> dict[str, Any]:
        """Read optional Xiaomi MISS PTZ state through the authenticated Dashboard."""

        require_auth(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        read_state = getattr(integration, "ptz_status", None)
        if not callable(read_state):
            return {"status": "unavailable", "success": False, "ptz_available": False, "message_zh_tw": "目前版本尚未提供 Xiaomi MISS PTZ。"}
        if operation_id:
            return dict(read_state(device_id, refresh=refresh, operation_id=operation_id))
        return dict(read_state(device_id, refresh=refresh))

    @app.get("/api/v1/cameras/xiaomi/positions/diagnostic")
    def xiaomi_position_diagnostic(request: Request) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        adapter = getattr(integration, "privacy_adapter", None)
        path = getattr(adapter, "path", None)
        if adapter is None or not isinstance(path, (str, Path)):
            return {"status": "unavailable", "message_zh_tw": "目前 Host 未接上位置控制服務。"}
        from meowkeeper.core.notifications.position_presets import PositionPresetService

        return PositionPresetService(adapter, Path(path).with_name("telegram-position-presets.json")).diagnostic()

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/ptz")
    def xiaomi_ptz_action(request: Request, device_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        """Execute one explicit, CSRF-protected Xiaomi MISS PTZ action."""

        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        send_action = getattr(integration, "ptz_action", None)
        if not callable(send_action):
            return {"status": "unavailable", "success": False, "ptz_available": False, "message_zh_tw": "目前版本尚未提供 Xiaomi MISS PTZ。"}
        payload = json_body(body)
        arguments = {
            "action": str(payload.get("action") or ""),
            "direction": str(payload.get("direction")) if payload.get("direction") is not None else None,
            "angle": payload.get("angle"),
            "elevation": payload.get("elevation"),
        }
        if payload.get("operation_id") is not None:
            arguments["operation_id"] = str(payload.get("operation_id"))
        return dict(send_action(device_id, **arguments))

    @app.post("/api/v1/cameras/xiaomi/devices/{device_id}/privacy")
    def xiaomi_privacy_action(request: Request, device_id: str, body: Any = Body(default=None)) -> dict[str, Any]:  # noqa: B008
        require_mutation(request)
        integration = integration_or_not_configured()
        action = json_body(body)
        operation = getattr(integration, "privacy_action", None)
        if not callable(operation):
            raise HTTPException(status_code=503, detail="尚未提供 Xiaomi MISS 隱私位置服務。")
        if action.get("action") == "capture" and action.get("visually_confirmed") is not True:
            raise HTTPException(status_code=400, detail="請先確認目前畫面已避開私人區域。")
        return dict(operation(device_id, str(action.get("action") or ""), mode=action.get("mode")))

    @app.get("/api/v1/cameras/xiaomi/devices/{device_id}/privacy")
    def xiaomi_privacy_configuration(request: Request, device_id: str) -> dict[str, Any]:
        require_auth(request)
        integration = integration_or_not_configured()
        read = getattr(integration, "privacy_configuration", None)
        if not callable(read):
            raise HTTPException(status_code=503, detail="尚未提供 Xiaomi 隱私設定。")
        try:
            return dict(read(device_id))
        except (OSError, ValueError, TypeError, RuntimeError):
            raise HTTPException(status_code=503, detail="隱私設定暫時不可讀取。") from None

    @app.post("/api/v1/cameras/xiaomi/disconnect")
    def xiaomi_disconnect(request: Request) -> dict[str, Any]:
        require_mutation(request)
        integration = integration_or_not_configured()
        if integration is None:
            raise HTTPException(status_code=503, detail="尚未建立 Xiaomi 設定服務。")
        return integration.disconnect()

    @app.get("/api/v1/diagnostics/export")
    def diagnostics_export(request: Request) -> Any:
        require_auth(request)
        return JSONResponse(content=core.diagnostics())

    @app.get("/api/v1/diagnostics")
    def diagnostics(request: Request) -> dict[str, Any]:
        require_auth(request)
        return core.diagnostics()

    @app.get("/api/v1/logs")
    def recent_logs(request: Request) -> dict[str, Any]:
        require_auth(request)
        snapshot = getattr(app.state, "log_snapshot", None)
        return {"items": snapshot()[-200:] if callable(snapshot) else [], "source": "host_memory"}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/live", response_class=HTMLResponse)
    @app.get("/events", response_class=HTMLResponse)
    @app.get("/analytics", response_class=HTMLResponse)
    @app.get("/settings/source", response_class=HTMLResponse)
    @app.get("/settings/ai", response_class=HTMLResponse)
    @app.get("/settings/pets", response_class=HTMLResponse)
    @app.get("/settings/telegram", response_class=HTMLResponse)
    @app.get("/settings/tailscale", response_class=HTMLResponse)
    @app.get("/cameras", response_class=HTMLResponse)
    @app.get("/cameras/xiaomi/setup", response_class=HTMLResponse)
    @app.get("/diagnostics", response_class=HTMLResponse)
    @app.get("/logs", response_class=HTMLResponse)
    @app.get("/settings/advanced", response_class=HTMLResponse)
    @app.get("/help", response_class=HTMLResponse)
    def dashboard_page(request: Request) -> Any:
        path = str(request.url.path)
        if path == "/help":
            require_auth(request)
        protected_pages = {"/", "/dashboard", "/live", "/events", "/analytics", "/settings/source", "/settings/ai", "/settings/pets", "/settings/telegram", "/settings/tailscale", "/diagnostics", "/logs", "/settings/advanced", "/cameras", "/cameras/xiaomi/setup"}
        if path in protected_pages or path == "/help":
            if setup_redirect and not sessions.configured:
                return RedirectResponse("/setup", status_code=307)
            if sessions.configured and session_from_request(request) is None:
                return RedirectResponse("/login", status_code=307)
        if path == "/cameras":
            return RedirectResponse("/settings/source", status_code=307)
        title_map = {
            "/help": "使用教學",
            "/": "總覽",
            "/dashboard": "總覽",
            "/live": "即時畫面",
            "/events": "事件與回報",
            "/analytics": "趨勢分析",
            "/settings/source": "來源與攝影機",
            "/settings/ai": "AI 與錄影",
            "/settings/pets": "寵物檔案",
            "/settings/telegram": "通知設定",
            "/settings/tailscale": "連線與安全",
            "/cameras": "攝影機",
            "/cameras/xiaomi/setup": "小米攝影機設定",
            "/diagnostics": "維護中心",
            "/logs": "日誌",
            "/settings/advanced": "進階設定",
        }
        template_map = {
            "/help": "help.html",
            "/live": "live.html",
            "/events": "events.html",
            "/analytics": "analytics.html",
            "/diagnostics": "diagnostics.html",
            "/logs": "logs.html",
            "/settings/source": "settings.html",
            "/settings/ai": "settings.html",
            "/settings/pets": "settings.html",
            "/settings/telegram": "settings.html",
            "/settings/tailscale": "settings.html",
            "/cameras": "cameras.html",
            "/cameras/xiaomi/setup": "xiaomi_setup.html",
            "/settings/advanced": "settings.html",
        }
        template_name = template_map.get(path, "dashboard.html")
        template_file = templates_path / template_name
        if not template_file.is_file():
            template_name = "dashboard.html"
        return app.state.templates.TemplateResponse(
            request=request,
            name=template_name,
            context={"page_title": title_map.get(path, "總覽"), "page_path": path},
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Any:
        return app.state.templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"page_title": "登入"},
        )

    return app


__all__ = ["create_app"]
