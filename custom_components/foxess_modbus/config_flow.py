"""Adds config flow for foxess_modbus."""

import re
from typing import Any

import probatio
from modbus_connection import ModbusError

from homeassistant import config_entries
from homeassistant.components.energy import data
from homeassistant.components.energy.data import BatterySourceType
from homeassistant.components.energy.data import EnergyPreferencesUpdate
from homeassistant.components.energy.data import FlowFromGridSourceType
from homeassistant.components.energy.data import FlowToGridSourceType
from homeassistant.components.energy.data import GridSourceType
from homeassistant.components.energy.data import SolarSourceType
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigFlow
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.config_entries import OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.util import slugify

from .client.modbus_client import ModbusClient
from .client.modbus_client import create_modbus_params
from .client.modbus_client import endpoint_str
from .common.exceptions import UnsupportedInverterError
from .const import CONFIG_ENTRY_TITLE
from .const import DEFAULT_MAX_READ
from .const import DEFAULT_MODBUS_SLAVE
from .const import DEFAULT_POLL_RATE
from .const import DEFAULT_PORT
from .const import DOMAIN
from .const import ENTITY_ID_PREFIX
from .const import FRIENDLY_NAME
from .const import HOST
from .const import INVERTER_BASE
from .const import INVERTER_CONN
from .const import INVERTER_MODEL
from .const import INVERTER_VERSION
from .const import MAX_READ
from .const import MODBUS_SLAVE
from .const import MODBUS_TYPE
from .const import POLL_RATE
from .const import PORT
from .const import ROUND_SENSOR_VALUES
from .const import RTU_OVER_TCP
from .const import SERIAL
from .const import TCP
from .const import UDP
from .const import UNIQUE_ID_PREFIX
from .inverter_profiles import Version
from .inverter_profiles import inverter_connection_type_profile_from_config
from .modbus_controller import ModbusController

SECTION_MORE_OPTIONS = "more_options"

_PORT_SELECTOR = probatio.All(
    NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1, max=65535)),
    probatio.Coerce(int),
)

_POSITIVE_INT_SELECTOR = probatio.All(
    NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1)),
    probatio.Coerce(int),
)

_UNIT_ID_SELECTOR = probatio.All(
    NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1, max=247)),
    probatio.Coerce(int),
)

_CONNECTION_TYPE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value="LAN", label="lan"),
            SelectOptionDict(value="AUX", label="aux"),
        ],
        translation_key="inverter_connections",
    )
)

# Poll rate, max read and sensor rounding apply whatever the link is, so they
# are tucked away in a collapsed section.
MORE_OPTIONS = {
    probatio.Required(SECTION_MORE_OPTIONS): section(
        probatio.Schema(
            {
                probatio.Required(POLL_RATE, default=DEFAULT_POLL_RATE): _POSITIVE_INT_SELECTOR,
                probatio.Required(MAX_READ, default=DEFAULT_MAX_READ): _POSITIVE_INT_SELECTOR,
                probatio.Required(ROUND_SENSOR_VALUES, default=False): BooleanSelector(),
            }
        ),
        {"collapsed": True},
    )
}

_TCP_SCHEMA = probatio.Schema(
    {
        probatio.Required(HOST): TextSelector(),
        probatio.Required(PORT, default=DEFAULT_PORT): _PORT_SELECTOR,
        probatio.Required(MODBUS_SLAVE, default=DEFAULT_MODBUS_SLAVE): _UNIT_ID_SELECTOR,
        probatio.Required(INVERTER_CONN, default="AUX"): _CONNECTION_TYPE_SELECTOR,
        **MORE_OPTIONS,
    }
)

_NETWORK_SCHEMA = probatio.Schema(
    {
        probatio.Required(HOST): TextSelector(),
        probatio.Required(PORT, default=DEFAULT_PORT): _PORT_SELECTOR,
        probatio.Required(MODBUS_SLAVE, default=DEFAULT_MODBUS_SLAVE): _UNIT_ID_SELECTOR,
        **MORE_OPTIONS,
    }
)

