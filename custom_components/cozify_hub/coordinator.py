"""DataUpdateCoordinator for Cozify HUB."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CozifyHubApi, CozifyHubApiError, CozifyHubAuthError
from .const import (
    CONF_CLOUD_TOKEN,
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    DEFAULT_SCAN_INTERVAL_CLOUD,
    DEFAULT_SCAN_INTERVAL_LOCAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CozifyHubCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage Cozify HUB data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: CozifyHubApi,
        hub_name: str,
        connection_mode: str,
        entry: ConfigEntry,
    ) -> None:
        self.api = api
        self._entry = entry
        self._connection_mode = connection_mode
        interval = (DEFAULT_SCAN_INTERVAL_LOCAL
                    if connection_mode == CONNECTION_MODE_LOCAL
                    else DEFAULT_SCAN_INTERVAL_CLOUD)
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{hub_name}",
                         update_interval=interval)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data, refreshing cloud token once on auth failure."""
        try:
            return await self._fetch_data()
        except CozifyHubAuthError as err:
            if self._connection_mode == CONNECTION_MODE_CLOUD:
                try:
                    new_token = await self.api.refresh_cloud_token()
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={**self._entry.data, CONF_CLOUD_TOKEN: new_token},
                    )
                    _LOGGER.info("Cloud token refreshed successfully")
                    return await self._fetch_data()
                except Exception as refresh_err:
                    _LOGGER.warning("Token refresh failed: %s", refresh_err)
            from homeassistant.exceptions import ConfigEntryAuthFailed
            raise ConfigEntryAuthFailed(f"Token expired: {err}") from err
        except CozifyHubApiError as err:
            raise UpdateFailed(f"Cozify HUB error: {err}") from err

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch all data from hub in parallel."""
        devices, rooms, scenes, groups, alarms = await asyncio.gather(
            self.api.get_devices(),
            self.api.get_rooms(),
            self.api.get_scenes(),
            self.api.get_groups(),
            self.api.get_alarms(),
        )

        room_names = {
            rid: rdata.get("name", rid)
            for rid, rdata in (rooms or {}).items()
        }

        normalized = {}
        for device_id, device_data in (devices or {}).items():
            normalized[device_id] = self._normalize(device_id, device_data, room_names)

        return {
            "devices": normalized,
            "rooms": rooms or {},
            "scenes": scenes or {},
            "groups": groups or {},
            "alarms": alarms or {},
        }

    def _normalize(self, device_id: str, d: dict[str, Any],
                   room_names: dict[str, str]) -> dict[str, Any]:
        """Normalize device data from hub API response."""
        state = d.get("state", {})

        # Capabilities: list or dict with "values"
        caps = d.get("capabilities", [])
        if isinstance(caps, dict):
            caps = caps.get("values", [])

        # On/off state
        is_on = state.get("isOn") or state.get("on", False)

        # room field can be a list ["room_id"] or string "room_id"
        room_raw = d.get("room")
        if isinstance(room_raw, list):
            room_id = room_raw[0] if room_raw else None
        else:
            room_id = room_raw
        room_name = room_names.get(room_id) if room_id else None

        return {
            "id": device_id,
            "name": d.get("name", device_id),
            "type": d.get("type"),
            "manufacturer": d.get("manufacturer"),
            "model": d.get("model"),
            "room_id": room_id,
            "room_name": room_name,
            "capabilities": caps,
            "reachable": state.get("reachable", True),
            "last_seen": state.get("lastSeen"),
            "is_on": is_on,
            # Light
            "brightness": state.get("brightness"),       # 0.0–1.0
            "hue": state.get("hue"),                     # radians
            "saturation": state.get("saturation"),       # 0.0–1.0
            "color_mode": state.get("colorMode"),
            "color_temperature": state.get("temperature"),  # Kelvin (lights)
            # Sensors
            "temperature": state.get("temperature"),     # °C (sensors)
            "humidity": state.get("humidity"),
            "pressure": state.get("pressure"),
            "lux": state.get("lux"),
            "co2_ppm": state.get("co2Ppm"),
            "voc_ppm": state.get("vocPpm"),
            "rssi": state.get("rssi"),
            # Binary sensors
            "motion": state.get("motion"),
            "last_motion": state.get("lastMotion"),
            "open": state.get("open"),
            "alert": state.get("alert"),
            "moisture": state.get("moisture"),
            "twilight": state.get("twilight"),
            "low_temp": state.get("lowTemp"),
            "co_detected": state.get("coDetected"),
            "siren_on": state.get("sirenOn"),
            # Power
            "power": state.get("power"),
            "active_power": state.get("activePower"),
            "total_power": state.get("totalPower"),
            "power_today": state.get("powerToday"),
            # Battery
            "battery": state.get("battery"),
            "battery_v": state.get("batteryV"),
            "battery_low": state.get("batteryLow"),
            # Climate
            "target_temperature": state.get("targetTemperature"),
            "heating_demand": state.get("heatingDemand"),
            "hvac_mode": state.get("mode"),
            "hvac_preset": state.get("preset"),
            # Cover
            "position": state.get("position"),           # 0.0–1.0
            "lift_pct": state.get("liftPct"),            # 0–100
            "tilt_pct": state.get("tiltPct"),            # 0–100
            # Fan / VU
            "fan_mode": state.get("fanMode"),
            "ventilation_mode": state.get("mode"),
            "fresh_temperature": state.get("freshTemperature"),
            "supply_temperature": state.get("supplyTemperature"),
            "extract_temperature": state.get("extractTemperature"),
            "fn_fireplace": state.get("fn_fireplace"),
            # Valve
            "open_pct": state.get("openPct"),
            # Water
            "flow": state.get("flow"),
            "flow_volume": state.get("volume"),
            "flow_temperature": state.get("flowTemp"),
        }
