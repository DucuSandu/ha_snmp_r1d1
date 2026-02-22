"""Configuration flow for snmp_r1d1 integration."""

import ipaddress
import re
import asyncio
import copy
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

import homeassistant.helpers.config_validation as cv
from .const import *
from .snmp import SnmpClient, SnmpCredentials
import logging

_LOGGER = logging.getLogger(__name__)

# Allowed entity types (lowercase)
ALLOWED_TYPES = ["sensor", "binary_sensor", "switch", "text", "text_sensor",  "mac_table", "mac_port",]
ALLOWED_CALC_TYPES = ["direct", "diff"]

def validate_custom_oids(oids_str):
    """Validate custom OIDs in name:oid format."""
    if not oids_str:
        return []
    pairs = [pair.strip() for pair in oids_str.split(",")]
    result = []
    for pair in pairs:
        parts = pair.split(":", 1)  # Split only on the first colon
        if len(parts) != 2:
            raise ValueError("Invalid custom OIDs format. Use name:oid (e.g., name1:oid1,name2:oid2).")
        name, oid = parts[0].strip(), parts[1].strip()
        if not name or not oid:
            raise ValueError("Invalid custom OIDs format. Name must be non-empty and OID must not be empty")
        # Normalize to always start with "."
        if not oid.startswith("."):
            oid = f".{oid}"
        result.append((name, oid))
    if len(result) > MAX_CUSTOM_OIDS:
        raise ValueError(f"Maximum {MAX_CUSTOM_OIDS} custom OIDs allowed.")
    return result



# ================================================================
# Helper: Validate MAC OID via shallow walk
# ================================================================
async def validate_mac_oid(client, oid: str, key: str, section: str, logger=_LOGGER) -> bool:
    """Check if a MAC-related OID exists by doing a single GETNEXT and verifying root."""

    try:
        logger.debug("validate_mac_oid: requesting GETNEXT for root=%s (%s/%s)", oid, section, key)
        result = await client.async_getnext(oid)
        logger.debug("validate_mac_oid: GETNEXT raw result for root=%s: %s", oid, result)

        if not result or "oid" not in result:
            logger.warning("MAC OID %s for %s in %s returned no valid result", oid, key, section)
            return False

        first_oid = result["oid"]
        value = result.get("value")
        logger.debug("validate_mac_oid: first_oid=%s, value=%s", first_oid, value)

        # Normalize both sides: strip leading dot
        if first_oid.lstrip(".").startswith(oid.lstrip(".")):
            logger.debug(
                "Validated MAC OID %s for %s in %s via GETNEXT (first_oid=%s, value=%s)",
                oid, key, section, first_oid, value
            )
            return True

        logger.warning(
            "MAC OID %s for %s in %s did not match in GETNEXT (first_oid=%s, root=%s, value=%s)",
            oid, key, section, first_oid, oid, value
        )
        return False

    except Exception as e:
        logger.warning("Failed to validate MAC OID %s for %s in %s: %s", oid, key, section, e)
        return False


def _process_options(entry, key, section, entity_type, errors, log_context):
    """Validate and normalize options for OID entries.
    Returns a dict of validated/defaulted fields, caller merges into _configured_entry.
    """

    options = {}

    # Calc
    calc_type = str(entry.get("calc", "direct")).lower()
    options["calc"] = calc_type

    # Optional math
    math = entry.get("math")
    if isinstance(math, str) and math.strip():
        options["math"] = math.strip()

    # Unit
    unit = entry.get("unit")
    if unit is not None:
        if isinstance(unit, str) and unit.strip():
            options["native_unit_of_measurement"] = unit.strip()
        else:
            _LOGGER.error("Invalid unit for %s in %s: %s", key, log_context, unit)
            errors["base"] = "invalid_unit"
    else:
        dev_class = entry.get("device_class")
        if dev_class == "data_rate":
            options["native_unit_of_measurement"] = "Bps"
        elif dev_class == "power":
            options["native_unit_of_measurement"] = "W"
        elif dev_class == "temperature":
            options["native_unit_of_measurement"] = "°C"

    # vmap
    vmap = entry.get("vmap")
    if vmap:
        if entity_type == "switch":
            remapped_vmap = {}
            if "true" in vmap:
                remapped_vmap["on"] = vmap["true"]
                _LOGGER.debug("Remapped vmap key 'true' to 'on' for %s in %s", key, log_context)
            if "false" in vmap:
                remapped_vmap["off"] = vmap["false"]
                _LOGGER.debug("Remapped vmap key 'false' to 'off' for %s in %s", key, log_context)
            for k, v in vmap.items():
                if k not in ("true", "false"):
                    remapped_vmap[k] = v
            vmap = remapped_vmap
        try:
            validate_vmap(vmap, entity_type)
            options["vmap"] = vmap
        except ValueError as e:
            _LOGGER.error("Invalid vmap for %s in %s: %s", key, log_context, str(e))
            errors["base"] = "invalid_vmap"
    elif entity_type == "switch":
        options["vmap"] = {"on": "1", "off": "2"}
        _LOGGER.debug("Set default vmap {'on': '1', 'off': '2'} for switch %s in %s", key, log_context)

    # Ensure device_class always exists
    options["device_class"] = entry.get("device_class", None)

    return options

def validate_vmap(vmap, entity_type):

    # vmap must always be a dictionary
    if not isinstance(vmap, dict):
        raise ValueError("vmap must be a dictionary")

    # ------------------------------
    # Switch validation
    # ------------------------------
    if entity_type == "switch":
        valid_keys = {"on", "off"}
        alt_keys = {"1", "0"}
        # Accept only {"on","off"} or {"1","0"}
        if not (set(vmap.keys()) == valid_keys or set(vmap.keys()) == alt_keys):
            raise ValueError(
                "Switch vmap must be {'on': '<val>', 'off': '<val>'} "
                "or {'1': '<val>', '0': '<val>'}"
            )
        # All values must be strings
        if not all(isinstance(v, str) for v in vmap.values()):
            raise ValueError("Switch vmap values must be strings")

    # ------------------------------
    # Binary sensor validation
    # ------------------------------
    elif entity_type == "binary_sensor":
        valid_keys = {"on", "off"}
        alt_keys = {"1", "0"}
        # Accept {"on","off"} or {"1","0"}
        if not (set(vmap.keys()) == valid_keys or set(vmap.keys()) == alt_keys):
            raise ValueError(
                "Binary_sensor vmap must be {'on': [...], 'off': [...]} "
                "or {'1': '<val>', '0': '<val>'}"
            )
        # Iterate through each vmap entry
        for k, v in vmap.items():
            # If list: each element must be string or comparison
            if isinstance(v, list):
                for token in v:
                    if token.startswith(("<", ">")):
                        try:
                            float(token[1:])  # must parse as number
                        except ValueError:
                            raise ValueError(
                                f"Invalid vmap comparison value in {token}"
                            )
                    elif not isinstance(token, str):
                        raise ValueError(
                            "Binary_sensor vmap list values must be strings"
                        )
            # If single string: must be string
            elif not isinstance(v, str):
                raise ValueError("Binary_sensor vmap values must be strings")

    # ------------------------------
    # Sensor validation
    # ------------------------------
    elif entity_type == "sensor":
        for key, value in vmap.items():
            # Comparison key must be numeric
            if key.startswith(("<", ">")):
                try:
                    float(key[1:])
                except ValueError:
                    raise ValueError(
                        f"Invalid vmap comparison value in {key}"
                    )
            # Keys and values must be strings
            elif not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Sensor vmap keys and values must be strings")

    # ------------------------------
    # Other entity types: not supported
    # ------------------------------
    else:
        raise ValueError(f"Unsupported entity type {entity_type} for vmap")


