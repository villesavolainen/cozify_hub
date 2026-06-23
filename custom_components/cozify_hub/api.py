"""Cozify HUB API client."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import (
    API_URLS,
    API_URLS_FALLBACK,
    API_ENVIRONMENT_PRODUCTION,
    COZIFY_API_VERSION,
    COZIFY_LOCAL_API_PORT,
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
)

_LOGGER = logging.getLogger(__name__)


class CozifyHubApiError(Exception):
    """Base exception for Cozify HUB API errors."""


class CozifyHubAuthError(CozifyHubApiError):
    """Authentication error."""


class CozifyHubConnectionError(CozifyHubApiError):
    """Connection error."""


class CozifyHubApi:
    """Cozify HUB API client supporting local and cloud connection modes."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        hub_token: str,
        connection_mode: str = CONNECTION_MODE_LOCAL,
        cloud_token: str | None = None,
        hub_host: str | None = None,
        api_environment: str = API_ENVIRONMENT_PRODUCTION,
    ) -> None:
        self._session = session
        self._hub_token = hub_token
        self._connection_mode = connection_mode
        self._cloud_token = cloud_token
        self._hub_host = hub_host
        self._api_environment = api_environment
        self._cloud_base_url = API_URLS.get(api_environment, API_URLS[API_ENVIRONMENT_PRODUCTION])
        self._cloud_fallback_url = API_URLS_FALLBACK.get(api_environment)

    def update_tokens(self, cloud_token: str | None = None, hub_token: str | None = None) -> None:
        """Update tokens."""
        if cloud_token:
            self._cloud_token = cloud_token
        if hub_token:
            self._hub_token = hub_token

    async def refresh_cloud_token(self) -> str:
        """Refresh the cloud token and update internal state."""
        auth = CozifyHubAuth(self._session, self._api_environment)
        new_token = await auth.refresh_session(self._cloud_token)
        self._cloud_token = new_token
        return new_token

    def _get_ssl_context(self) -> bool:
        # Local mode: hub uses self-signed cert — skip verification
        # aiohttp accepts False to disable SSL verification without
        # calling the blocking ssl.create_default_context()
        if self._connection_mode == CONNECTION_MODE_LOCAL:
            return False
        return True

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._connection_mode == CONNECTION_MODE_LOCAL:
            headers["Authorization"] = self._hub_token
        else:
            headers["Authorization"] = self._cloud_token
            headers["X-Hub-Key"] = self._hub_token
        return headers

    def _build_url(self, endpoint: str) -> str:
        if self._connection_mode == CONNECTION_MODE_LOCAL:
            return f"http://{self._hub_host}:{COZIFY_LOCAL_API_PORT}/cc/{COZIFY_API_VERSION}/{endpoint}"
        return f"{self._cloud_base_url}/hub/remote/cc/{COZIFY_API_VERSION}/{endpoint}"

    async def _request(self, method: str, endpoint: str, data: Any = None) -> Any:
        url = self._build_url(endpoint)
        # Local mode uses HTTP — no SSL needed. Cloud uses HTTPS with SSL.
        kwargs: dict = {"headers": self._headers, "json": data}
        if self._connection_mode != CONNECTION_MODE_LOCAL:
            kwargs["ssl"] = True
        _LOGGER.debug("Request %s %s token=%s", method, url, self._hub_token[:8] if self._hub_token else "None")
        try:
            async with asyncio.timeout(15):
                async with self._session.request(method, url, **kwargs) as resp:
                    _LOGGER.debug("Response %s %s status=%s", method, url, resp.status)
                    if resp.status == 401:
                        body = await resp.text()
                        _LOGGER.error("401 response body: %s", body[:200])
                        raise CozifyHubAuthError("Authentication failed — token may be expired")
                    if resp.status == 408:
                        raise CozifyHubConnectionError("Hub not connected to cloud (408)")
                    resp.raise_for_status()
                    text = await resp.text()
                    if not text:
                        return {}
                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        return text
        except asyncio.TimeoutError as err:
            raise CozifyHubConnectionError(f"Timeout: {url}") from err
        except aiohttp.ClientError as err:
            raise CozifyHubConnectionError(f"Connection error: {err}") from err

    # ── Hub Info ──

    async def get_hub_info(self) -> dict[str, Any]:
        """Get hub information."""
        if self._connection_mode == CONNECTION_MODE_LOCAL:
            url = f"http://{self._hub_host}:{COZIFY_LOCAL_API_PORT}/hub"
            async with self._session.get(url, headers=self._headers) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        else:
            url = f"{self._cloud_base_url}/hub/remote/hub"
            async with self._session.get(url, headers=self._headers, ssl=True) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)

    # ── Devices ──

    async def get_devices(self) -> dict[str, Any]:
        return await self._request("GET", "devices")

    async def get_rooms(self) -> dict[str, Any]:
        return await self._request("GET", "rooms")

    async def get_scenes(self) -> dict[str, Any]:
        return await self._request("GET", "scenes")

    async def get_rules(self) -> dict[str, Any]:
        return await self._request("GET", "rules")

    async def get_groups(self) -> dict[str, Any]:
        return await self._request("GET", "groups")

    async def get_alarms(self) -> dict[str, Any]:
        return await self._request("GET", "alarms")

    # ── Device Commands ──

    async def turn_on(self, device_id: str) -> None:
        await self._request("PUT", "devices/command", [{"id": device_id, "type": "CMD_DEVICE_ON"}])

    async def turn_off(self, device_id: str) -> None:
        await self._request("PUT", "devices/command", [{"id": device_id, "type": "CMD_DEVICE_OFF"}])

    async def set_brightness(self, device_id: str, brightness: float) -> None:
        """Set brightness 0.0–1.0."""
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"type": "STATE_LIGHT", "brightness": brightness},
        }])

    async def set_color_hs(self, device_id: str, hue_rad: float, saturation: float,
                            brightness: float | None = None) -> None:
        """Set color. hue in radians (0–2π), saturation 0.0–1.0."""
        state: dict[str, Any] = {
            "type": "STATE_LIGHT", "colorMode": "hs",
            "hue": hue_rad, "saturation": saturation,
        }
        if brightness is not None:
            state["brightness"] = brightness
        await self._request("PUT", "devices/command", [{"id": device_id, "type": "CMD_DEVICE", "state": state}])

    async def set_color_temperature(self, device_id: str, kelvin: int,
                                     brightness: float | None = None) -> None:
        """Set color temperature in Kelvin."""
        state: dict[str, Any] = {"type": "STATE_LIGHT", "colorMode": "ct", "temperature": kelvin}
        if brightness is not None:
            state["brightness"] = brightness
        await self._request("PUT", "devices/command", [{"id": device_id, "type": "CMD_DEVICE", "state": state}])

    async def set_target_temperature(self, device_id: str, temperature: float) -> None:
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"type": "STATE_THERMOSTAT", "targetTemperature": temperature},
        }])

    async def set_climate_mode(self, device_id: str, mode: int) -> None:
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"mode": mode},
        }])

    async def set_climate_preset(self, device_id: str, preset: int) -> None:
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"preset": preset},
        }])

    async def set_cover_position(self, device_id: str, position: float) -> None:
        """Set cover position 0.0–1.0."""
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"type": "STATE_BLINDS", "position": position},
        }])

    async def set_cover_tilt(self, device_id: str, tilt_pct: int) -> None:
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"tiltPct": tilt_pct},
        }])

    async def set_ventilation_mode(self, device_id: str, mode: int) -> None:
        """VU modes: STOPPED=0, MIN=1, STANDARD=2, MAX=3, BOOST=4."""
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"mode": mode},
        }])

    async def set_fan_mode(self, device_id: str, fan_mode: int) -> None:
        """Fan modes: OFF=0, LOW=1, MEDIUM=2, HIGH=3, ON=4, AUTO=5, SMART=6."""
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"fanMode": fan_mode},
        }])

    async def set_valve_position(self, device_id: str, open_pct: float) -> None:
        """Set valve open percentage 0.0–100.0."""
        await self._request("PUT", "devices/command", [{
            "id": device_id, "type": "CMD_DEVICE",
            "state": {"openPct": open_pct},
        }])

    # ── Scene Commands ──

    async def activate_scene(self, scene_id: str) -> None:
        await self._request("PUT", "scenes/command", [{"id": scene_id, "type": "CMD_SCENE_ON"}])

    async def deactivate_scene(self, scene_id: str) -> None:
        await self._request("PUT", "scenes/command", [{"id": scene_id, "type": "CMD_SCENE_OFF"}])

    # ── Long Polling ──

    async def poll_device_deltas(self, ts: int, cozify_uuid: str) -> dict[str, Any]:
        """Long-poll for device state deltas. Hub holds connection up to 12s."""
        if self._connection_mode == CONNECTION_MODE_LOCAL:
            url = f"http://{self._hub_host}:{COZIFY_LOCAL_API_PORT}/cc/{COZIFY_API_VERSION}/hub/poll"
        else:
            url = f"{self._cloud_base_url}/hub/remote/cc/{COZIFY_API_VERSION}/hub/poll"

        params = {"ts": ts, "cozify_uuid": cozify_uuid}
        try:
            async with self._session.get(
                url, headers=self._headers, params=params,
                ssl=self._get_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=14),
            ) as resp:
                if resp.status == 304:
                    return {}
                if resp.status == 408:
                    raise CozifyHubConnectionError("Hub not connected to cloud (408)")
                if resp.status == 401:
                    raise CozifyHubAuthError("Authentication failed")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except asyncio.TimeoutError:
            return {}
        except aiohttp.ClientError as err:
            raise CozifyHubConnectionError(f"Poll error: {err}") from err


