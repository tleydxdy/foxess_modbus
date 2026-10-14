"""Constants for foxess_modbus."""

# Base component constants
NAME = "foxess_modbus"
DOMAIN = "foxess_modbus"

ISSUE_URL = "https://github.com/nathanmarlor/foxess_modbus/issues"

# Platforms
SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"
SELECT = "select"
NUMBER = "number"
PLATFORMS = [SENSOR, BINARY_SENSOR, SELECT, NUMBER]

# Modbus Options
ENTITY_ID_PREFIX = "entity_id_prefix"
UNIQUE_ID_PREFIX = "unique_id_prefix"
FRIENDLY_NAME = "friendly_name"
MODBUS_SLAVE = "modbus_slave"
MODBUS_TYPE = "modbus_type"  # TCP, UDP, SERIAL, RTU_OVER_TCP
POLL_RATE = "poll_rate"
MAX_READ = "max_read"
ROUND_SENSOR_VALUES = "round_sensor_values"

INVERTER_MODEL = "inverter_model"
INVERTER_BASE = "inverter_base"
INVERTER_CONN = "inverter_conn"
# The inverter manager version to use. This is the version corresponding to InverterModelConnectionTypeProfile.versions,
# i.e. the upper bound of a range of versions we support. None means use the latest.
INVERTER_VERSION = "inverter_version"

HOST = "host"
PORT = "port"
TCP = "tcp"
UDP = "udp"
SERIAL = "serial"
RTU_OVER_TCP = "rtu_over_tcp"

# Serial is always 9600, which is what the inverters default to
SERIAL_BAUDRATE = 9600

# Connection defaults
DEFAULT_PORT = 502
DEFAULT_MODBUS_SLAVE = 247
DEFAULT_POLL_RATE = 10
# Be safe by default
DEFAULT_MAX_READ = 20

CONFIG_ENTRY_TITLE = "FoxESS - Modbus"

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
