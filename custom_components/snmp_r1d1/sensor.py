"""Sensor platform for snmp_r1d1 integration."""

import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util
import re
import math
from .const import *
from .helpers import *
from . import mac_table
from .coordinator import SnmpDataUpdateCoordinator
_LOGGER = logging.getLogger(__name__)


# ================================================================
# Helper: Safe math formula evaluation
# ================================================================
def eval_formula(formula: str, x):
    """Safely evaluate a math formula string with one variable x."""
    try:
        x_val = float(x)  # convert input to float if possible
    except (ValueError, TypeError):
        return x  # if not numeric, return unchanged

    # Normalize formulas like 2(x+1) → 2*(x+1), (2+3)10 → (2+3)*10
    formula = re.sub(r"\)(\d)", r")*\1", formula)
    formula = re.sub(r"(\d)\(", r"\1*(", formula)
    formula = re.sub(r"\)([a-zA-Z])", r")*\1", formula)
    formula = re.sub(r"([a-zA-Z])\(", r"\1*(", formula)
    formula = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", formula)

    # Replace variable x with actual value
    formula = formula.replace("x", str(x_val))

    # Only allow math module functions (safe)
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    allowed["__builtins__"] = None

    try:
        result = eval(formula, allowed, {})
        # Convert float like 40.0 → 40
        return int(result) if isinstance(result, float) and result.is_integer() else result
    except Exception:
        return x

# ================================================================
# Helper: Apply calculation (direct, diff, or formula)
# ================================================================
def apply_calc(raw_value, entry, coordinator, sensor_id, is_port=False, port_key=None):
    """Apply calculation based on calc type, then optional math formula."""
    calc_type = entry.get("calc", "direct")  # default is "direct"
    math_formula = entry.get("math")  # optional formula string

    try:
        # ------------------------
        # direct = use raw value
        # ------------------------
        if calc_type == "direct":
            result = raw_value

        # ------------------------
        # diff = calculate rate of change
        # ------------------------
        elif calc_type == "diff":
            previous_data = coordinator.data.get("previous", {})

            # Port-level diff calculation
            if is_port:
                previous_port_data = previous_data.get("ports", {}).get(port_key, {})
                previous_raw = previous_port_data.get(entry.get("key"))
                previous_timestamp = previous_data.get("last_updated", {}).get(
                    f"port_{port_key}_{entry.get('key')}", 0
                )
            # Device-level diff calculation
            else:
                previous_device_data = previous_data.get("device", {})
                previous_raw = previous_device_data.get(entry.get("key"))
                previous_timestamp = previous_data.get("last_updated", {}).get(
                    f"device_{entry.get('key')}", 0
                )

            current_timestamp = dt_util.utcnow().timestamp()
            elapsed_time = current_timestamp - previous_timestamp

            # If first poll or no valid history → no diff
            if not previous_data or previous_raw is None or elapsed_time <= 0:
                return None

            try:
                current_raw = float(raw_value)
                previous_raw = float(previous_raw)
            except (ValueError, TypeError):
                return None

            # Skip if counter reset
            if current_raw < previous_raw:
                return None

            try:
                # Calculate rate (per second)
                result = (current_raw - previous_raw) / elapsed_time
                result = round(result, 2)
            except Exception:
                return None

        # ------------------------
        # Unknown type → raw value
        # ------------------------
        else:
            result = raw_value

        # Apply optional math formula
        if math_formula and result is not None:
            try:
                result = eval_formula(math_formula, result)
            except Exception:
                pass
        return result

    except Exception as e:
        _LOGGER.error(f"Error applying calc for {sensor_id}: {e}")
        return raw_value


