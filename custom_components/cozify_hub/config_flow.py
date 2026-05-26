"""Config flow for Cozify HUB."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CozifyHubAuth, CozifyHubAuthError, CozifyHubConnectionError
from .const import (
    API_ENVIRONMENT_PRODUCTION,
    CONF_API_ENVIRONMENT,
    CONF_CLOUD_TOKEN,
    CONF_CONNECTION_MODE,
    CONF_EMAIL,
    CONF_HUB_HOST,
    CONF_HUB_ID,
    CONF_HUB_NAME,
    CONF_HUB_TOKEN,
    CONNECTION_MODE_CLOUD,
    CONNECTION_MODE_LOCAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _decode_hub_token(token: str) -> dict:
    """Decode JWT payload from a hub token without verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


class CozifyHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Cozify HUB.

    Setup flow:
    1. Email → OTP sent to email
    2. OTP → cloud login, hub tokens fetched, LAN IPs probed
    3. Multiple hubs → hub selection step (skipped for single hub)
    4. Connection mode: local or cloud
    5a. Local + IP auto-discovered → create entry
    5b. Local + IP unknown → manual IP entry → create entry
    5c. Cloud → create cloud entry
    """

    VERSION = 2

    def __init__(self) -> None:
        self._email: str | None = None
        self._cloud_token: str | None = None
        self._hub_keys: dict[str, str] = {}  # hub_id -> hub_token
        # hub_id -> {name, hub_token, local_ip, local_reachable, cloud_online}
        self._hub_info: dict[str, dict] = {}
        self._selected_hub_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: ask for email address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"]
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                await auth.request_otp(email)
                self._email = email
                return await self.async_step_otp()
            except CozifyHubConnectionError as err:
                _LOGGER.error("OTP request failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("email"): str}),
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: enter OTP, fetch hub tokens, probe LAN for hub IPs."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp = user_input["otp"]
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                self._cloud_token = await auth.verify_otp(self._email, otp)
                _LOGGER.debug("Cloud login successful")

                self._hub_keys = await auth.get_hub_keys(self._cloud_token)
                _LOGGER.debug("Got %d hub keys", len(self._hub_keys))

                if not self._hub_keys:
                    errors["base"] = "no_hubs"
                else:
                    await self._discover_hubs(auth)
                    if len(self._hub_info) == 1:
                        self._selected_hub_id = next(iter(self._hub_info))
                        return await self.async_step_connection_mode()
                    return await self.async_step_select_hub()

            except CozifyHubAuthError:
                errors["base"] = "invalid_auth"
            except CozifyHubConnectionError as err:
                _LOGGER.error("Connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def _discover_hubs(self, auth: CozifyHubAuth) -> None:
        """Probe LAN and cloud to build hub info dict with names and reachability."""
        hub_info: dict[str, dict] = {}
        for hub_id, hub_token in self._hub_keys.items():
            claims = _decode_hub_token(hub_token)
            _LOGGER.debug("Hub token claims for %s: %s", hub_id[:8], claims)
            name = claims.get("hub_name") or claims.get("name") or f"Cozify HUB ({hub_id[:8]})"
            hub_info[hub_id] = {
                "name": name,
                "hub_token": hub_token,
                "local_ip": None,
                "local_reachable": False,
                "cloud_online": False,
            }

        # Fetch cloud info (name, online status) for all hubs in parallel
        hub_ids = list(self._hub_keys.keys())
        hub_tokens = list(self._hub_keys.values())
        cloud_tasks = [
            auth.get_hub_info_cloud(self._cloud_token, token) for token in hub_tokens
        ]
        cloud_results = await asyncio.gather(*cloud_tasks, return_exceptions=True)
        for hub_id, result in zip(hub_ids, cloud_results):
            if isinstance(result, dict):
                hub_info[hub_id]["cloud_online"] = result.get("online", False)
                if result.get("name"):
                    hub_info[hub_id]["name"] = result["name"]

        # Discover LAN IPs and probe each for hub identity
        try:
            lan_ips = await auth.get_hub_lan_ips(self._cloud_token)
            _LOGGER.debug("Found LAN IPs: %s", lan_ips)
        except Exception as err:
            _LOGGER.debug("LAN IP discovery failed: %s", err)
            lan_ips = []

        if lan_ips:
            probe_tasks = [auth.get_hub_info_local(ip, "") for ip in lan_ips]
            probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)
            for ip, result in zip(lan_ips, probe_results):
                if isinstance(result, dict) and result.get("reachable"):
                    local_hub_id = result.get("hubId", "")
                    if local_hub_id in hub_info:
                        hub_info[local_hub_id]["local_ip"] = ip
                        hub_info[local_hub_id]["local_reachable"] = True
                        if result.get("name"):
                            hub_info[local_hub_id]["name"] = result["name"]

        self._hub_info = hub_info
        _LOGGER.debug(
            "Hub discovery complete: %s",
            {k: {**v, "hub_token": "***"} for k, v in hub_info.items()},
        )

    async def async_step_select_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3 (multi-hub only): select which hub to configure."""
        if user_input is not None:
            self._selected_hub_id = user_input["hub"]
            return await self.async_step_connection_mode()

        hub_options = {hub_id: info["name"] for hub_id, info in self._hub_info.items()}

        return self.async_show_form(
            step_id="select_hub",
            data_schema=vol.Schema({vol.Required("hub"): vol.In(hub_options)}),
        )

    async def async_step_connection_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: choose local (LAN) or cloud connection mode."""
        hub = self._hub_info[self._selected_hub_id]

        if user_input is not None:
            mode = user_input["connection_mode"]
            if mode == CONNECTION_MODE_LOCAL:
                if hub["local_reachable"] and hub["local_ip"]:
                    return await self._create_entry(
                        self._selected_hub_id,
                        hub["hub_token"],
                        hub["name"],
                        CONNECTION_MODE_LOCAL,
                        hub["local_ip"],
                    )
                # Local chosen but no auto-discovered IP → ask user for IP
                return await self.async_step_hub_ip()
            else:
                return await self._create_entry(
                    self._selected_hub_id,
                    hub["hub_token"],
                    hub["name"],
                    CONNECTION_MODE_CLOUD,
                    None,
                )

        modes: dict[str, str] = {}
        if hub["local_reachable"]:
            modes[CONNECTION_MODE_LOCAL] = "Local (LAN)"
        if hub["cloud_online"]:
            modes[CONNECTION_MODE_CLOUD] = "Cloud (remote)"

        if not modes:
            # Hub unreachable via both LAN and cloud — let user try manual IP
            return await self.async_step_hub_ip()

        default_mode = CONNECTION_MODE_LOCAL if hub["local_reachable"] else CONNECTION_MODE_CLOUD

        return self.async_show_form(
            step_id="connection_mode",
            data_schema=vol.Schema({
                vol.Required("connection_mode", default=default_mode): vol.In(modes),
            }),
            description_placeholders={"hub_name": hub["name"]},
        )

    async def async_step_hub_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fallback step: manually enter hub local IP when auto-discovery failed."""
        errors: dict[str, str] = {}
        hub = self._hub_info[self._selected_hub_id]

        if user_input is not None:
            hub_ip = user_input["hub_ip"].strip()
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)

            try:
                info = await auth.get_hub_info_local(hub_ip, "")
                if not info.get("reachable"):
                    errors["base"] = "cannot_connect"
                else:
                    local_hub_id = info.get("hubId", "")
                    if local_hub_id != self._selected_hub_id:
                        _LOGGER.error(
                            "Hub at %s has ID %s, expected %s",
                            hub_ip, local_hub_id, self._selected_hub_id,
                        )
                        errors["base"] = "cannot_connect"
                    else:
                        hub_name = info.get("name") or hub["name"]
                        return await self._create_entry(
                            self._selected_hub_id,
                            hub["hub_token"],
                            hub_name,
                            CONNECTION_MODE_LOCAL,
                            hub_ip,
                        )
            except Exception as err:
                _LOGGER.error("Hub IP check failed for %s: %s", hub_ip, err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="hub_ip",
            data_schema=vol.Schema({vol.Required("hub_ip"): str}),
            errors=errors,
            description_placeholders={"hub_name": hub["name"]},
        )

    async def _create_entry(
        self,
        hub_id: str,
        hub_token: str,
        hub_name: str,
        connection_mode: str,
        hub_ip: str | None,
    ) -> ConfigFlowResult:
        """Create the config entry."""
        await self.async_set_unique_id(hub_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=hub_name,
            data={
                CONF_CONNECTION_MODE: connection_mode,
                CONF_API_ENVIRONMENT: API_ENVIRONMENT_PRODUCTION,
                CONF_EMAIL: self._email,
                CONF_CLOUD_TOKEN: self._cloud_token,
                CONF_HUB_ID: hub_id,
                CONF_HUB_TOKEN: hub_token,
                CONF_HUB_NAME: hub_name,
                CONF_HUB_HOST: hub_ip,
            },
        )
