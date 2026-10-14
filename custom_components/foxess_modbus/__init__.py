"""
Custom integration to integrate FoxESS Modbus with Home Assistant.

For more details about this integration, please refer to
https://github.com/nathanmarlor/foxess_modbus
"""

import copy
import logging

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .client.modbus_client import ModbusClient
from .client.modbus_client import create_modbus_params
from .client.modbus_client import endpoint_str
from .common.types import HassData
from .common.types import HassDataEntry
from .const import DOMAIN
from .const import MAX_READ
from .const import MODBUS_SLAVE
from .const import POLL_RATE
from .const import PLATFORMS
from .const import STARTUP_MESSAGE
from .inverter_profiles import inverter_connection_type_profile_from_config
from .modbus_controller import ModbusController
from .services import async_setup_services

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the services this integration exposes"""

    _LOGGER.info(STARTUP_MESSAGE)
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""

    # It turns out that HA really doesn't like us mutating the ConfigEntry it passes us!
    # Since we merge in the options etc, do this on a copy.
    # From here on, do not access entry.data and entry.options directly!
    data = copy.deepcopy(dict(entry.data))
    data.update(copy.deepcopy(dict(entry.options)))

    # The modbus integration owns the connection: this shares one connection to the device
    # with everything else talking to it, and is released when the entry unloads.
    unit = async_get_unit(hass, entry, create_modbus_params(data), data[MODBUS_SLAVE])
    client = ModbusClient(unit, endpoint_str(data))

    controller = ModbusController(
        hass,
        client,
        inverter_connection_type_profile_from_config(data),
        data,
        data[MODBUS_SLAVE],
        data[POLL_RATE],
        data[MAX_READ],
    )

    hass_data: HassData = hass.data.setdefault(DOMAIN, HassData())
    hass_data.setdefault(entry.entry_id, HassDataEntry(controllers=[]))
    hass_data[entry.entry_id]["controllers"].append(controller)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    hass_data: HassData = hass.data[DOMAIN]
    for controller in hass_data.pop(entry.entry_id)["controllers"]:
        controller.unload()

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry on update of its options."""

    hass_data: HassData = hass.data[DOMAIN]
    if entry.entry_id not in hass_data:
        # Unloaded: HA reloads the entry itself when the entry is reloaded
        return

    await hass.config_entries.async_reload(entry.entry_id)
