import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol  # type: ignore[import-untyped]

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv, aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from pyhon import Hon
from pyhon.exceptions import HonAuthenticationError

from .const import DOMAIN, PLATFORMS, MOBILE_ID, CONF_REFRESH_TOKEN
from .ssl import update_ca_certificates

_LOGGER = logging.getLogger(__name__)

_LOGGER.warning("HON MODULE: custom_components.hon loaded")

HON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema(vol.All(cv.ensure_list, [HON_SCHEMA]))},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hon from a config entry."""
    _LOGGER.warning("HON SETUP: starting async_setup_entry for %s", entry.unique_id)
    session = aiohttp_client.async_get_clientsession(hass)

    try:
        _LOGGER.warning("HON SETUP: checking SSL certificates")
        updated = await update_ca_certificates(hass)
        if updated:
            _LOGGER.error("HON SETUP: certificate loaded into Certifi CA bundle — restart required")
            raise Exception("Certificate loaded into Certifi CA bundle. Restart Home Assistant to apply changes.")

        _LOGGER.warning("HON SETUP: creating Hon instance with mobile_id=%s", MOBILE_ID)
        hon = Hon(
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
            mobile_id=MOBILE_ID,
            session=session,
            test_data_path=Path(hass.config.config_dir),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
        )

        _LOGGER.warning("HON SETUP: calling hon.create() — authenticating and loading appliances")
        hon = await hon.create()
        _LOGGER.warning(
            "HON SETUP: hon.create() succeeded — %d appliance(s) loaded, refresh_token present=%s",
            len(hon.appliances),
            bool(hon.api.auth.refresh_token),
        )
        for i, appliance in enumerate(hon.appliances):
            _LOGGER.warning(
                "HON SETUP: appliance[%d] type=%s unique_id=%s nick_name=%s connection=%s",
                i,
                appliance.appliance_type,
                appliance.unique_id,
                appliance.nick_name,
                appliance.connection,
            )

        # Diagnostic: query the raw appliances endpoint and log the full response
        try:
            import aiohttp as _aiohttp
            from pyhon import const as _hon_const
            async with hon.api._hon.get(f"{_hon_const.API_URL}/commands/v1/appliance") as _resp:
                _raw = await _resp.json()
            _LOGGER.warning(
                "HON DIAG: raw HTTP status=%s full_response=%s",
                _resp.status,
                _raw,
            )
        except Exception as diag_exc:
            _LOGGER.warning("HON DIAG: failed to get raw response: %s", diag_exc, exc_info=True)

    except HonAuthenticationError as exc:
        _LOGGER.error("HON SETUP: authentication failed: %s", exc, exc_info=True)
        raise
    except Exception as exc:
        _LOGGER.error("HON SETUP: failed to create Hon instance: %s", exc, exc_info=True)
        raise

    async def async_update_data() -> dict[str, Any]:
        """Fetch data from API."""
        _LOGGER.warning("HON UPDATE: starting data refresh for %d appliance(s)", len(hon.appliances))
        try:
            for appliance in hon.appliances:
                _LOGGER.warning(
                    "HON UPDATE: updating appliance type=%s unique_id=%s",
                    appliance.appliance_type,
                    appliance.unique_id,
                )
                await appliance.update()
                _LOGGER.warning(
                    "HON UPDATE: appliance updated — connection=%s onOffStatus=%s",
                    appliance.connection,
                    appliance.get("onOffStatus", "N/A"),
                )
            token = hon.api.auth.refresh_token
            _LOGGER.warning("HON UPDATE: refresh complete, token present=%s", bool(token))
            return {"last_update": token}
        except HonAuthenticationError as exc:
            _LOGGER.error("HON UPDATE: authentication error: %s", exc, exc_info=True)
            raise ConfigEntryAuthFailed from exc
        except Exception as exc:
            _LOGGER.error("HON UPDATE: unexpected error: %s", exc, exc_info=True)
            raise UpdateFailed(f"Error updating Hon data: {exc}") from exc

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        config_entry=entry,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    def _handle_mqtt_update(_: Any) -> None:
        """Handle MQTT updates."""
        try:
            coordinator.async_set_updated_data({"last_update": hon.api.auth.refresh_token})
            _LOGGER.warning("HON MQTT: coordinator notified of MQTT update")
        except Exception as exc:
            _LOGGER.error("HON MQTT: error handling update: %s", exc)

    def handle_update(msg: Any) -> None:
        """Handle updates from MQTT subscription in a thread-safe way."""
        try:
            _LOGGER.warning("HON MQTT: received update, scheduling coordinator refresh")
            hass.loop.call_soon_threadsafe(_handle_mqtt_update, msg)
        except Exception as exc:
            _LOGGER.error("HON MQTT: error scheduling update: %s", exc)

    try:
        hon.subscribe_updates(handle_update)
    except Exception as exc:
        _LOGGER.error("HON SETUP: error subscribing to MQTT updates: %s", exc)

    _LOGGER.warning("HON SETUP: running first coordinator refresh")
    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.warning(
            "HON SETUP: first refresh done — last_update_success=%s",
            coordinator.last_update_success,
        )
    except Exception as exc:
        _LOGGER.error("HON SETUP: first refresh failed: %s", exc, exc_info=True)
        raise

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: hon.api.auth.refresh_token}
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.unique_id] = {"hon": hon, "coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.warning("HON SETUP: async_setup_entry complete for %s", entry.unique_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        hon = hass.data[DOMAIN][entry.unique_id]["hon"]
        refresh_token = hon.api.auth.refresh_token

        try:
            hon.subscribe_updates(None)
        except Exception as exc:
            _LOGGER.warning("Error unsubscribing from updates: %s", exc)

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token}
        )

        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unload_ok:
            hass.data[DOMAIN].pop(entry.unique_id)

        return unload_ok
    except Exception as exc:
        _LOGGER.error("Error unloading entry: %s", exc)
        return False
