"""多貓身分登錄、特徵與時序判斷。"""

from .background_resolver import BackgroundIdentityResolver, IdentityResolution
from .base import EmbeddingBackend, IdentityPrediction, IdentityThresholds
from .dinov2_embedder import DinoV2Embedder, DINOv2Unavailable
from .identity_classifier import IdentityClassifier
from .pet_registry import PetProfile, PetRegistry
from .quality_filter import QualityResult, ReferenceQualityFilter
from .temporal_identity import TemporalIdentity

__all__ = [
    "BackgroundIdentityResolver",
    "DINOv2Unavailable",
    "DinoV2Embedder",
    "EmbeddingBackend",
    "IdentityClassifier",
    "IdentityPrediction",
    "IdentityResolution",
    "IdentityThresholds",
    "PetProfile",
    "PetRegistry",
    "QualityResult",
    "ReferenceQualityFilter",
    "TemporalIdentity",
]