_SERIAL_SCHEMA = probatio.Schema(
    {
        probatio.Required(HOST, default="/dev/ttyUSB0"): TextSelector(),
        probatio.Required(MODBUS_SLAVE, default=DEFAULT_MODBUS_SLAVE): _UNIT_ID_SELECTOR,
        **MORE_OPTIONS,
    }
)


def _flatten(modbus_type: str, user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten the sectioned form input into config entry data."""

    data = {MODBUS_TYPE: modbus_type, **user_input}
    data.update(data.pop(SECTION_MORE_OPTIONS))

    # ModbusTcpParams lower-cases the host it is given, so what we store has to
    # match, or entries pointing at the same device won't share one connection
    if modbus_type != SERIAL:
        data[HOST] = data[HOST].lower()

    # UDP and RTU over TCP are always reached through an RS485-to-ethernet adapter
    if modbus_type != TCP:
        data[INVERTER_CONN] = "AUX"

    return data


def _sectioned(data: dict[str, Any]) -> dict[str, Any]:
    """Shape config entry data back into the sectioned form input."""

    more_option_keys = (POLL_RATE, MAX_READ, ROUND_SENSOR_VALUES)
    sectioned = {key: value for key, value in data.items() if key not in more_option_keys}
    sectioned[SECTION_MORE_OPTIONS] = {key: data[key] for key in more_option_keys if key in data}
    return sectioned


def _validate_hostname(host: str) -> bool:
    """Whether the given network hostname is valid"""

    return re.fullmatch(r"[a-zA-Z0-9\.\-]+", host) is not None


def _create_unique_id(full_model: str, data: dict[str, Any]) -> str:
    """Generate a unique id from the probed model and the link details.

    Two identical inverters can sit behind one adapter (on different modbus
    slave ids), so the entry is tied to both its link and its modbus address.
    """
    return slugify(f"{full_model} {endpoint_str(data)} {data[MODBUS_SLAVE]}")


class ModbusFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for foxess_modbus."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, _user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""

        return self.async_show_menu(
            step_id="user",
            menu_options=[TCP, RTU_OVER_TCP, UDP, SERIAL],
        )

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle an inverter reached directly over its LAN port, or via an RS485-to-ethernet adapter."""
        return await self._async_step_link(TCP, _TCP_SCHEMA, user_input)

    async def async_step_rtu_over_tcp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle an inverter reached via an adapter sending RTU frames over TCP."""
        return await self._async_step_link(RTU_OVER_TCP, _NETWORK_SCHEMA, user_input)

    async def async_step_udp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle an inverter reached via an RS485-to-ethernet adapter using UDP."""
        return await self._async_step_link(UDP, _NETWORK_SCHEMA, user_input)

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle an inverter reached over an RS485 adapter on a serial port."""
        return await self._async_step_link(SERIAL, _SERIAL_SCHEMA, user_input)

    async def _async_probe(self, data: dict[str, Any]) -> tuple[str, str]:
        """Connect and attempt to detect the inverter type behind the link"""

        async with async_get_temporary_unit(self.hass, create_modbus_params(data), data[MODBUS_SLAVE]) as unit:
            client = ModbusClient(unit, endpoint_str(data))
            return await ModbusController.autodetect(client, data[MODBUS_SLAVE], data[MAX_READ])

    async def async_step_friendly_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user enter a friendly name for their inverter."""

        errors: dict[str, str] = {}
        if user_input is not None:
            friendly_name = user_input.get(FRIENDLY_NAME, "")
            if friendly_name and self._is_duplicate_friendly_name(friendly_name):
                errors = {"base": "duplicate_friendly_name"}
            else:
                self._data[UNIQUE_ID_PREFIX] = friendly_name
                self._data[ENTITY_ID_PREFIX] = slugify(friendly_name).strip("_")
                return await self.async_step_energy()

        schema = probatio.Schema(
            {
                probatio.Optional(FRIENDLY_NAME): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="friendly_name",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors or None,
        )

    def _is_duplicate_friendly_name(self, friendly_name: str) -> bool:
        """Check that no other config entry has a friendly name the same

        Entities and the services below key off the friendly name (or its
        absence), so this has to be unique across all inverters.
        """

        return any(
            dict(x.data).get(UNIQUE_ID_PREFIX, "") == friendly_name
            for x in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def async_step_energy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user choose whether to set up the energy dashboard"""

        if user_input is not None:
            if user_input["energy_dashboard"]:
                await self._setup_energy_dashboard()
            return self.async_create_entry(title=CONFIG_ENTRY_TITLE, data=self._data)

        schema = probatio.Schema(
            {
                probatio.Required("energy_dashboard", default=False): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="energy",
            data_schema=schema,
            description_placeholders={
                "energy_link": "https://my.home-assistant.io/redirect/config_energy",
            },
        )

    async def _setup_energy_dashboard(self) -> None:
        """Setup Energy Dashboard"""

        manager = await data.async_get_manager(self.hass)

        name_prefix = f"sensor.{self._data[ENTITY_ID_PREFIX]}_" if self._data[ENTITY_ID_PREFIX] else "sensor."
        energy_prefs = EnergyPreferencesUpdate(energy_sources=[])  # type: ignore
        energy_prefs["energy_sources"].extend(
            [
                SolarSourceType(
                    type="solar",
                    stat_energy_from=f"{name_prefix}pv1_energy_total",
                    config_entry_solar_forecast=None,
                ),
                SolarSourceType(
                    type="solar",
                    stat_energy_from=f"{name_prefix}pv2_energy_total",
                    config_entry_solar_forecast=None,
                ),
                BatterySourceType(
                    type="battery",
                    stat_energy_to=f"{name_prefix}battery_charge_total",
                    stat_energy_from=f"{name_prefix}battery_discharge_total",
                ),
            ]
        )

        grid_source = GridSourceType(type="grid", flow_from=[], flow_to=[], cost_adjustment_day=0.0)
        grid_source["flow_from"].append(
            FlowFromGridSourceType(
                stat_energy_from=f"{name_prefix}grid_consumption_energy_total",
                stat_cost=None,
                entity_energy_price=None,
                number_energy_price=None,
            )
        )
        grid_source["flow_to"].append(
            FlowToGridSourceType(
                stat_energy_to=f"{name_prefix}feed_in_energy_total",
                stat_compensation=None,
                entity_energy_price=None,
                number_energy_price=None,
            )
        )
        energy_prefs["energy_sources"].append(grid_source)

        await manager.async_update(energy_prefs)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguring how the inverter is reached."""

        entry = self._get_reconfigure_entry()
        entry_data = dict(entry.data)
        modbus_type = entry_data[MODBUS_TYPE]
        schema = (
            _TCP_SCHEMA if modbus_type == TCP else _SERIAL_SCHEMA if modbus_type == SERIAL else _NETWORK_SCHEMA
        )

        return await self._async_step_link(modbus_type, schema, user_input, is_reconfigure=entry)

    async def _async_step_link(
        self,
        modbus_type: str,
        schema: probatio.Schema,
        user_input: dict[str, Any] | None,
        is_reconfigure: ConfigEntry | None = None,
    ) -> ConfigFlowResult:
        """Ask for the link settings, probe the inverter behind them, and show the form again on failure."""

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            data = _flatten(modbus_type, user_input)

            if modbus_type != SERIAL and not _validate_hostname(data[HOST]):
                errors = {"base": "invalid_hostname"}
                description_placeholders = {"hostname": data[HOST]}
            else:
                try:
                    base_model, full_model = await self._async_probe(data)
                except UnsupportedInverterError as ex:
                    errors = {"base": "inverter_model_not_supported"}
                    description_placeholders["not_supported_model"] = ex.full_model
                except ModbusError as ex:
                    errors = {"base": "cannot_connect"}
                    description_placeholders["error_details"] = str(ex)
                except Exception as ex:  # pylint: disable=broad-except
                    errors = {"base": "cannot_connect"}
                    description_placeholders["error_details"] = str(ex)
                else:
                    await self.async_set_unique_id(_create_unique_id(full_model, data))

                    if is_reconfigure is None:
                        self._abort_if_unique_id_configured()
                        self._data = {
                            **data,
                            INVERTER_BASE: base_model,
                            INVERTER_MODEL: full_model,
                        }
                        return await self.async_step_friendly_name()

                    # The inverter behind the link must stay the same: everything else
                    # registered against the entry (devices, its unique id) still affixes to it
                    if base_model != dict(is_reconfigure.data)[INVERTER_BASE]:
                        self._abort_if_unique_id_configured()
                        errors = {"base": "wrong_device"}
                    else:
                        # Keep the friendly name and prefixes: only how the inverter is reached changes
                        data.update(
                            INVERTER_BASE=base_model,
                            INVERTER_MODEL=full_model,
                            FRIENDLY_NAME=is_reconfigure.data.get(FRIENDLY_NAME, ""),
                            ENTITY_ID_PREFIX=is_reconfigure.data.get(ENTITY_ID_PREFIX, ""),
                            UNIQUE_ID_PREFIX=is_reconfigure.data.get(UNIQUE_ID_PREFIX, ""),
                        )
                        self._abort_if_unique_id_configured()
                        return self.async_update_reload_and_abort(is_reconfigure, data_updates=data)

        suggested_values = self._data if is_reconfigure is None else _sectioned(dict(is_reconfigure.data))
        step_id = modbus_type if is_reconfigure is None else "reconfigure"

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested_values),
            errors=errors or None,
            description_placeholders=description_placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(OptionsFlow):
    """Options flow handler"""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user change the options of the entry."""

        entry = self._entry
        if entry.state is not config_entries.ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        combined = {**dict(entry.data), **dict(entry.options)}
        versions = sorted(inverter_connection_type_profile_from_config(combined).versions.keys())  # type: ignore[type-var]

        schema_parts: dict[Any, Any] = {}

        if len(versions) > 1:
            version_options = []
            prev_version: Version | None = None
            # The last element will be None, which means "latest"
            for version in versions[:-1]:
                assert version is not None
                # For the sake of clarity, we'll shown an exclusive range as the version one minor version below
                # (We don't know whether that version ever actually existed, but that doesn't matter here)
                if prev_version is None:
                    label = f"Earlier than {version}"
                else:
                    inclusive_end = Version(version.major, version.minor - 1)
                    label = f"{inclusive_end}" if inclusive_end == prev_version else f"{prev_version} - {inclusive_end}"

                version_options.append({"label": label, "value": str(version)})
                prev_version = version
            version_options.append({"label": f"{versions[-2]} and higher", "value": "latest"})  # HA can't cope with None

            schema_parts[probatio.Optional(INVERTER_VERSION)] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=x["value"], label=x["label"]) for x in version_options
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema_parts[probatio.Optional(POLL_RATE)] = _POSITIVE_INT_SELECTOR
        schema_parts[probatio.Optional(MAX_READ)] = _POSITIVE_INT_SELECTOR
        schema_parts[probatio.Optional(ROUND_SENSOR_VALUES, default=False)] = BooleanSelector()

        schema = probatio.Schema(schema_parts)

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=self.add_suggested_values_to_schema(schema, combined),
            )

        options: dict[str, Any] = {}

        version = user_input.get(INVERTER_VERSION)
        if version not in (None, "latest") and version != combined.get(INVERTER_VERSION):
            options[INVERTER_VERSION] = version

        for key in (POLL_RATE, MAX_READ):
            value = user_input.get(key)
            if value is not None and value != combined.get(key):
                options[key] = value

        round_sensor_values = user_input.get(ROUND_SENSOR_VALUES)
        if round_sensor_values and round_sensor_values != combined.get(ROUND_SENSOR_VALUES, False):
            options[ROUND_SENSOR_VALUES] = True

        return self.async_create_entry(title="", data=options)