# ================================================================
# Entry point: set up all sensor entities
# ================================================================
async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up sensors from a config entry."""
    _LOGGER.info("Starting sensor setup")
    coordinator: SnmpDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    device_info_data = config_entry.data.get(CONF_DEVICE_INFO, {})
    prefix = config_entry.data[CONF_ENTITY_PREFIX]

    # Device metadata (manufacturer, model, etc.)
    device_info = {
        "identifiers": {(DOMAIN, config_entry.data[CONF_DEVICE_IP])},
        "name": config_entry.data[CONF_DEVICE_NAME],
        "manufacturer": device_info_data.get("manufacturer", "Unknown"),
        "model": device_info_data.get("model", "Unknown"),
    }
    entities = []


    # ----------------------------
    # Device-level OID sensors
    # ----------------------------
    for key, entry in coordinator.validated_oids.get("device", {}).items():
        entity_type = entry.get("type", "sensor")
        if entity_type == "sensor":
            entry["key"] = key
            entities.append(SnmpSensor(coordinator, device_info, key, entry, prefix))
            _LOGGER.info(f"Added device sensor: {key}")
        elif entity_type == "text_sensor":
            entities.append(SnmpTextSensor(coordinator, device_info, key, entry, prefix))
            _LOGGER.info(f"Added device text sensor: {key}")

    # ----------------------------
    # Port-level OID sensors
    # ----------------------------
    for port_key, port_attrs in coordinator.validated_oids.get("ports", {}).items():
        for key, entry in port_attrs.items():
            entity_type = entry.get("type", "sensor")
            if entity_type == "sensor":
                entry["key"] = key
                entities.append(SnmpPortSensor(coordinator, device_info, key, entry, prefix, port_key))
                _LOGGER.info(f"Added port sensor: {port_key}_{key}")
            elif entity_type == "text_sensor":
                entities.append(SnmpPortTextSensor(coordinator, device_info, key, entry, prefix, port_key))
                _LOGGER.info(f"Added port text sensor: {port_key}_{key}")

    # ----------------------------
    # MAC table sensors (if OIDs exist)
    # ----------------------------
    has_mac_table = any(
        entry.get("type") == "mac_table"
        for entry in coordinator.validated_oids.get("device", {}).values()
    )
    has_mac_port = any(
        entry.get("type") == "mac_port"
        for entry in coordinator.validated_oids.get("device", {}).values()
    )

    if has_mac_table and has_mac_port:
        port_count = int(device_info_data.get("port_count", 1))
        entities.append(mac_table.DeviceMacTableSensor(coordinator, device_info, prefix))
        entities.append(mac_table.DeviceMacTableLastUpdateSensor(coordinator, device_info, prefix))
        for port in range(1, port_count + 1):
            entities.append(mac_table.PortMacTableSensor(coordinator, device_info, prefix, port))
        _LOGGER.info("MAC table sensors created")

    else:
        _LOGGER.info("No MAC table OIDs found, skipping MAC sensors")
    # Add all entities to HA
    async_add_entities(entities)
    _LOGGER.info("Sensor setup completed")

# ================================================================
# Entity: Device-level numeric sensor
# ================================================================
class SnmpSensor(SensorEntity):
    """Representation of a device-level sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict, prefix: str):
        super().__init__()
        self.coordinator = coordinator
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False

        
        # Unique ID = entry_id + sensor_type
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", sensor_type, prefix)

        # Human-readable name
        self._attr_name = make_entity_name(sensor_type, prefix=prefix)
        self._attr_device_class = entry.get("device_class")
        self._attr_native_unit_of_measurement = entry.get("native_unit_of_measurement")
        self._entry = entry

    async def async_added_to_hass(self):
        # Register for coordinator updates
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        # Get raw value
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return None
        raw_value = self.coordinator.data["device"].get(self.sensor_type)
        if raw_value is None:
            return None
        # Apply transformations
        processed_value = apply_calc(raw_value, self._entry, self.coordinator, self._attr_unique_id)
        return apply_vmap(processed_value, self._entry.get("vmap", {}), self._attr_unique_id)


# ================================================================
# Entity: Port-level numeric sensor
# ================================================================
class SnmpPortSensor(SensorEntity):
    """Representation of a port-level sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict, prefix: str, padded_port_key: str):
        super().__init__()
        self.coordinator = coordinator
        self.padded_port_key = padded_port_key
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        # Unique ID includes port key
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", sensor_type, prefix, padded_port_key)

        # Human-readable name: "Port-05 In Octets"
        self._attr_name = make_entity_name(sensor_type, prefix=prefix, port_key=padded_port_key)

        self._attr_device_class = entry.get("device_class")
        self._attr_native_unit_of_measurement = entry.get("native_unit_of_measurement")
        self._entry = entry

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return None
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        raw_value = port_data.get(self.sensor_type)
        _LOGGER.debug("raw value [%s]: raw='%s'", self._attr_unique_id, raw_value)

        if raw_value is None:
            return None
        processed_value = apply_calc(raw_value, self._entry, self.coordinator, self._attr_unique_id, is_port=True, port_key=self.padded_port_key)
        _LOGGER.debug("apply_calc trace [%s]: raw='%s' → processed='%s'", self._attr_unique_id, raw_value, processed_value)
        _LOGGER.debug("vmap trace [%s]: raw=%r → processed=%r; vmap=%s", self._attr_unique_id, raw_value, processed_value, self._entry.get("vmap", {}))

        return apply_vmap(processed_value, self._entry.get("vmap", {}), self._attr_unique_id)


# ================================================================
# Entity: Device-level text sensor
# ================================================================
class SnmpTextSensor(SensorEntity):
    """Representation of a device-level read-only text sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict, prefix: str):
        super().__init__()
        self.coordinator = coordinator
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "text_sensor", sensor_type, prefix)
        self._attr_name = make_entity_name(sensor_type, prefix=prefix)
        self._entry = entry

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def state(self):
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return None
        return self.coordinator.data["device"].get(self.sensor_type, "")


# ================================================================
# Entity: Port-level text sensor
# ================================================================
class SnmpPortTextSensor(SensorEntity):
    """Representation of a port-level read-only text sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict, prefix: str, padded_port_key: str):
        super().__init__()
        self.coordinator = coordinator
        self.padded_port_key = padded_port_key
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "text_sensor", sensor_type, prefix, padded_port_key)
        self._attr_name = make_entity_name(sensor_type, prefix=prefix, port_key=padded_port_key)
        self._entry = entry

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def state(self):
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return None
        return self.coordinator.data["ports"].get(self.padded_port_key, {}).get(self.sensor_type, "")