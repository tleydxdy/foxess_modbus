"""The client used to talk Modbus.

All connections are owned by the modbus integration. Config flows and config
entries borrow units on them with `async_get_temporary_unit`/`async_get_unit`,
and this client wraps one of those units.
"""

from typing import Any

from modbus_connection import (
    ModbusError,
    ModbusSerialParams,
    ModbusTcpParams,
    ModbusUdpParams,
    ModbusUnit,
)

from ..common.types import RegisterType
from ..const import HOST
from ..const import MODBUS_TYPE
from ..const import PORT
from ..const import RTU_OVER_TCP
from ..const import SERIAL
from ..const import SERIAL_BAUDRATE
from ..const import TCP
from ..const import UDP

# Modbus exception code for "Illegal Data Address", which the inverter returns
# for registers it does not implement. See the Modbus specification.
_EXCEPTION_ILLEGAL_ADDRESS = 2

# The largest value a 16-bit register can hold
_UINT16_MAX = 65535


def create_modbus_params(data: dict[str, Any]) -> ModbusTcpParams | ModbusUdpParams | ModbusSerialParams:
    """Build the link params for the connection described by the given config, which select the shared connection."""

    modbus_type = data[MODBUS_TYPE]
    if modbus_type == SERIAL:
        return ModbusSerialParams(device=data[HOST], baudrate=SERIAL_BAUDRATE)

    if modbus_type == TCP:
        return ModbusTcpParams(host=data[HOST], port=data[PORT])
    if modbus_type == UDP:
        return ModbusUdpParams(host=data[HOST], port=data[PORT])
    if modbus_type == RTU_OVER_TCP:
        return ModbusTcpParams(host=data[HOST], port=data[PORT], framer="rtu")
    raise AssertionError()


def endpoint_str(data: dict[str, Any]) -> str:
    """Provide a human-readable name for the connection described by the given config."""

    modbus_type = data[MODBUS_TYPE]
    if modbus_type == SERIAL:
        return data[HOST]
    if modbus_type == RTU_OVER_TCP:
        return f"{RTU_OVER_TCP}://{data[HOST]}:{data[PORT]}"
    return f"{modbus_type}://{data[HOST]}:{data[PORT]}"


class ModbusClient:
    """Modbus"""

    def __init__(self, unit: ModbusUnit, name: str) -> None:
        """Init"""
        self._unit = unit
        self._name = name

    async def read_registers(
        self,
        start_address: int,
        num_registers: int,
        register_type: RegisterType,
        slave: int,
    ) -> list[int]:
        """Read registers"""
        message = (
            f"Error reading registers. Type: {register_type}; start: {start_address}; count: {num_registers}; "
            f"slave: {slave}"
        )
        try:
            if register_type == RegisterType.HOLDING:
                response = await self._unit.read_holding_registers(start_address, num_registers)
            elif register_type == RegisterType.INPUT:
                response = await self._unit.read_input_registers(start_address, num_registers)
            else:
                raise AssertionError()
        except ModbusError as ex:
            raise ModbusClientFailedError(message, self, ex) from ex

        # The unit hands back a list of registers, but be defensive about other return shapes
        if not isinstance(response, list):
            response = getattr(response, "registers", None)
        assert isinstance(response, list)
        return response

    async def write_registers(self, register_address: int, register_values: list[int], slave: int) -> None:
        """Write registers"""
        message = f"Error writing registers. Start: {register_address}; values: {register_values}; slave: {slave}"
        try:
            for i, value in enumerate(register_values):
                value = int(value)  # Ensure that we've been given an int
                if not (0 <= value <= _UINT16_MAX):
                    raise ValueError(f"Value {value} is outside the range of a register")
                await self._unit.write_register(register_address + i, value)
        except ModbusError as ex:
            raise ModbusClientFailedError(message, self, ex) from ex

    def __str__(self) -> str:
        return self._name


class ModbusClientFailedError(Exception):
    """Raised when the ModbusClient fails to read/write"""

    def __init__(self, message: str, client: ModbusClient, response: Exception) -> None:
        super().__init__(f"{message} from {client}: {response}")
        self.message = message
        self.client = client
        self.response = response

    def __str__(self) -> str:
        return f"{self.message} from {self.client}: {self.response}"


def is_illegal_address_error(ex: ModbusClientFailedError) -> bool:
    """Whether the underlying error was a "no such register" (Illegal Data Address) response.

    The link layer doesn't expose the modbus exception code on every error, in
    which case we can't distinguish an unimplemented register and report False.
    """
    return getattr(ex.response, "exception_code", None) == _EXCEPTION_ILLEGAL_ADDRESS