class CozifyHubAuth:
    """Handle Cozify cloud authentication."""

    def __init__(self, session: aiohttp.ClientSession,
                 api_environment: str = API_ENVIRONMENT_PRODUCTION) -> None:
        self._session = session
        self._base_url = API_URLS.get(api_environment, API_URLS[API_ENVIRONMENT_PRODUCTION])
        self._fallback_url = API_URLS_FALLBACK.get(api_environment)

    async def _post(self, path: str, data: str | None = None,
                    headers: dict | None = None) -> str:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.post(url, data=data, headers=headers or {}) as resp:
                if resp.status in (401, 403, 404) and self._fallback_url:
                    url = f"{self._fallback_url}{path}"
                    async with self._session.post(url, data=data, headers=headers or {}) as resp2:
                        resp2.raise_for_status()
                        return await resp2.text()
                resp.raise_for_status()
                return await resp.text()
        except aiohttp.ClientError as err:
            raise CozifyHubConnectionError(f"Request failed: {err}") from err

    async def request_otp(self, email: str) -> None:
        """Request OTP email."""
        await self._post(f"/user/requestlogin?email={email}")

    async def verify_otp(self, email: str, otp: str) -> str:
        """Verify OTP, return cloud token."""
        token = await self._post(
            "/user/emaillogin",
            data=f"email={email}&password={otp}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return token.strip().strip('"')

    async def get_hub_keys(self, cloud_token: str) -> dict[str, str]:
        """Get hub_id → hub_token mapping."""
        url = f"{self._base_url}/user/hubkeys"
        async with self._session.get(url, headers={"Authorization": cloud_token}) as resp:
            if resp.status in (401, 403, 404) and self._fallback_url:
                url = f"{self._fallback_url}/user/hubkeys"
                async with self._session.get(url, headers={"Authorization": cloud_token}) as resp2:
                    resp2.raise_for_status()
                    return await resp2.json(content_type=None)
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def refresh_session(self, cloud_token: str) -> str:
        """Refresh cloud token (~28 day expiry)."""
        token = await self._post("/user/refreshsession",
                                 headers={"Authorization": cloud_token})
        return token.strip().strip('"')

    async def get_hub_lan_ips(self, cloud_token: str) -> list[str]:
        """Discover hub LAN IP addresses."""
        url = f"{self._base_url}/hub/lan_ip"
        async with self._session.get(url, headers={"Authorization": cloud_token}) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_hub_info_cloud(self, cloud_token: str, hub_token: str) -> dict[str, Any]:
        """Get hub info via cloud."""
        url = f"{self._base_url}/hub/remote/hub"
        headers = {"Authorization": cloud_token, "X-Hub-Key": hub_token}
        try:
            async with self._session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                _LOGGER.debug("get_hub_info_cloud status=%s", resp.status)
                if resp.ok:
                    data = await resp.json(content_type=None)
                    return {"online": True, **data}
                body = await resp.text()
                _LOGGER.debug("get_hub_info_cloud non-ok body: %s", body[:200])
                return {"online": False}
        except asyncio.TimeoutError:
            _LOGGER.debug("get_hub_info_cloud timeout")
            return {"online": False}
        except aiohttp.ClientError as err:
            _LOGGER.debug("get_hub_info_cloud error: %s", err)
            return {"online": False}

    async def get_hub_info_local(self, hub_host: str, hub_token: str) -> dict[str, Any]:
        """Get hub info via local connection."""
        url = f"http://{hub_host}:{COZIFY_LOCAL_API_PORT}/hub"
        try:
            async with self._session.get(
                url,
                headers={"Authorization": hub_token},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.ok:
                    data = await resp.json(content_type=None)
                    return {"online": True, "reachable": True, **data}
                return {"online": False, "reachable": True}
        except aiohttp.ClientError:
            return {"online": False, "reachable": False}
