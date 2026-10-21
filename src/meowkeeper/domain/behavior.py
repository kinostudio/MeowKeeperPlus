"""跨 GUI、服務、資料庫與分析模組共用的行為列舉。"""

from enum import Enum


class BehaviorState(str, Enum):
    """同一隻寵物在同一時間只能有一個主要行為。"""

    SLEEPING = "sleeping"
    RESTING = "resting"
    EATING = "eating"
    DRINKING = "drinking"
    PLAYING = "playing"
    GROOMING = "grooming"
    LITTER_BOX = "litter_box"
    OTHER_ACTIVE = "other_active"
    OUT_OF_VIEW = "out_of_view"
    UNKNOWN = "unknown"


class MotionState(str, Enum):
    """可與主要行為並存的動作狀態。"""

    STATIONARY = "stationary"
    LOW_MOTION = "low_motion"
    WALKING = "walking"
    RUNNING = "running"
    JUMPING = "jumping"


class EventTag(str, Enum):
    """同一時間可有零到多個的附加事件。"""

    CHASING = "chasing"
    CAT_INTERACTION = "cat_interaction"
    MEOW = "meow"
    CATERWAUL = "caterwaul"
    RETCHING = "retching"
    POSSIBLE_VOMIT = "possible_vomit"
    ZONE_ENTER = "zone_enter"
    ZONE_EXIT = "zone_exit"
