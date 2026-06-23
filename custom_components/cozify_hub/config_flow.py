"""Config flow for Cozify HUB."""
from __future__ import annotations

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

    Local mode:
      1. Choose mode → local
      2. Enter hub IP + hub token → verify → create entry

    Cloud mode:
      1. Choose mode → cloud
      2. Enter email → OTP sent
      3. Enter OTP → cloud login, hub tokens fetched
      4. Select hub (skipped for single hub)
      5. Create cloud entry
    """

    VERSION = 2

    def __init__(self) -> None:
        self._email: str | None = None
        self._cloud_token: str | None = None
        self._hub_keys: dict[str, str] = {}  # hub_id -> hub_token
        self._hub_names: dict[str, str] = {}  # hub_id -> name
        self._selected_hub_id: str | None = None
        self._reauth_connection_mode: str = CONNECTION_MODE_LOCAL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: choose local or cloud connection mode."""
        if user_input is not None:
            if user_input["connection_mode"] == CONNECTION_MODE_LOCAL:
                return await self.async_step_local()
            return await self.async_step_email()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("connection_mode", default=CONNECTION_MODE_LOCAL): vol.In({
                    CONNECTION_MODE_LOCAL: "Local (LAN)",
                    CONNECTION_MODE_CLOUD: "Cloud",
                }),
            }),
        )

    # ── Local path ───────────────────────────────────────────────────────────

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Local mode: enter hub IP and hub token, verify by connecting."""
        errors: dict[str, str] = {}

        if user_input is not None:
            hub_ip = user_input["hub_ip"].strip()
            hub_token = user_input["hub_token"].strip()
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                info = await auth.get_hub_info_local(hub_ip, hub_token)
                if not info.get("reachable"):
                    errors["base"] = "cannot_connect"
                else:
                    hub_id = info.get("hubId", "")
                    hub_name = info.get("name") or f"Cozify HUB ({hub_ip})"
                    if not hub_id:
                        errors["base"] = "cannot_connect"
                    else:
                        return await self._create_entry(
                            hub_id, hub_token, hub_name,
                            CONNECTION_MODE_LOCAL, hub_ip, None,
                        )
            except Exception as err:
                _LOGGER.error("Local hub connection failed for %s: %s", hub_ip, err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema({
                vol.Required("hub_ip"): str,
                vol.Required("hub_token"): str,
            }),
            errors=errors,
        )

    # ── Cloud path ───────────────────────────────────────────────────────────

    async def async_step_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud mode step 1: enter email and request OTP."""
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
            step_id="email",
            data_schema=vol.Schema({vol.Required("email"): str}),
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud mode step 2: verify OTP, fetch hub tokens."""
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
                    self._build_hub_names()
                    if len(self._hub_keys) == 1:
                        self._selected_hub_id = next(iter(self._hub_keys))
                        return await self._finish_cloud()
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

    def _build_hub_names(self) -> None:
        """Decode hub names from JWT payloads in hub tokens."""
        self._hub_names = {}
        for hub_id, hub_token in self._hub_keys.items():
            claims = _decode_hub_token(hub_token)
            _LOGGER.debug("Hub token claims for %s: %s", hub_id[:8], claims)
            name = claims.get("hub_name") or claims.get("name") or f"Cozify HUB ({hub_id[:8]})"
            self._hub_names[hub_id] = name

    async def async_step_select_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud mode step 3 (multi-hub): select which hub to configure."""
        if user_input is not None:
            self._selected_hub_id = user_input["hub"]
            return await self._finish_cloud()

        return self.async_show_form(
            step_id="select_hub",
            data_schema=vol.Schema({
                vol.Required("hub"): vol.In(self._hub_names),
            }),
        )

    async def _finish_cloud(self) -> ConfigFlowResult:
        """Create cloud mode entry for the selected hub."""
        hub_id = self._selected_hub_id
        return await self._create_entry(
            hub_id,
            self._hub_keys[hub_id],
            self._hub_names[hub_id],
            CONNECTION_MODE_CLOUD,
            None,
            self._cloud_token,
        )

    # ── Reauthentication ──────────────────────────────────────────────────────

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by ConfigEntryAuthFailed — restart auth for existing entry."""
        self._email = entry_data.get(CONF_EMAIL)
        self._reauth_connection_mode = entry_data.get(CONF_CONNECTION_MODE, CONNECTION_MODE_LOCAL)

        if self._email:
            # Email known — send OTP automatically, go straight to OTP entry
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                await auth.request_otp(self._email)
                return await self.async_step_reauth_otp()
            except Exception as err:
                _LOGGER.warning("Auto OTP send failed, asking user to confirm email: %s", err)

        return await self.async_step_reauth_email()

    async def async_step_reauth_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reauth: enter or confirm email address and request OTP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"]
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                await auth.request_otp(email)
                self._email = email
                return await self.async_step_reauth_otp()
            except CozifyHubConnectionError as err:
                _LOGGER.error("Reauth OTP request failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected reauth error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_email",
            data_schema=vol.Schema({
                vol.Required("email", default=self._email or ""): str
            }),
            errors=errors,
        )

    async def async_step_reauth_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reauth: verify OTP and update stored tokens."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp = user_input["otp"]
            session = async_get_clientsession(self.hass)
            auth = CozifyHubAuth(session, API_ENVIRONMENT_PRODUCTION)
            try:
                cloud_token = await auth.verify_otp(self._email, otp)
                hub_keys = await auth.get_hub_keys(cloud_token)
                entry = self._get_reauth_entry()
                hub_id = entry.data[CONF_HUB_ID]
                new_hub_token = hub_keys.get(hub_id)
                if not new_hub_token:
                    errors["base"] = "no_hubs"
                else:
                    data_updates = {CONF_HUB_TOKEN: new_hub_token, CONF_EMAIL: self._email}
                    if self._reauth_connection_mode != CONNECTION_MODE_LOCAL:
                        data_updates[CONF_CLOUD_TOKEN] = cloud_token
                    return self.async_update_reload_and_abort(
                        entry, data_updates=data_updates
                    )
            except CozifyHubAuthError:
                errors["base"] = "invalid_auth"
            except CozifyHubConnectionError as err:
                _LOGGER.error("Reauth connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected reauth error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    # ── Entry creation ────────────────────────────────────────────────────────

    async def _create_entry(
        self,
        hub_id: str,
        hub_token: str,
        hub_name: str,
        connection_mode: str,
        hub_ip: str | None,
        cloud_token: str | None,
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
                CONF_CLOUD_TOKEN: cloud_token,
                CONF_HUB_ID: hub_id,
                CONF_HUB_TOKEN: hub_token,
                CONF_HUB_NAME: hub_name,
                CONF_HUB_HOST: hub_ip,
            },
        )
