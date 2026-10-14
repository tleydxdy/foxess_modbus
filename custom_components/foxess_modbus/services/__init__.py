"""Startup for the services exposed by this integration"""

from homeassistant.core import HomeAssistant

from . import read_registers_service
from . import update_charge_period_service
from . import websocket_api
from . import write_registers_service


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services this integration exposes"""

    read_registers_service.register(hass)
    write_registers_service.register(hass)
    update_charge_period_service.register(hass)
    websocket_api.register(hass)
