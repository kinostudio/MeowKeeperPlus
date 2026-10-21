"""單一媒體入口、時間戳、環形快取與片段組裝。"""

from .buffers import CompressedRingBuffer, CompressedSegment, LatestFrameQueue
from .clip_assembler import ClipAssembler, ClipAssemblyResult
from .event_clip import EventClipRequest, EventClipService, LiveClipResult
from .ingest import FfmpegMediaIngest
from .media_validation import MediaValidationResult, assert_valid_media, validate_media
from .ring_buffer import DiskSegmentRingBuffer, RingBufferStatus
from .segment_index import SegmentIndex, SegmentRecord
from .segment_recorder import SegmentRecorder
from .sources import (
    BackoffPolicy,
    FfmpegFrameSource,
    FramePacket,
    MediaSourceError,
    ResolvedStream,
    YouTubeStreamResolver,
    is_youtube_url,
    iter_jpeg_frames,
    resolve_source,
)
from .youtube import (
    YOUTUBE_MAX_HEIGHTS,
    YOUTUBE_RECONNECT_BACKOFF_SECONDS,
    YTDLP_EJS_PINNED_VERSION,
    YTDLP_PINNED_VERSION,
    YouTubeMediaInfo,
    YouTubeRuntimeCheck,
    YouTubeSourceAdapter,
    YouTubeSourceConfig,
    YouTubeSourceStatus,
    YouTubeUnsupportedError,
    check_youtube_runtime,
)

__all__ = [
    "YOUTUBE_MAX_HEIGHTS",
    "YOUTUBE_RECONNECT_BACKOFF_SECONDS",
    "YTDLP_EJS_PINNED_VERSION",
    "YTDLP_PINNED_VERSION",
    "BackoffPolicy",
    "ClipAssembler",
    "ClipAssemblyResult",
    "CompressedRingBuffer",
    "CompressedSegment",
    "DiskSegmentRingBuffer",
    "EventClipRequest",
    "EventClipService",
    "FfmpegFrameSource",
    "FfmpegMediaIngest",
    "FramePacket",
    "LatestFrameQueue",
    "LiveClipResult",
    "MediaSourceError",
    "MediaValidationResult",
    "ResolvedStream",
    "RingBufferStatus",
    "SegmentIndex",
    "SegmentRecord",
    "SegmentRecorder",
    "YouTubeMediaInfo",
    "YouTubeRuntimeCheck",
    "YouTubeSourceAdapter",
    "YouTubeSourceConfig",
    "YouTubeSourceStatus",
    "YouTubeStreamResolver",
    "YouTubeUnsupportedError",
    "assert_valid_media",
    "check_youtube_runtime",
    "is_youtube_url",
    "iter_jpeg_frames",
    "resolve_source",
    "validate_media",
]
