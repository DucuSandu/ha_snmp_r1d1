"""Helper functions for snmp_r1d1 integration."""

import logging

_LOGGER = logging.getLogger(__name__)

# ================================================================
# Boolean vmap (for switch and binary_sensor)
# ================================================================
def apply_bool_vmap(value, vmap, sensor_id, logger=_LOGGER):
    """Map SNMP values to booleans (True/False)."""
    try:
        val = str(value)

        if not vmap:  # default fallback
            return val in ("1", "on", "true")

        def _match(token, val):
            if token == val:
                return True
            if token.startswith(">"):
                try:
                    return float(val) > float(token[1:])
                except ValueError:
                    return False
            if token.startswith("<"):
                try:
                    return float(val) < float(token[1:])
                except ValueError:
                    return False
            return False

        if "on" in vmap:
            v_on = vmap["on"]
            if isinstance(v_on, list):
                if any(_match(token, val) for token in v_on):
                    return True
            elif _match(str(v_on), val):
                return True

        if "off" in vmap:
            v_off = vmap["off"]
            if isinstance(v_off, list):
                if any(_match(token, val) for token in v_off):
                    return False
            elif _match(str(v_off), val):
                return False

        if "1" in vmap and val == "1":
            return vmap["1"].lower() in ("on", "true", "1")
        if "0" in vmap and val == "0":
            return vmap["0"].lower() in ("off", "false", "0")

        return val in ("1", "on", "true")

    except Exception as e:
        logger.error("Error applying vmap for %s: %s", sensor_id, e)
        return False


# ================================================================
# Numeric/String vmap (for sensor values) Apply value mapping (e.g. 1=up, 2=down)
# ================================================================
def apply_vmap(value, vmap, sensor_id, logger=_LOGGER):
    """Apply value mapping for sensors (numeric/string)."""
    if not vmap or value is None:
        return value

    # 🔎 Start-of-call context
    logger.debug("apply_vmap start [%s]: value=%r, vmap=%s", sensor_id, value, vmap)

    try:
        for key, mapped in vmap.items():
            if key.startswith(">") or key.startswith("<"):
                # Only compare numerically if value is numeric
                try:
                    v = float(value)
                    t = float(key[1:])
                    if key.startswith(">") and v > t:
                        return mapped
                    if key.startswith("<") and v < t:
                        return mapped
                except (TypeError, ValueError):
                    # 🔎 Skip bad numeric compares instead of erroring
                    logger.debug(
                        "apply_vmap skip numeric compare [%s]: value=%r not numeric for threshold '%s'",
                        sensor_id, value, key
                    )
                    continue
            elif str(value) == key:
                return mapped

        return value
    except Exception as e:
        logger.error("Error applying vmap for %s: %s", sensor_id, e)
        logger.debug("apply_vmap context [%s]: value=%r, vmap=%s", sensor_id, value, vmap)
        return value

# ================================================================
# Boolean → SNMP mapping (write path)
# ================================================================
def to_snmp_bool(state: bool, vmap: dict, sensor_id: str, logger=_LOGGER):
    """Convert a boolean state (True/False) into the SNMP raw value using vmap.

    Args:
        state (bool): Desired state (True for ON, False for OFF).
        vmap (dict): Mapping dictionary with "on" and "off" keys.
        sensor_id (str): Identifier for logging context.
        logger (Logger): Logger for errors.

    Returns:
        str|int: The SNMP-compatible value from vmap, or None if invalid.
    """
    try:
        if not vmap or "on" not in vmap or "off" not in vmap:
            logger.error("No valid vmap defined for %s (requires both 'on' and 'off')", sensor_id)
            return None

        return vmap["on"] if state else vmap["off"]

    except Exception as e:
        logger.error("Error mapping boolean state for %s: %s", sensor_id, e)
        return None
# ================================================================
# Entity naming helpers
# ================================================================
def make_entity_name(sensor_type: str) -> str:
    """Convert a raw sensor key (e.g. 'poe_status') into a human-friendly name.

    Args:
        sensor_type (str): The technical key used in validated_oids.
                           Example: "poe_status"

    Returns:
        str: Human-friendly name.
             Example: "Poe Status"
    """
    if not sensor_type:
        return "Unknown"
    return sensor_type.replace("_", " ").title()


def make_port_entity_name(port_key: str, sensor_type: str) -> str:
    """Generate a human-friendly entity name for port-based sensors.

    Args:
        port_key (str): Port identifier like "p02" or "p10".
        sensor_type (str): The technical key used in validated_oids.
                           Example: "poe_status"

    Returns:
        str: Human-friendly port-based entity name.
             Example: "Port-02 Poe Status"
    """
    try:
        port_num = port_key[1:] if port_key.startswith("p") else port_key
    except Exception:
        port_num = port_key or "?"
    return f"Port-{port_num} {make_entity_name(sensor_type)}"

# ================================================================
# Helper: Consistent unique_id generator for entities
# ================================================================
def make_entity_id(entry_id: str, key: str, suffix: str = None, port: str = None) -> str:
    """Build a consistent entity unique_id for Home Assistant entities.

    This function ensures all entity IDs across sensors, switches,
    binary_sensors, and MAC table entities follow the same format.

    Format:
        <entry_id>[_<port>]<key>[_<suffix>]

    Examples:
        make_entity_id("abc123", "poe_status")
            → "abc123_poe_status"

        make_entity_id("abc123", "poe_status", port="p02")
            → "abc123_p02_poe_status"

        make_entity_id("abc123", "poe_status", suffix="binary")
            → "abc123_poe_status_binary"

        make_entity_id("abc123", "mac_collection", port="p05", suffix="switch")
            → "abc123_p05_mac_collection_switch"

    Args:
        entry_id (str): The HA config_entry.entry_id (globally unique per device).
        key (str): The base entity key (e.g., "poe_status", "mac_table").
        suffix (str, optional): Optional extra marker like "binary", "switch", or "text".
        port (str, optional): Optional padded port key (e.g., "p01", "p12").

    Returns:
        str: A unique and consistent entity_id string.
    """
    # Start with config entry_id (ensures uniqueness across devices)
    parts = [entry_id]

    # Append port key if provided (ensures uniqueness per port)
    if port:
        parts.append(port)

    # Append the base key (mandatory)
    parts.append(key)

    # Append optional suffix (e.g., type marker like binary/text/switch)
    if suffix:
        parts.append(suffix)

    # Join with underscores → final unique_id
    return "_".join(parts)
