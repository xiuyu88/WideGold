from enum import StrEnum


class TriggerType(StrEnum):
    SCHEDULED = "SCHEDULED"
    ADMIN_MANUAL = "ADMIN_MANUAL"
    SYSTEM_RETRY = "SYSTEM_RETRY"
    REPLAY = "REPLAY"


class AnalysisRunMode(StrEnum):
    FULL_REFRESH = "FULL_REFRESH"
    REANALYZE = "REANALYZE"
    DATA_ONLY = "DATA_ONLY"
    REPLAY = "REPLAY"


class PublishMode(StrEnum):
    AUTO = "AUTO"
    PREVIEW_ONLY = "PREVIEW_ONLY"
    EXPLICIT_PUBLISH = "EXPLICIT_PUBLISH"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DATA_READY = "DATA_READY"
    EVENTS_READY = "EVENTS_READY"
    FACTORS_READY = "FACTORS_READY"
    SCORED = "SCORED"
    QUALITY_FAILED = "QUALITY_FAILED"
    PREVIEW_READY = "PREVIEW_READY"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class DataStatus(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIAL = "PARTIAL"
    CONFLICTED = "CONFLICTED"


class SourceTier(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class Horizon(StrEnum):
    TACTICAL = "tactical"
    SWING = "swing"
    STRATEGIC = "strategic"


class EventVerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ModelTier(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
