"""少量跨模組設定契約；正式設定載入器將於 V0.1 實作。"""

from dataclasses import dataclass

COCO_CAT_CLASS_ID = 15


@dataclass(frozen=True)
class VisionAnalysisSettings:
    """V0.1 YOLO 貓咪偵測的可配置契約。"""

    cat_class_id: int = COCO_CAT_CLASS_ID
    confidence_threshold: float = 0.45
    input_size: int = 640

    def validate(self) -> None:
        if self.cat_class_id != COCO_CAT_CLASS_ID:
            raise ValueError("V0.1 只允許使用 COCO cat class id 15。")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("YOLO 信心門檻必須介於 0 到 1。")
        if self.input_size not in {640, 960, 1280, 1920}:
            raise ValueError("YOLO 輸入長邊只支援 640／960／1280／1920；MX250 預設仍為 640。")


DEFAULT_VISION_ANALYSIS = VisionAnalysisSettings()


@dataclass(frozen=True)
class MediaBufferSettings:
    """環形快取與 Telegram `/live` 片段設定。"""

    ring_buffer_seconds: int = 60
    live_pre_seconds: int = 5
    live_post_seconds: int = 0
    segment_seconds: float = 1.0
    storage_mode: str = "disk"
    temp_directory: str = "data/cache/ring"
    maximum_size_mb: int = 512
    prefer_stream_copy: bool = True
    event_pre_seconds: int = 5
    event_post_seconds: int = 10

    def validate(self) -> None:
        if not 10 <= self.ring_buffer_seconds <= 120:
            raise ValueError("環形影音快取總長度必須介於 10 到 120 秒。")
        if not 0 <= self.live_pre_seconds <= 30:
            raise ValueError("/live 預錄長度必須介於 0 到 30 秒。")
        if not 0 <= self.live_post_seconds <= 30:
            raise ValueError("/live 後錄長度必須介於 0 到 30 秒。")
        if self.segment_seconds <= 0 or self.segment_seconds > 10:
            raise ValueError("影音分段長度必須大於零且不超過 10 秒。")
        if self.storage_mode != "disk":
            raise ValueError("目前只支援 disk 壓縮分段快取。")
        if not self.temp_directory.strip():
            raise ValueError("環形快取暫存目錄不可為空白。")
        if self.maximum_size_mb <= 0:
            raise ValueError("環形快取容量上限必須大於零。")
        if self.event_pre_seconds < 0 or self.event_post_seconds < 0:
            raise ValueError("事件前錄與後錄秒數不可小於零。")
        if self.event_pre_seconds > self.ring_buffer_seconds:
            raise ValueError("事件前錄秒數不可大於環形影音快取總長度。")
        if self.live_pre_seconds > self.ring_buffer_seconds:
            raise ValueError("/live 預錄長度不可大於環形影音快取總長度。")


@dataclass(frozen=True)
class PerformanceProfile:
    """產品最低硬體與預設推論負載契約。"""

    name: str
    reference_gpu: str
    minimum_ram_gib: float
    recommended_ram_gib: float
    minimum_vram_mib: int
    max_visual_models: int
    model_batch_size: int
    inference_input_size: int
    analysis_fps: int
    preferred_backend: str
    fallback_backend: str
    identity_sampling_fps: float = 1.0
    behavior_sampling_hz: float = 1.0
    conditional_identity: bool = True
    normal_yolo_fps: float = 3.0
    active_yolo_fps: float = 5.0
    quiet_yolo_fps: float = 1.0
    active_mode_seconds: float = 30.0
    identity_recheck_interval_seconds: float = 10.0
    identity_max_pending_tasks: int = 2
    identity_timeout_seconds: float = 3.0
    identity_minimum_crop_pixels: int = 96

    def validate(self) -> None:
        if not self.name:
            raise ValueError("效能 profile 必須有名稱。")
        if self.minimum_ram_gib < 8:
            raise ValueError("最低系統記憶體預算不得低於約 8.59 GB。")
        if self.recommended_ram_gib < self.minimum_ram_gib:
            raise ValueError("建議記憶體不得低於最低記憶體。")
        if self.minimum_vram_mib < 2048:
            raise ValueError("MX250 基線的 VRAM 預算不得低於約 2147 MB。")
        if self.max_visual_models != 1:
            raise ValueError("MX250 基線一次只能載入一個視覺模型。")
        if self.model_batch_size != 1:
            raise ValueError("MX250 基線的推論 batch 必須為 1。")
        if self.inference_input_size > 640:
            raise ValueError("MX250 基線的預設推論輸入不得大於 640。")
        if self.analysis_fps > 5:
            raise ValueError("MX250 基線的預設 AI 採樣不得高於 5 FPS。")
        if self.identity_sampling_fps <= 0 or self.identity_sampling_fps > 1:
            raise ValueError("MX250 基線的身分 embedding 採樣不得高於 1 FPS。")
        if self.behavior_sampling_hz <= 0 or self.behavior_sampling_hz > 1:
            raise ValueError("MX250 基線的行為彙整頻率不得高於 1 Hz。")
        if not 1 <= self.quiet_yolo_fps <= self.normal_yolo_fps <= self.active_yolo_fps <= 5:
            raise ValueError("動態 YOLO FPS 必須滿足 1 <= quiet <= normal <= active <= 5。")
        if self.active_mode_seconds <= 0:
            raise ValueError("活動增強模式持續時間必須大於零。")
        if self.identity_recheck_interval_seconds <= 0:
            raise ValueError("身分複核間隔必須大於零。")
        if self.identity_max_pending_tasks < 1:
            raise ValueError("身分佇列長度必須至少為 1。")
        if self.identity_timeout_seconds <= 0 or self.identity_minimum_crop_pixels < 1:
            raise ValueError("身分工作逾時與 crop 尺寸設定不正確。")


MX250_MINIMUM = PerformanceProfile(
    name="mx250_minimum",
    reference_gpu="NVIDIA GeForce MX250",
    minimum_ram_gib=8.0,
    recommended_ram_gib=16.0,
    minimum_vram_mib=2048,
    max_visual_models=1,
    model_batch_size=1,
    inference_input_size=640,
    analysis_fps=5,
    preferred_backend="onnxruntime_cuda",
    fallback_backend="cpu",
    identity_sampling_fps=1.0,
    behavior_sampling_hz=1.0,
    conditional_identity=True,
    normal_yolo_fps=3.0,
    active_yolo_fps=5.0,
    quiet_yolo_fps=1.0,
    active_mode_seconds=30.0,
    identity_recheck_interval_seconds=10.0,
    identity_max_pending_tasks=2,
    identity_timeout_seconds=3.0,
    identity_minimum_crop_pixels=96,
)

PERFORMANCE_PROFILES = {MX250_MINIMUM.name: MX250_MINIMUM}


def get_performance_profile(name: str) -> PerformanceProfile:
    """依穩定名稱取得 profile，未知名稱不得靜默回退。"""

    try:
        profile = PERFORMANCE_PROFILES[name]
    except KeyError as exc:
        supported = ", ".join(sorted(PERFORMANCE_PROFILES))
        raise ValueError(f"未知效能 profile：{name}。可用值：{supported}") from exc
    profile.validate()
    return profile
