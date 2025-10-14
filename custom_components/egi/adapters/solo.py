"""
Adapter logic for EGI HVAC Adapter Solo (single IDU).
"""
import logging
from .base_adapter import BaseAdapter

BRAND_NAMES = {
    1:  "Hitachi VRF (2-wire)",
    2:  "Daikin VRF (2-wire)",
    3:  "Toshiba VRF (2-wire)",
    4:  "Mitsubishi Heavy Industries VRF (2-wire)",
    6:  "Gree VRF (2-wire)",
    7:  "Hisense VRF (2-wire)",
    10: "LG",
    13: "Samsung",
    19: "Panasonic Ducted (4-wire)",
    20: "Midea W1/W2 (2-wire)",
    36: "Hitachi Ducted (4-wire)",
    38: "Gree Ducted (4-wire)",
    39: "Gree Ducted (2-wire)",
    41: "Daikin MX Ducted (4-wire)",
    44: "Hisense Ducted (4-wire)",
    46: "Haier REMOTE terminal",
    48: "Midea CN20 (5-wire)",
    50: "Midea X1/X2 (2-wire)",
    51: "Midea COLMO (2-wire)",
    53: "Fujitsu Ducted & VRF (3-wire)",
    54: "Fujitsu Ducted & VRF (2-wire)",
    55: "AUX VRF (2-wire)",
    56: "AUX Ducted (4-wire)",
    68: "Electra Central On/Off HVAC (STAR)",
    68: "Electra Central Inverter HVAC (GEMINI)",
    77: "Electra ELCO Central Inverter HVAC (4-wire)",
    69: "Tadiran Central Inverter HVAC (TAC680DC)",
    70: "Tadiran Central Inverter HVAC (TAC680)",
    71: "Tadiran Central HVAC (TAC640HPU)",
    72: "Tadiran Central On/Off HVAC (TAC640H)",
    73: "Tadiran Central Fan Coil (TAC640FC)",
    75: "AUX VRF (4-wire)",
    76: "Haier WIFI (4-wire)",
    78: "AUFIT VRF (4-wire)",
    79: "Family VRF (4-wire)",
    88: "Simulator",
}


class AdapterSolo(BaseAdapter):
    BRAND_NAMES = BRAND_NAMES

    def __init__(self):
        super().__init__()
        self._log = logging.getLogger(f"custom_components.egi.adapter.{self.__class__.__name__}")
        self.name = "EGI HVAC Adapter Solo"
        self.display_type = "Solo Adapter"
        self.max_idus = 1
        self.supports_scan = False
        self.supports_brand_write = True
        self.supports_factory_reset = False
        self.supports_restart = True

    def get_brand_name(self, code):
        return BRAND_NAMES.get(code, f"Unknown ({code})")

    def read_adapter_info(self, client):
        try:
            reg = client.read_holding_registers(2000, 1)
            if not reg:
                self._log.warning("Solo: No response for adapter info (D2000).")
                return {}
            self._log.debug("Solo adapter info: D2000 = %s", reg)
            return {
                "brand_code": reg[0],
                "supported_modes": 0,
                "supported_fan": 0,
                "temp_limits": 0,
                "special_info": 0,
            }
        except Exception as e:
            self._log.warning("Failed to read Solo adapter info: %s", e)
            return {}

    def scan_devices(self, client):
        self._log.debug("Solo scan_devices always returns one device (0,0).")
        return [(0, 0)]

    def read_status(self, client, system, index):
        data = {
            "available": False,
            "power": False,
            "mode_code": 0,
            "target_temp": 24,
            "current_temp": 0,
            "fan_code": 0,
            "wind_code": 0,
            "error_code": 0
        }
        try:
            regs = client.read_holding_registers(0, 7)
            if not regs or len(regs) < 7:
                self._log.warning("Solo read_status: no or partial response from IDU 0-0")
                return data

            data["available"] = True
            data["power"] = bool(regs[0])
            data["mode_code"] = regs[1] & 0xFF
            data["target_temp"] = regs[2]
            data["fan_code"] = regs[3] & 0x0F
            data["wind_code"] = regs[4] & 0xFF
            data["error_code"] = regs[5]
            data["current_temp"] = regs[6]

            self._log.debug("Solo read_status for IDU 0-0: %s", data)
        except Exception as e:
            self._log.error("Error reading Solo status: %s", e)
        return data

    def write_power(self, client, system, index, power_on: bool):
        self._log.info("Solo write_power(%s) to IDU 0-0", power_on)
        return client.write_register(4000, 1 if power_on else 0)

    def write_mode(self, client, system, index, mode_code: int):
        self._log.info("Solo write_mode(%s) to IDU 0-0", mode_code)
        return client.write_register(4001, mode_code & 0x0F)

    def write_temperature(self, client, system, index, temp: int):
        tval = max(16, min(30, temp))
        self._log.info("Solo write_temperature(%s → %s) to IDU 0-0", temp, tval)
        return client.write_register(4002, tval)

    def write_fan_speed(self, client, system, index, fan_code: int):
        self._log.info("Solo write_fan_speed(%s) to IDU 0-0", fan_code)
        return client.write_register(4003, fan_code & 0x0F)

    def write_swing(self, client, system, index, swing_code: int):
        self._log.info("Solo write_swing(%s) to IDU 0-0", swing_code)
        return client.write_register(4004, swing_code & 0xFF)

    def write_brand_code(self, client, brand_id: int):
        self._log.info("Solo write_brand_code(%s) + restart", brand_id)
        success = client.write_register(4010, brand_id & 0xFF)
        if success:
            client.write_register(4015, 1)
        return success

    def restart_device(self, client):
        self._log.info("Solo restart_device()")
        return client.write_register(4015, 1)

    def factory_reset(self, client):
        self._log.info("Solo factory_reset()")
        return client.write_register(4016, 1)

    def decode_mode(self, value):
        return {
            0x01: "heat",
            0x02: "cool",
            0x04: "fan_only",
            0x08: "dry",
        }.get(value, "fan_only")

    def encode_mode(self, ha_mode):
        return {
            "heat": 0x01,
            "cool": 0x02,
            "fan_only": 0x04,
            "dry": 0x08,
        }.get(ha_mode, 0x02)

    def decode_fan(self, value):
        return {
            0x00: "auto",
            0x01: "low",
            0x02: "medium",
            0x03: "high",
        }.get(value, "auto")

    def encode_fan(self, ha_fan):
        return {
            "auto": 0x00,
            "low": 0x01,
            "medium": 0x02,
            "high": 0x03,
        }.get(ha_fan, 0x00)

    def decode_adapter_info(self, info: dict) -> dict:
        """
        Turn raw registers from read_adapter_info() into a friendly dict.
        Solo only provides brand_code (at D2000) and nothing else.
        """
        # Pull the raw brand_code (or default to 0)
        code = info.get("brand_code", 0)
        return {
            "brand_code": code,
            "brand_name": self.get_brand_name(code),
            # Solo has no modes/fan/limits, but you could include placeholders
            "supported_modes": [],
            "supported_fan": [],
            "min_temp": None,
            "max_temp": None,
        }
