"""可配置 normalized 監控區域。"""

from __future__ import annotations

from collections.abc import Iterable

from .models import ZoneDefinition


class ZoneRegistry:
    def __init__(self, zones: Iterable[ZoneDefinition] = ()) -> None:
        self._zones: dict[str, ZoneDefinition] = {}
        for zone in zones:
            self.add(zone)

    def add(self, zone: ZoneDefinition) -> None:
        zone.validate()
        self._zones[zone.zone_id] = zone

    def remove(self, zone_id: str) -> None:
        self._zones.pop(zone_id, None)

    def get(self, zone_id: str) -> ZoneDefinition | None:
        return self._zones.get(zone_id)

    def list(self) -> tuple[ZoneDefinition, ...]:
        return tuple(self._zones.values())

    def classify_center(self, center_normalized: tuple[float, float]) -> tuple[ZoneDefinition, ...]:
        return tuple(zone for zone in self._zones.values() if zone.contains_center(center_normalized))