def _log_oids_pretty(level: str, label: str, oids: dict, logger=_LOGGER) -> None:
    """Log OID structure in a human-readable multi-line format.

    Args:
        level: Log level ("debug", "info", "warning", "error")
        label: Header label for the log block
        oids: OID dict to format (configured_oids or validated_oids)
        logger: Logger instance
    """
    lines = [f"{label}:"]
    for section, entries in oids.items():
        lines.append(f"  {section}:")
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    if any(isinstance(v, dict) for v in entry.values()):
                        lines.append(f"    {key}:")
                        for attr, val in entry.items():
                            lines.append(f"      {attr}: {val}")
                    else:
                        oid = entry.get("oid", "na")
                        etype = entry.get("type", "?")
                        lines.append(f"    {key}: [{etype}] {oid}")
                else:
                    lines.append(f"    {key}: {entry}")
    log_fn = getattr(logger, level, logger.debug)
    log_fn("\n".join(lines))


class SnmpFlowHelper:
    """Helper class for shared flow step logic."""

    @staticmethod
    async def handle_settings(flow, user_input=None):
        """Handle the settings step."""
        _LOGGER.info("# Entering step settings")
        errors = {}
        if user_input is not None:
            _LOGGER.debug("        Step settings user_input: %s", user_input)
            try:
                if user_input.get(CONF_CUSTOM_OIDS):
                    user_input[CONF_CUSTOM_OIDS] = validate_custom_oids(user_input[CONF_CUSTOM_OIDS])
                user_input[CONF_POLLING_INTERVAL] = int(user_input[CONF_POLLING_INTERVAL])
                user_input[CONF_MAC_UPDATE_CYCLE] = int(user_input[CONF_MAC_UPDATE_CYCLE])
                flow._data.update(user_input)
                _LOGGER.debug("        Step settings updated data: %s", flow._data)
                _LOGGER.info("        Step settings validated, proceeding to credentials")
                _LOGGER.debug("        Step settings transitioning to credentials")
                return await flow.async_step_credentials()
            except ValueError as e:
                errors["base"] = str(e).lower().replace(" ", "_")
                _LOGGER.error("        Step settings validation error: %s", e)

        _LOGGER.debug("        Step settings rendering form with data: %s", flow._data)
        return flow.async_show_form(
            step_id="settings",
            data_schema=vol.Schema({
                vol.Required(CONF_SNMP_VERSION, default=flow._data.get(CONF_SNMP_VERSION, "v2c")): vol.In(SNMP_VERSIONS),
                vol.Required(CONF_POLLING_INTERVAL, default=int(flow._data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL)),
                vol.Required(CONF_MAC_UPDATE_CYCLE, default=int(flow._data.get(CONF_MAC_UPDATE_CYCLE, DEFAULT_MAC_UPDATE_CYCLE))): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MAC_UPDATE_CYCLE)),
                vol.Required(CONF_ENABLE_CONTROLS, default=flow._data.get(CONF_ENABLE_CONTROLS, False)): bool,
                vol.Optional(CONF_CUSTOM_OIDS, default=flow._data.get(CONF_CUSTOM_OIDS, "")): str,
            }),
            errors=errors,
        )

    @staticmethod
    async def handle_credentials(flow, user_input=None):
        """Handle credentials step."""
        _LOGGER.info("# Entering step credentials")
        errors = {}
        try:
            _LOGGER.debug("        Step credentials flow data: %s", flow._data)
            if user_input is not None:
                _LOGGER.debug("        Step credentials input: %s", user_input)
                if user_input.get(CONF_GO_BACK, False):
                    _LOGGER.info("        Step credentials user requested to go back to settings")
                    _LOGGER.debug("        Step credentials transitioning to settings")
                    return await flow.async_step_settings()
                flow._data.update(user_input)
                _LOGGER.debug("        Step credentials updated data: %s", flow._data)
                _LOGGER.info("        Step credentials provided, proceeding to test")
                _LOGGER.debug("        Step credentials transitioning to test")
                return await flow.async_step_test()

            _LOGGER.debug("        Step credentials preparing form")
            snmp_version = flow._data.get(CONF_SNMP_VERSION, "v2c")
            _LOGGER.debug("        Step credentials using SNMP version: %s", snmp_version)
            defaults = {
                CONF_GO_BACK: False,
                CONF_READ_COMMUNITY_STRING: flow._data.get(CONF_READ_COMMUNITY_STRING, ""),
                CONF_WRITE_COMMUNITY_STRING: flow._data.get(CONF_WRITE_COMMUNITY_STRING, ""),
                CONF_USERNAME: flow._data.get(CONF_USERNAME, ""),
                CONF_AUTH_PROTOCOL: flow._data.get(CONF_AUTH_PROTOCOL, "None"),
                CONF_AUTH_KEY: flow._data.get(CONF_AUTH_KEY, ""),
                CONF_PRIVACY_PROTOCOL: flow._data.get(CONF_PRIVACY_PROTOCOL, "None"),
                CONF_PRIVACY_KEY: flow._data.get(CONF_PRIVACY_KEY, "")
            }
            _LOGGER.debug("        Step credentials defaults: go_back=%s, read_community_string=%s, write_community_string=%s",
                        defaults[CONF_GO_BACK], defaults[CONF_READ_COMMUNITY_STRING], defaults[CONF_WRITE_COMMUNITY_STRING])

            _LOGGER.debug("        Step credentials building schema")
            schema = {
                vol.Optional(CONF_GO_BACK, default=defaults[CONF_GO_BACK]): bool,
                vol.Required(CONF_READ_COMMUNITY_STRING, default=defaults[CONF_READ_COMMUNITY_STRING]): str,
                vol.Optional(CONF_WRITE_COMMUNITY_STRING, default=defaults[CONF_WRITE_COMMUNITY_STRING]): str,
            } if snmp_version in ["v1", "v2c"] else {
                vol.Optional(CONF_GO_BACK, default=defaults[CONF_GO_BACK]): bool,
                vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME]): str,
                vol.Required(CONF_AUTH_PROTOCOL, default=defaults[CONF_AUTH_PROTOCOL]): vol.In(AUTH_PROTOCOLS),
                vol.Optional(CONF_AUTH_KEY, default=defaults[CONF_AUTH_KEY]): str,
                vol.Required(CONF_PRIVACY_PROTOCOL, default=defaults[CONF_PRIVACY_PROTOCOL]): vol.In(PRIVACY_PROTOCOLS),
                vol.Optional(CONF_PRIVACY_KEY, default=defaults[CONF_PRIVACY_KEY]): str,
            }
            _LOGGER.debug("        Step credentials schema created: %s", schema)

            _LOGGER.debug("        Step credentials creating form schema")
            form_schema = vol.Schema(schema)
            _LOGGER.debug("        Step credentials form schema: %s", form_schema.schema)
            _LOGGER.debug("        Step credentials schema validation complete")

            _LOGGER.debug("        Step credentials calling async_show_form")
            result = flow.async_show_form(
                step_id="credentials",
                data_schema=form_schema,
                errors=errors
            )
            _LOGGER.debug("        Step credentials form render result: %s", result)
            return result
        except Exception as e:
            _LOGGER.error("        Step credentials error: %s", e)
            errors["base"] = "form_error"
            _LOGGER.debug("        Step credentials returning fallback form")
            return flow.async_show_form(
                step_id="credentials",
                data_schema=vol.Schema({}),
                errors=errors
            )

    @staticmethod
    async def handle_test(flow, user_input=None):
        """Test SNMP connection using access_test_oid."""
        _LOGGER.info("# Entering step test")
        errors = {}
        credentials = SnmpCredentials(
            version=flow._data.get(CONF_SNMP_VERSION, "v2c"),
            read_community=flow._data.get(CONF_READ_COMMUNITY_STRING, ""),
            write_community=flow._data.get(CONF_WRITE_COMMUNITY_STRING)
        )
        client = await SnmpClient.create(flow._data[CONF_DEVICE_IP], credentials)

        vendor_oids = DEVICE_TYPE_OIDS[flow._data[CONF_DEVICE_TYPE]]
        test_oid = vendor_oids.get("config", {}).get("access_test_oid")
        if not test_oid:
            _LOGGER.error("Device type %s missing mandatory config.access_test_oid", flow._data[CONF_DEVICE_TYPE])
            return await flow.async_step_settings()  # go back to settings

        # Read test
        _LOGGER.info("        Starting SNMP read test with OID %s", test_oid)
        result = await client.async_get(test_oid)
        if result is None:
            _LOGGER.error("        SNMP read test failed for OID %s: No response", test_oid)
            return await flow.async_step_settings()  # go back to settings
        _LOGGER.debug("        SNMP read test successful for OID %s: %s", test_oid, result)

        # Write test if controls enabled
        if flow._data.get(CONF_ENABLE_CONTROLS, False):
            if not credentials.write_community:
                _LOGGER.error("        Write community string is required for controls")
                return await flow.async_step_settings()  # go back to settings

            test_value = "wr_test"
            _LOGGER.info("        Starting SNMP write test with OID %s, value: %s", test_oid, test_value)
            write_result = await client.async_set(test_oid, test_value, value_type="string")
            if not write_result:
                _LOGGER.error("        SNMP write test failed for OID %s", test_oid)
                return await flow.async_step_settings()  # go back to settings
            _LOGGER.debug("        SNMP write test successful for OID %s", test_oid)

            # Optional verify
            verify_result = await client.async_get(test_oid)
            if verify_result != test_value:
                _LOGGER.error("        SNMP write verification failed for OID %s: Expected %s, got %s", test_oid, test_value, verify_result)
                return await flow.async_step_settings()  # go back to settings
            _LOGGER.debug("        SNMP write verified for OID %s: %s", test_oid, verify_result)

            # Reset to empty
            reset_result = await client.async_set(test_oid, "", value_type="string")
            _LOGGER.debug("        SNMP reset %s for OID %s", "successful" if reset_result else "failed", test_oid)
        else:
            _LOGGER.debug("        SNMP write test skipped: Controls not enabled")

        _LOGGER.info("        Step test successful, proceeding to discover")
        return await flow.async_step_discover()

    @staticmethod
    async def handle_discover(flow, user_input=None):
        """Discover device attributes."""
        _LOGGER.info("# Entering step discover")
        errors = {}
        credentials = SnmpCredentials(
            version=flow._data.get(CONF_SNMP_VERSION, "v2c"),
            read_community=flow._data.get(CONF_READ_COMMUNITY_STRING, ""),
            write_community=flow._data.get(CONF_WRITE_COMMUNITY_STRING)
        )
        client = await SnmpClient.create(flow._data[CONF_DEVICE_IP], credentials)
        _LOGGER.debug("        Step discover created SNMP client")

        flow._device_info = {}

        vendor_oids = DEVICE_TYPE_OIDS[flow._data[CONF_DEVICE_TYPE]]

        # Handle attributes
        for attr, entry in vendor_oids.get("attributes", {}).items():
            oid = entry.get("oid", "na")
            if oid == "na":
                flow._device_info[attr] = "Unknown"
                _LOGGER.debug("        Step discover skipped %s: marked as 'na'", attr)
                continue
            try:
                if attr == "poe_port_list" and entry.get("mode") == "WalkforList":
                    port_count = int(flow._device_info.get("port_count", MAX_PORTS))
                    result = await client.async_get_subtree_idx_list(oid, max_ports=port_count)
                    poe_ports = sorted(result, key=int)  # Ensure numerical sorting
                    _LOGGER.debug("        Step discover raw poe_ports: %s", result)
                    _LOGGER.debug("        Step discover sorted poe_ports: %s", poe_ports)
                    flow._device_info["poe_ports"] = poe_ports
                    if poe_ports or result == []:  # Empty list is valid for no PoE ports
                        _LOGGER.info("        Step discover found %d PoE ports: %s", len(poe_ports), poe_ports)
                    else:
                        _LOGGER.debug("        Step discover no PoE ports found for OID %s", oid)
                else:
                    value = await client.async_get(oid)
                    if value is not None:  # Accept "" for no data
                        flow._device_info[attr] = value or "None"  # Store "None" for empty data
                        _LOGGER.info("        Step discover discovered %s: %s via OID %s", attr, value, oid)
                    else:
                        _LOGGER.warning("        Step discover OID %s for %s is invalid", oid, attr)
                        flow._device_info[attr] = "Unknown"
            except Exception as e:
                _LOGGER.warning("        Step discover failed to fetch %s with OID %s: %s", attr, oid, e)
                flow._device_info[attr] = "Unknown"
                if attr == "poe_port_list":
                    flow._device_info["poe_ports"] = []

        # Handle device attributes
        for attr, entry in vendor_oids.get("device", {}).items():
            oid = entry.get("oid", "na")
            if oid == "na":
                flow._device_info[attr] = "Unknown"
                _LOGGER.debug("        Step discover skipped %s: marked as 'na'", attr)
                continue
            try:
                value = await client.async_get(oid)
                if value is not None:  # Accept "" for no data
                    flow._device_info[attr] = value or "None"
                    _LOGGER.info("        Step discover discovered %s: %s via OID %s", attr, value, oid)
                else:
                    _LOGGER.warning("        Step discover OID %s for %s is invalid", oid, attr)
                    flow._device_info[attr] = "Unknown"
            except Exception as e:
                _LOGGER.warning("        Step discover failed to fetch %s with OID %s: %s", attr, oid, e)
                flow._device_info[attr] = "Unknown"

        # Fallback defaults
        device_type = flow._data.get(CONF_DEVICE_TYPE, "Unknown")
        manufacturer_fallback = device_type.split("_")[0].capitalize()
        flow._device_info.setdefault("manufacturer", manufacturer_fallback)
        flow._device_info.setdefault("model", "SNMP Device")
        flow._device_info.setdefault("port_count", "1")
        flow._device_info.setdefault("firmware", "Unknown")
        flow._device_info.setdefault("poe_ports", [])
        # get the excluded ports
        flow._device_info.setdefault("excluded_ports", vendor_oids.get("config", {}).get("port_exclude", []))

        _LOGGER.info("        Step discover device info: %s", flow._device_info)
        _LOGGER.info("        Step discover proceeding to parse_config")
        return await flow.async_step_parse_config()

    @staticmethod
    async def handle_parse_config(flow, user_input=None):
        """Parse configuration and structure OIDs."""
        _LOGGER.info("# Entering step parse_config")
        errors = {}
        try:
            # Validate user_input
            if user_input is not None and not isinstance(user_input, dict):
                _LOGGER.error("        Step parse_config received invalid user_input type: %s, expected None or dict", type(user_input))
                errors["base"] = "invalid_input"
                return flow.async_show_form(
                    step_id="settings",
                    data_schema=vol.Schema({
                        vol.Required(CONF_SNMP_VERSION, default=flow._data.get(CONF_SNMP_VERSION, "v2c")): vol.In(SNMP_VERSIONS),
                        vol.Required(CONF_POLLING_INTERVAL, default=int(flow._data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL)),
                        vol.Required(CONF_MAC_UPDATE_CYCLE, default=int(flow._data.get(CONF_MAC_UPDATE_CYCLE, DEFAULT_MAC_UPDATE_CYCLE))): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MAC_UPDATE_CYCLE)),
                        vol.Required(CONF_ENABLE_CONTROLS, default=flow._data.get(CONF_ENABLE_CONTROLS, False)): bool,
                        vol.Optional(CONF_CUSTOM_OIDS, default=flow._data.get(CONF_CUSTOM_OIDS, "")): str,
                    }),
                    errors=errors
                )

            vendor_oids = DEVICE_TYPE_OIDS[flow._data[CONF_DEVICE_TYPE]]
            enable_controls = flow._data.get(CONF_ENABLE_CONTROLS, False)
            port_count = int(flow._device_info.get("port_count", 1))
            poe_ports = [int(port) for port in flow._device_info.get("poe_ports", [])]
            excluded_ports = flow._device_info.get("excluded_ports", [])

            # Initialize configured_oids
            flow._configured_oids = {"attributes": {}, "device": {}, "ports": {}}

            # Parse attributes and device sections
            for section in ["attributes", "device"]:
                for key, entry in vendor_oids.get(section, {}).items():
                    errors = {}  # reset per entry
                    if not isinstance(entry, dict):
                        _LOGGER.error("Invalid entry for %s in %s: %s, expected dict", key, section, entry)
                        errors["base"] = "invalid_config"
                        return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors=errors)

                    oid = entry.get("oid", "na")
                    if oid == "na":
                        _LOGGER.debug("Skipping OID %s in %s: marked as 'na'", key, section)
                        continue

                    _configured_entry = entry.copy()

                    # Normalize entity type
                    entity_type = entry.get("type", "sensor").lower()
                    if entity_type in ("mac_table", "mac_port"):
                        flow._configured_oids[section][key] = entry
                        _LOGGER.debug("Added special MAC OID for %s in %s: %s", key, section, entry)
                        continue
                    if entity_type not in ALLOWED_TYPES:
                        error_msg = f"Invalid type '{entity_type}'. Allowed types are: {', '.join(ALLOWED_TYPES)}"
                        _LOGGER.error(error_msg)
                        errors["base"] = "invalid_type"
                        return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors={"base": error_msg})
                    _configured_entry["type"] = entity_type

                    #  Process Options  calc/math/unit/vmap handling
                    options = _process_options(entry, key, section, entity_type, errors, section)
                    _configured_entry.update(options)

                    if errors.get("base"):
                        return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors=errors)

                    # Adjust types if controls disabled
                    if section == "device" and entity_type == "switch" and not enable_controls:
                        _configured_entry["type"] = "binary_sensor"
                        _LOGGER.warning("Adjusted type for %s to binary_sensor (read-only)", key)

                    elif section == "device" and entity_type == "text" and not enable_controls:
                        _configured_entry["type"] = "text_sensor"
                        _LOGGER.warning("Adjusted type for %s to text_sensor (read-only)", key)

                    flow._configured_oids[section][key] = _configured_entry
                    _LOGGER.debug("Added OID for %s in %s: %s", key, section, _configured_entry)

            # Parse ports section
            if "ports" in vendor_oids:
                for port in range(1, port_count + 1):
                    if port in excluded_ports:
                        _LOGGER.info("Skipping excluded port %s", port)
                        continue
                    port_key = f"p{port:02d}"
                    flow._configured_oids["ports"][port_key] = {}
                    try:
                        for key, entry in vendor_oids["ports"].items():
                            errors = {}  # reset per entry
                            _LOGGER.debug("        Processing OID %s for %s", key, port_key)
                            if not isinstance(entry, dict):
                                _LOGGER.error("        Invalid entry for %s in ports for %s: %s, expected dict", key, port_key, entry)
                                errors["base"] = "invalid_config"
                                return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors=errors)

                            oid = entry.get("oid", "na")
                            if oid == "na":
                                _LOGGER.debug("        Skipping OID %s for %s in ports: marked as 'na'", key, port_key)
                                continue
                            if key.startswith("poe_") and port not in poe_ports:
                                _LOGGER.debug("        Skipping PoE attribute %s for non-PoE port %s", key, port_key)
                                continue

                            port_oid = f"{oid}.{port}"
                            _configured_entry = entry.copy()
                            _configured_entry["oid"] = port_oid

                            entity_type = entry.get("type", "sensor").lower()
                            if entity_type not in ALLOWED_TYPES:
                                _LOGGER.error("        Invalid type %s for %s in %s", entity_type, key, port_key)
                                errors["base"] = "invalid_type"
                                return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors=errors)
                            _configured_entry["type"] = entity_type

                            # 🔹 replace manual calc/math/unit/vmap with helper
                            options = _process_options(entry, key, port_key, entity_type, errors, port_key)
                            _configured_entry.update(options)

                            if errors.get("base"):
                                return flow.async_show_form(step_id="settings", data_schema=vol.Schema({}), errors=errors)

                            # Adjust type if controls disabled
                            if entity_type == "switch" and not enable_controls:
                                _configured_entry["type"] = "binary_sensor"
                                _LOGGER.debug("        Adjusted type for %s to binary_sensor (read-only) for %s", key, port_key)
                            elif entity_type == "text" and not enable_controls:
                                _configured_entry["type"] = "text_sensor"
                                _LOGGER.debug("        Adjusted type for %s to text_sensor (read-only) for %s", key, port_key)

                            flow._configured_oids["ports"][port_key][key] = _configured_entry
                            _LOGGER.debug("        Added OID for %s in %s: %s", key, port_key, _configured_entry)

                            # Add a small delay to prevent async overload with many ports
                            await asyncio.sleep(0.01)
                    except Exception as e:
                        _LOGGER.error("        Error processing port %s, key %s: %s", port_key, key, e)
                        errors["base"] = "port_processing_error"
                        return flow.async_show_form(
                            step_id="settings",
                            data_schema=vol.Schema({
                                vol.Required(CONF_SNMP_VERSION, default=flow._data.get(CONF_SNMP_VERSION, "v2c")): vol.In(SNMP_VERSIONS),
                                vol.Required(CONF_POLLING_INTERVAL, default=int(flow._data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL)),
                                vol.Required(CONF_MAC_UPDATE_CYCLE, default=int(flow._data.get(CONF_MAC_UPDATE_CYCLE, DEFAULT_MAC_UPDATE_CYCLE))): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MAC_UPDATE_CYCLE)),
                                vol.Required(CONF_ENABLE_CONTROLS, default=flow._data.get(CONF_ENABLE_CONTROLS, False)): bool,
                                vol.Optional(CONF_CUSTOM_OIDS, default=flow._data.get(CONF_CUSTOM_OIDS, "")): str,
                            }),
                            errors=errors
                        )

            # Parse custom OIDs
            if flow._data.get(CONF_CUSTOM_OIDS):
                for name, oid in flow._data[CONF_CUSTOM_OIDS]:
                    _configured_custom_entry = {
                        "oid": oid,
                        "type": "sensor",
                        "device_class": None,
                        "calc": "direct"
                        # "math": "..."  # optional, if user wants to scale the custom OID
                    }

                    flow._configured_oids["device"][f"custom_{name}"] = _configured_custom_entry
                    _LOGGER.debug("        Added custom OID: %s = %s, type=sensor, calc=direct", name, oid)

            ###_LOGGER.warning("First port after parsing (Configured Oids): %s -> %s", *(lambda p=flow._configured_oids.get("ports", {}): (next(iter(p), "None"), p.get(next(iter(p), "None"), "None")))())
            _log_oids_pretty("debug", "Configured OIDs", flow._configured_oids)


            _LOGGER.info("        Step parse_config completed, proceeding to validate")
            return await flow.async_step_validate()
        except Exception as e:
            _LOGGER.error("        Step parse_config error: %s", e)
            errors["base"] = "parse_config_error"
            return flow.async_show_form(
                step_id="settings",
                data_schema=vol.Schema({
                    vol.Required(CONF_SNMP_VERSION, default=flow._data.get(CONF_SNMP_VERSION, "v2c")): vol.In(SNMP_VERSIONS),
                    vol.Required(CONF_POLLING_INTERVAL, default=int(flow._data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL)),
                    vol.Required(CONF_MAC_UPDATE_CYCLE, default=int(flow._data.get(CONF_MAC_UPDATE_CYCLE, DEFAULT_MAC_UPDATE_CYCLE))): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MAC_UPDATE_CYCLE)),
                    vol.Required(CONF_ENABLE_CONTROLS, default=flow._data.get(CONF_ENABLE_CONTROLS, False)): bool,
                    vol.Optional(CONF_CUSTOM_OIDS, default=flow._data.get(CONF_CUSTOM_OIDS, "")): str,
                }),
                errors=errors
            )

    @staticmethod
    async def handle_validate(flow, user_input=None):
        """Validate OIDs by testing configured OIDs against the SNMP device."""
        _LOGGER.info("# Entering step validate")
        errors = {}

        # Always start fresh for validated_oids
        flow._validated_oids = {"attributes": {}, "device": {}, "ports": {}}

        # Prepare SNMP client
        credentials = SnmpCredentials(
            version=flow._data.get(CONF_SNMP_VERSION, "v2c"),
            read_community=flow._data.get(CONF_READ_COMMUNITY_STRING, ""),
            write_community=flow._data.get(CONF_WRITE_COMMUNITY_STRING)
        )
        client = await SnmpClient.create(flow._data[CONF_DEVICE_IP], credentials)

        # ------------------------------------------------------------------
        # Validate attributes + device sections
        # ------------------------------------------------------------------
        for section in ["attributes", "device"]:
            configured_section = flow._configured_oids.get(section, {})
            for key, entry in list(configured_section.items()):
                oid = entry.get("oid", "na")
                if oid == "na":
                    _LOGGER.debug("        Skipping OID %s in %s: marked as 'na'", key, section)
                    continue
                # Special case: MAC OIDs → validate via helper, skip scalar GET
                if entry.get("type") in ("mac_table", "mac_port"):
                    if await validate_mac_oid(client, oid, key, section, _LOGGER):
                        flow._validated_oids[section][key] = entry
                        _LOGGER.info(        "Validated Mac Table OID for %s in %s: %s",        key, section, entry    )
                    continue

                try:
                    value = await client.async_get(oid)
                    if value is not None and not (isinstance(value, str) and value.startswith("No Such")):


                        # Case 1: calc = diff  → must be numeric
                        if entry.get("calc") == "diff":
                            try:
                                float(value)
                            except (ValueError, TypeError):
                                _LOGGER.warning("        OID %s for %s in %s is not numeric for calc=diff", oid, key, section)
                                continue
                        # Case 2: math present  → must be numeric
                        elif entry.get("math"):
                            try:
                                float(value)
                            except (ValueError, TypeError):
                                _LOGGER.warning("        OID %s for %s in %s is not numeric but math was defined", oid, key, section)
                                continue

                        # Case 3: vmap with numeric comparisons
                        if "vmap" in entry:
                            for vmap_key in entry["vmap"].keys():
                                if vmap_key.startswith(("<", ">")):
                                    try:
                                        float(vmap_key[1:])
                                    except ValueError:
                                        _LOGGER.warning("        Invalid vmap comparison %s for %s in %s", vmap_key, key, section)
                                        continue

                        # If we reach here, OID is valid → keep it
                        flow._validated_oids[section][key] = entry
                        _LOGGER.debug("        Validated OID for %s in %s: %s (value=%s)", key, section, entry, value)
                    else:
                        _LOGGER.warning("        Invalid OID %s for %s in %s (value=%s)",oid, key, section, value)
                except Exception as e:
                    _LOGGER.warning("        Failed to validate OID %s for %s in %s: %s", oid, key, section, e)

        # ------------------------------------------------------------------
        # Validate ports section
        # ------------------------------------------------------------------
        for port_key, port_attrs in flow._configured_oids.get("ports", {}).items():
            flow._validated_oids["ports"][port_key] = {}
            for key, entry in list(port_attrs.items()):
                oid = entry.get("oid", "na")
                if oid == "na":
                    _LOGGER.debug("        Skipping OID %s in %s: marked as 'na'", key, port_key)
                    continue
                try:
                    value = await client.async_get(oid)
                    if value is not None and not (isinstance(value, str) and value.startswith("No Such")):
                        if entry.get("calc") in ["diff"]:
                            try:
                                float(value)
                            except (ValueError, TypeError):
                                _LOGGER.warning("        Port OID %s for %s not numeric for calc=%s",
                                                oid, key, entry.get("calc"))
                                continue
                        if "vmap" in entry:
                            for vmap_key in entry["vmap"].keys():
                                if vmap_key.startswith(("<", ">")):
                                    try:
                                        float(vmap_key[1:])
                                    except ValueError:
                                        _LOGGER.warning("        Invalid vmap comparison %s for port %s key %s",
                                                        vmap_key, port_key, key)
                                        continue
                        # If valid → add to validated_oids
                        flow._validated_oids["ports"][port_key][key] = entry
                        _LOGGER.debug("        Validated OID for %s in %s: %s (value=%s)",
                                    key, port_key, entry, value)
                    else:
                        _LOGGER.warning("        Invalid OID %s for %s in %s (value=%s)",
                                        oid, key, port_key, value)
                except Exception as e:
                    _LOGGER.warning("        Failed to validate port OID %s for %s in %s: %s",
                                    oid, key, port_key, e)

        # ------------------------------------------------------------------
        # Final summary check
        # ------------------------------------------------------------------
        total = (
            len(flow._validated_oids["attributes"]) +
            len(flow._validated_oids["device"]) +
            sum(len(p) for p in flow._validated_oids["ports"].values())
        )
        _LOGGER.info("        Validated OIDs summary: %d total (Attributes=%d, Device=%d, Ports=%d)",
                    total,
                    len(flow._validated_oids["attributes"]),
                    len(flow._validated_oids["device"]),
                    sum(len(p) for p in flow._validated_oids["ports"].values()))

        if total == 0:
            _LOGGER.error("        No valid OIDs found, cannot proceed")
            errors["base"] = "no_valid_oids"
            return await flow.async_step_settings()
        _log_oids_pretty("info", "Validated OIDs", flow._validated_oids)
        # Warn about OIDs that were configured but did not pass validation
        for section in ["device", "attributes"]:
            for key in flow._configured_oids.get(section, {}):
                if key not in flow._validated_oids.get(section, {}):
                    _LOGGER.warning("OID configured but not validated: [%s] %s", section, key)
        for port_key, port_attrs in flow._configured_oids.get("ports", {}).items():
            for key in port_attrs:
                if key not in flow._validated_oids.get("ports", {}).get(port_key, {}):
                    _LOGGER.warning("OID configured but not validated: [%s] %s -> %s", port_key, port_key, key)

        return await flow.async_step_present()

    @staticmethod
    async def handle_present(flow, user_input=None):
        """Present entities to user."""
        _LOGGER.info("# Entering step present")
        errors = {}
        if user_input is not None:
            confirm = user_input.get(CONF_CONFIRM, True)
            if not confirm:
                _LOGGER.warning("User unchecked confirm → aborting entire config flow")
                return flow.async_abort(reason="user_aborted")

            _LOGGER.info("Step present entities confirmed, proceeding to finish")
            return await flow.async_step_finish()

        # Buckets
        sensors = {"device": [], "ports": set()}
        binary_sensors = {"device": [], "ports": set()}
        switches = {"device": [], "ports": set()}
        texts = {"device": [], "ports": set()}
        text_sensors = {"device": [], "ports": set()}
        poe_entity_list = []
        poe_port_entities = set()

        # Populate buckets
        for section in ("device", "ports"):
            if section == "device":
                for key, entry in flow._validated_oids.get(section, {}).items():
                    entity_type = entry.get("type", "sensor")
                    name = key.replace("_", " ").title()
                    if key.startswith("poe_"):
                        poe_entity_list.append(f"{name} ({entity_type})")
                    elif entity_type == "switch":
                        switches["device"].append(name)
                    elif entity_type == "binary_sensor":
                        binary_sensors["device"].append(name)
                    elif entity_type == "sensor":
                        sensors["device"].append(name)
                    elif entity_type == "text":
                        texts["device"].append(name)
                    elif entity_type == "text_sensor":
                        text_sensors["device"].append(name)
            else:  # ports
                for port_key, port_attrs in flow._validated_oids.get(section, {}).items():
                    for key, entry in port_attrs.items():
                        entity_type = entry.get("type", "sensor")
                        name = key.replace("port_", "").replace("_", " ").title()
                        if key.startswith("poe_"):
                            poe_port_entities.add(f"{name} ({entity_type})")
                        elif entity_type == "switch":
                            switches["ports"].add(name)
                        elif entity_type == "binary_sensor":
                            binary_sensors["ports"].add(name)
                        elif entity_type == "sensor":
                            sensors["ports"].add(name)
                        elif entity_type == "text":
                            texts["ports"].add(name)
                        elif entity_type == "text_sensor":
                            text_sensors["ports"].add(name)

        # Device info
        device_info = flow._device_info
        excluded_ports = device_info.get("excluded_ports", [])
        poe_ports = device_info.get("poe_ports", [])
        poe_budget = device_info.get("poe_budget", "Unknown")

        device_info_text = (
            f"{device_info.get('manufacturer', 'na')}, "
            f"{device_info.get('model', 'na')}, "
            f"{device_info.get('serial', 'na')}, "
            f"{device_info.get('firmware', 'na')}, "
            f"{device_info.get('port_count', 'na')}, "
            f"{poe_budget}"
        )
        if excluded_ports:
            device_info_text += f", Excluded Ports: {excluded_ports}"

        # Build PoE + MAC summaries
        poe_port_count = len(poe_ports)
        poe_entities_text = ", ".join(sorted(poe_entity_list)) if poe_entity_list else "None"
        device_poe_text = f"PoE Port Count: {poe_port_count}, PoE Budget: {poe_budget}, Entities: {poe_entities_text}"

        poe_port_entities_text = (
            f"PoE Port Entities: {poe_port_count} ports - "
            f"{', '.join(sorted(poe_port_entities)) if poe_port_entities else 'None'}"
        )

        has_mac = any(e.get("type") in ("mac_table", "mac_port") for e in flow._validated_oids.get("device", {}).values())
        mac_text = f"MAC Table + {flow._device_info.get('port_count', 0)} port switches" if has_mac else "None"

        # Build schema dynamically in correct order
        schema_dict = {}
        schema_dict[vol.Optional(CONF_DEVICE_INFO, default=device_info_text)] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))

        if sensors["device"]:
            schema_dict[vol.Optional("device_sensors", default=", ".join(sorted(sensors["device"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if binary_sensors["device"]:
            schema_dict[vol.Optional("device_binary_sensors", default=", ".join(sorted(binary_sensors["device"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if switches["device"]:
            schema_dict[vol.Optional("device_switches", default=", ".join(sorted(switches["device"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if texts["device"]:
            schema_dict[vol.Optional("device_texts", default=", ".join(sorted(texts["device"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if text_sensors["device"]:
            schema_dict[vol.Optional("device_text_sensors", default=", ".join(sorted(text_sensors["device"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))

        if sensors["ports"]:
            schema_dict[vol.Optional("per_port_sensors", default=", ".join(sorted(sensors["ports"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if binary_sensors["ports"]:
            schema_dict[vol.Optional("per_port_binary_sensors", default=", ".join(sorted(binary_sensors["ports"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if switches["ports"]:
            schema_dict[vol.Optional("per_port_switches", default=", ".join(sorted(switches["ports"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if texts["ports"]:
            schema_dict[vol.Optional("per_port_texts", default=", ".join(sorted(texts["ports"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        if text_sensors["ports"]:
            schema_dict[vol.Optional("per_port_text_sensors", default=", ".join(sorted(text_sensors["ports"])))] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))

        schema_dict[vol.Optional("device_poe", default=device_poe_text)] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))
        schema_dict[vol.Optional("poe_port_entities", default=poe_port_entities_text)] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))


        schema_dict[vol.Optional("device_mac", default=f"MAC Entities: {mac_text}")] = selector.TextSelector(selector.TextSelectorConfig(multiline=False))

        schema_dict[vol.Required(CONF_CONFIRM, default=True)] = bool

        return flow.async_show_form(
            step_id="present",
            data_schema=vol.Schema(schema_dict),
            errors=errors
        )



    @staticmethod
    async def handle_finish(flow, user_input=None):
        """Finalize the config entry."""
        _LOGGER.info("# Entering step finish")

        entity_registry = async_get_entity_registry(flow.hass)
        device_registry = async_get_device_registry(flow.hass)

        config_entry_id = getattr(flow, "_entry_id", None) if flow._is_reconfigure else None
        device_ip = flow._data[CONF_DEVICE_IP]
        entities_to_delete = []

        # Collect entity IDs to delete
        for entity_id, entity in entity_registry.entities.items():
            if config_entry_id and entity.config_entry_id == config_entry_id:
                entities_to_delete.append(entity_id)
            elif entity.device_id:
                device = device_registry.async_get(entity.device_id)
                if device and (DOMAIN, device_ip) in device.identifiers:
                    entities_to_delete.append(entity_id)

        # Remove entities
        for entity_id in entities_to_delete:
            entity_registry.async_remove(entity_id)
        _LOGGER.info("        Step finish queued %d entities for removal", len(entities_to_delete))

        await asyncio.sleep(0)  # let removals flush

        # Save validated OIDs & device info
        flow._data[CONF_VALIDATED_OIDS] = flow._validated_oids
        flow._data[CONF_DEVICE_INFO] = flow._device_info

        if flow._is_reconfigure:
            entry = flow.hass.config_entries.async_get_entry(flow._entry_id)
            if entry:
                flow.hass.config_entries.async_update_entry(entry, data=flow._data)
                _LOGGER.info("        Step finish updated config entry: %s", flow._entry_id)
                # Reload only during reconfigure
            return flow.async_create_entry(title="", data={})

        # First-time setup → no reload yet
        return flow.async_create_entry(
            title=flow._data[CONF_DEVICE_NAME],
            data=flow._data,
        )



    @staticmethod
    def create_client_params(flow):
        """Create client credentials for SNMP client."""
        _LOGGER.info("# Entering create_client_params")
        credentials = SnmpCredentials(
            version=flow._data.get(CONF_SNMP_VERSION, "v2c"),
            read_community=flow._data.get(CONF_READ_COMMUNITY_STRING, ""),
            write_community=flow._data.get(CONF_WRITE_COMMUNITY_STRING),
            username=flow._data.get(CONF_USERNAME),
            auth_protocol=flow._data.get(CONF_AUTH_PROTOCOL),
            auth_key=flow._data.get(CONF_AUTH_KEY),
            privacy_protocol=flow._data.get(CONF_PRIVACY_PROTOCOL),
            privacy_key=flow._data.get(CONF_PRIVACY_KEY)
        )
        _LOGGER.debug("        Step create_client_params created credentials: version=%s, read_community=%s, write_community=%s",
                    credentials.version, credentials.read_community, credentials.write_community)
        return credentials



class SnmpFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for snmp_r1d1."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the flow."""
        self._data = {CONF_SNMP_VERSION: "v2c"}
        self._configured_oids = {}
        self._validated_oids = {}
        self._device_info = {}
        self._firmware_value = "Unknown"
        self._is_reconfigure = False

    async def async_step_user(self, user_input=None):
        """Handle the initial step for basic device info."""
        _LOGGER.info("Starting user input step")
        errors = {}
        if user_input is not None:
            try:
                ipaddress.ip_address(user_input[CONF_DEVICE_IP])
                self._data.update(user_input)
                _LOGGER.debug("User step data: %s", self._data)
                _LOGGER.info("Basic device info validated, proceeding to settings step")
                _LOGGER.debug("Transitioning to settings step")
                return await self.async_step_settings()
            except ValueError as e:
                errors["base"] = str(e).lower().replace(" ", "_")
                _LOGGER.error(f"Validation error: %s", e)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_IP, default=self._data.get(CONF_DEVICE_IP, "")): str,
                vol.Required(CONF_DEVICE_NAME, default=self._data.get(CONF_DEVICE_NAME, "")): str,
                vol.Required(CONF_DEVICE_TYPE, default=self._data.get(CONF_DEVICE_TYPE, "zyxel")): vol.In(list(DEVICE_TYPE_OIDS.keys())),

            }),
            errors=errors,
        )

    async def async_step_settings(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_settings")
        _LOGGER.debug("Settings flow data: %s", self._data)
        return await SnmpFlowHelper.handle_settings(self, user_input)

    async def async_step_credentials(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_credentials")
        _LOGGER.debug("Credentials flow data: %s", self._data)
        return await SnmpFlowHelper.handle_credentials(self, user_input)

    async def async_step_test(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_test")
        _LOGGER.debug("Test flow data: %s", self._data)
        return await SnmpFlowHelper.handle_test(self, user_input)

    async def async_step_discover(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_discover")
        _LOGGER.debug("Discover flow data: %s", self._data)
        return await SnmpFlowHelper.handle_discover(self, user_input)

    async def async_step_parse_config(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_parse_config")
        _LOGGER.debug("Parse config flow data: %s", self._data)
        return await SnmpFlowHelper.handle_parse_config(self, user_input)

    async def async_step_validate(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_validate")
        _LOGGER.debug("Validate flow data: %s", self._data)
        return await SnmpFlowHelper.handle_validate(self, user_input)

    async def async_step_present(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_present")
        _LOGGER.debug("Present flow data: %s", self._data)
        return await SnmpFlowHelper.handle_present(self, user_input)

    async def async_step_finish(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("Entered async_step_finish")
        _LOGGER.debug("Finish flow data: %s", self._data)
        return await SnmpFlowHelper.handle_finish(self, user_input)

    def _create_client_params(self):
        """Delegate to helper."""
        return SnmpFlowHelper.create_client_params(self)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return SnmpOptionsFlow(config_entry)

class SnmpOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for snmp_r1d1."""

    def __init__(self, config_entry):
        """Initialize the options flow."""
        self._data = {}
        self._mode = None
        self._entry_id = config_entry.entry_id
        self._is_reconfigure = False
        self._validated_oids = {}
        self._device_info = {}
        self._firmware_value = "Unknown"
        self._device_name = config_entry.data.get(CONF_DEVICE_NAME, "this device")
        self._config_data = dict(config_entry.data)


    async def async_step_init(self, user_input=None):
        """Handle the initial step when clicking 'Configure'."""
        _LOGGER.info("# Entering step init (skipping menu, going to reconfigure)")
        self._mode = "reconfigure"
        return await self.async_step_reconfigure()


    async def async_step_reconfigure(self, user_input=None):
        """Start reconfiguration."""
        _LOGGER.info("# Entering step reconfigure")
        self._data = dict(self._config_data)
        _LOGGER.debug("        Step reconfigure loaded data: %s", self._data)
        self._is_reconfigure = True
        _LOGGER.info("        Step reconfigure proceeding to settings")
        _LOGGER.debug("        Step reconfigure transitioning to settings")
        return await self.async_step_settings()

    async def async_step_settings(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step settings (options)")
        _LOGGER.debug("        Step settings flow data: %s", self._data)
        return await SnmpFlowHelper.handle_settings(self, user_input)

    async def async_step_credentials(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step credentials (options)")
        _LOGGER.debug("        Step credentials flow data: %s", self._data)
        return await SnmpFlowHelper.handle_credentials(self, user_input)

    async def async_step_test(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step test (options)")
        _LOGGER.debug("        Step test flow data: %s", self._data)
        return await SnmpFlowHelper.handle_test(self, user_input)

    async def async_step_discover(self, user_input=None):

        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step discover (options)")
        _LOGGER.debug("        Step discover flow data: %s", self._data)
        return await SnmpFlowHelper.handle_discover(self, user_input)
    async def async_step_parse_config(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step parse_config (options)")
        _LOGGER.debug("        Step parse_config flow data: %s", self._data)
        return await SnmpFlowHelper.handle_parse_config(self, user_input)

    async def async_step_validate(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step validate (options)")
        _LOGGER.debug("        Step validate flow data: %s", self._data)
        return await SnmpFlowHelper.handle_validate(self, user_input)

    async def async_step_present(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step present (options)")
        _LOGGER.debug("        Step present flow data: %s", self._data)
        return await SnmpFlowHelper.handle_present(self, user_input)

    async def async_step_finish(self, user_input=None):
        """Handled by SnmpFlowHelper."""
        _LOGGER.info("# Entering step finish (options)")
        _LOGGER.debug("        Step finish flow data: %s", self._data)
        result = await SnmpFlowHelper.handle_finish(self, user_input)
        if self._is_reconfigure:
            # Reload the integration to ensure platforms are re-set up
            _LOGGER.info("Reloading integration platforms for entry: %s", self._entry_id)
            await self.hass.config_entries.async_reload(self._entry_id)
        return result

    async def async_step_delete(self, user_input=None):
        """Handle device deletion."""
        errors = {}
        if user_input is not None:
            if user_input.get("confirm", False):
                await self.hass.config_entries.async_remove(self._entry_id)
                device_registry = async_get_device_registry(self.hass)
                device = next(
                    (d for d in device_registry.devices.values() if self._entry_id in getattr(d, 'config_entries', set())),
                    None
                )
                if device:
                    device_registry.async_remove_device(device.id)
                return self.async_abort(reason="deleted")
            errors["base"] = "confirmation_required"
        return self.async_show_form(
            step_id="delete",
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): bool
            }),
            errors=errors,
            description_placeholders={"title": "Confirm Deletion"}
        )

    def _create_client_params(self):
        """Delegate to helper."""
        return SnmpFlowHelper.create_client_params(self)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return SnmpOptionsFlow(config_entry)