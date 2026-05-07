"""Sensor platform for IQAir Cloud."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import IQAirDataUpdateCoordinator


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Return a nested value from a dictionary."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_datetime(value: Any) -> datetime | None:
    """Convert an API timestamp to a datetime for Home Assistant."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_filter_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the best available filter-maintenance list."""
    for path in (
        ("filtersDetails",),
        ("filterMaintenance",),
        ("remote", "filters"),
    ):
        filters = _get_nested(data, path)
        if isinstance(filters, list):
            return [item for item in filters if isinstance(item, dict)]
    return []


@dataclass(frozen=True, kw_only=True)
class IQAirSensorEntityDescription(SensorEntityDescription):
    """Describes an IQAir sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_TYPES: tuple[IQAirSensorEntityDescription, ...] = (
    IQAirSensorEntityDescription(
        key="pm25",
        name="PM2.5",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _get_nested(data, ("current", "pm25", "value")),
        extra_attrs_fn=lambda data: {
            "aqi": _get_nested(data, ("current", "pm25", "aqi")),
            "label": _get_nested(data, ("current", "pm25", "label")),
            "color": _get_nested(data, ("current", "pm25", "color")),
        },
    ),
    IQAirSensorEntityDescription(
        key="aqi",
        name="AQI",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _get_nested(data, ("current", "aqi", "value")),
        extra_attrs_fn=lambda data: {
            "label": _get_nested(data, ("current", "aqi", "label")),
            "color": _get_nested(data, ("current", "aqi", "color")),
        },
    ),
    IQAirSensorEntityDescription(
        key="particle_count",
        name="Particle Count",
        icon="mdi:dots-hexagon",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _get_nested(data, ("current", "pc", "value")),
    ),
    IQAirSensorEntityDescription(
        key="clean_air_delivery_percent",
        name="Clean Air Delivery",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:fan-chevron-up",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _get_nested(
            data, ("performance", "cleanAirDeliveryRatePercent")
        ),
        extra_attrs_fn=lambda data: {
            "clean_air_delivery_rate": _get_nested(
                data, ("performance", "cleanAirDeliveryRate")
            ),
        },
    ),
    IQAirSensorEntityDescription(
        key="cumulative_air_volume",
        name="Cumulative Air Volume",
        icon="mdi:gauge",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _get_nested(data, ("performance", "totCumAirVolume")),
    ),
    IQAirSensorEntityDescription(
        key="fan_runtime",
        name="Fan Runtime",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _get_nested(data, ("performance", "totTimeFanRun")),
    ),
    IQAirSensorEntityDescription(
        key="wifi_signal",
        name="Wi-Fi Signal",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _get_nested(data, ("connectivity", "percentage")),
        extra_attrs_fn=lambda data: {
            "type": _get_nested(data, ("connectivity", "type")),
        },
    ),
    IQAirSensorEntityDescription(
        key="last_seen",
        name="Last Seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _as_datetime(data.get("lastSeenAt")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the IQAir sensor entities."""
    coordinator: IQAirDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    device_id = entry.data[CONF_DEVICE_ID]

    entities: list[SensorEntity] = [
        IQAirSensor(coordinator, device_id, entry, description)
        for description in SENSOR_TYPES
    ]

    filters = _first_filter_list(coordinator.data or {})
    entities.extend(
        IQAirFilterHealthSensor(coordinator, device_id, entry, filter_info)
        for filter_info in filters
        if filter_info.get("slot") is not None
    )

    async_add_entities(entities)


class IQAirSensor(CoordinatorEntity[IQAirDataUpdateCoordinator], SensorEntity):
    """Representation of an IQAir Cloud sensor."""

    entity_description: IQAirSensorEntityDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: IQAirDataUpdateCoordinator,
        device_id: str,
        entry: ConfigEntry,
        description: IQAirSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": entry.title,
            "manufacturer": "IQAir",
            "model": (
                self.coordinator.data.get("modelLabel")
                if self.coordinator.data
                else None
            ),
        }

    @property
    def native_value(self) -> Any:
        """Return the sensor state."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data or not self.entity_description.extra_attrs_fn:
            return None
        attrs = self.entity_description.extra_attrs_fn(self.coordinator.data)
        return {key: value for key, value in attrs.items() if value is not None}


class IQAirFilterHealthSensor(
    CoordinatorEntity[IQAirDataUpdateCoordinator], SensorEntity
):
    """Representation of an IQAir Cloud filter health sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:air-filter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IQAirDataUpdateCoordinator,
        device_id: str,
        entry: ConfigEntry,
        filter_info: dict[str, Any],
    ) -> None:
        """Initialize the filter health sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._slot = filter_info["slot"]
        self._attr_name = self._filter_name(filter_info)
        self._attr_unique_id = f"{device_id}_filter_{self._slot}_health"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": entry.title,
            "manufacturer": "IQAir",
            "model": (
                self.coordinator.data.get("modelLabel")
                if self.coordinator.data
                else None
            ),
        }

    def _current_filter(self) -> dict[str, Any] | None:
        """Return the current filter info for this slot."""
        if not self.coordinator.data:
            return None
        for filter_info in _first_filter_list(self.coordinator.data):
            if filter_info.get("slot") == self._slot:
                return filter_info
        return None

    @staticmethod
    def _filter_name(filter_info: dict[str, Any]) -> str:
        """Return a friendly filter sensor name."""
        filter_type = filter_info.get("filterType")
        if isinstance(filter_type, str) and filter_type:
            return f"{filter_type} Health"

        mediums = filter_info.get("filterMediums")
        if isinstance(mediums, list) and mediums:
            medium = mediums[0]
            if isinstance(medium, str) and medium:
                return f"{medium} Health"

        return f"Filter {filter_info.get('slot')} Health"

    @property
    def native_value(self) -> int | float | None:
        """Return the filter health percentage."""
        filter_info = self._current_filter()
        if not filter_info:
            return None
        return filter_info.get("healthPercent")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        filter_info = self._current_filter()
        if not filter_info:
            return None
        attrs = {
            "slot": filter_info.get("slot"),
            "filter_mediums": filter_info.get("filterMediums"),
            "filter_type": filter_info.get("filterType"),
            "filter_level": filter_info.get("filterLevel"),
            "health_label": filter_info.get("healthLabel"),
            "article_number": filter_info.get("filterArticleNumber"),
            "is_filter_undetected": filter_info.get("isFilterUndetected"),
            "last_reset_date": filter_info.get("lastResetDate"),
            "used_since": filter_info.get("usedSince"),
            "filter_runtime": filter_info.get("filterRuntime"),
        }
        return {key: value for key, value in attrs.items() if value is not None}
