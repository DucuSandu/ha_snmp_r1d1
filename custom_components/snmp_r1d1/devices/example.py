# custom_components/snmp_r1d1/devices/ubnt_edgerouter.py

# OPTIONALS: Calculation, Unit, and Value Mapping Options for OIDs.
# These options customize how raw SNMP values are processed and displayed for sensors, binary sensors, and switches.
# Structure:
#
# - "calc": (Optional) Defines the calculation applied to the raw value.
#   - Types:
#     - "direct": No calculation, returns raw value (default, optional).
#     - "diff": Rate of change (current - previous) / elapsed time, used for counters.
#   - Note: To invert a diff (e.g., for upload/download inversion), use "math": "-x" together with calc="diff".
#
# - "math": (Optional) Apply a math formula string with variable x to the final value (after calc if present, or directly to the raw SNMP value if no calc is used)
#   - Examples:
#       "x/100"       → divide by 100
#       "100*x"       → multiply by 100
#       "(x/2)*10"    → divide by 2 then multiply by 10
#       "-x"          → invert the value
#   - Any valid Python math expression is allowed, using `x` as the variable.
#   - Supports standard math functions: sin, cos, log, sqrt, etc.
#
# - "unit": (Optional) Specifies the unit of measurement (e.g., "W", "%", "Mbit/s").
#   - Overrides native_unit_of_measurement.
#   - If omitted, falls back to a unit derived from device_class (e.g., "W" for power, "°C" for temperature).
#
# - "vmap": (Optional) Maps raw SNMP values to states or labels (value mapping).
#   - For sensor: {"<raw_value>": "<state>"} or {"<operator><value>": "<state>"}.
#       Operators: "<", ">" for numeric comparisons.
#       Example: {"0": "off", ">0": "delivering"}
#   - For binary_sensor: {"on": "<value|list>", "off": "<value|list>"}.
#       Lists can contain multiple values or comparisons, e.g., {"on": [">0"], "off": ["0"]}.
#   - For switch: {"on": "<value>", "off": "<value>"}.
#       Only exact values allowed, since switches require precise states.
#
# - Validation: calc, math, unit, and vmap are validated in config_flow during parse_config.
#
# Examples:
#   "poe_usage": {"oid": "...", "type": "sensor", "calc": "diff", "math": "x/1000", "unit": "W"}  # diff in mW → W
#   "cpu_usage": {"oid": "...", "type": "sensor", "unit": "%"}                                   # percentage
#   "port_speed": {"oid": "...", "type": "sensor", "device_class": "data_rate", "math": "x/1000000", "unit": "Mbit/s"}  # bps → Mbps
#   "poe_status": {"oid": "...", "type": "sensor", "vmap": {"0": "off", "1": "waiting", ">1": "delivering"}}            # multi-state
#   "port_status": {"oid": "...", "type": "binary_sensor", "vmap": {"on": [">0"], "off": ["0"]}}                        # binary with comparison
#   "port_admin": {"oid": "...", "type": "switch", "vmap": {"on": "1", "off": "2"}}                                     # binary switch
#
# INTEGER {disabled(1), searching(2), deliveringPower(3), fault(4), test(5), otherFault(6)}# custom_components/snmp_r1d1/devices/generic.py

config = {
    "access_test_oid": "1.3.6.1.2.1.1.4.0",  # sysContact (readable, writable if controls enabled) used for SNMP access test
}

attributes = {
    "manufacturer": {"oid": "na"},
    "model": {"oid": "na"},
    "serial": {"oid": "na"},
    "firmware": {"oid": "1.3.6.1.2.1.1.1.0"},  # sysDescr
    "sys_object_id": {"oid": "1.3.6.1.2.1.1.2.0"},  # sysObjectID
    "port_count": {"oid": "1.3.6.1.2.1.2.1.0"},  # ifNumber
    "poe_budget": {"oid": "na"},
    "poe_port_list": {"oid": "na"},
}

device = {
    "sys_name": {"oid": "1.3.6.1.2.1.1.5.0", "type": "text"},
    "sys_uptime": {"oid": "1.3.6.1.2.1.1.3.0", "type": "sensor", "calc": "direct"},
    # CPU (UCD-SNMP)
    "cpu_user": {"oid": "1.3.6.1.4.1.2021.11.9.0", "type": "sensor", "unit": "%"},
    "cpu_system": {"oid": "1.3.6.1.4.1.2021.11.10.0", "type": "sensor", "unit": "%"},
    "cpu_idle": {"oid": "1.3.6.1.4.1.2021.11.11.0", "type": "sensor", "unit": "%"},
    # Load averages
    "load_1": {"oid": "1.3.6.1.4.1.2021.10.1.3.1", "type": "sensor"},
    "load_5": {"oid": "1.3.6.1.4.1.2021.10.1.3.2", "type": "sensor"},
    "load_15": {"oid": "1.3.6.1.4.1.2021.10.1.3.3", "type": "sensor"},
    # Memory
    "mem_total": {"oid": "1.3.6.1.4.1.2021.4.5.0", "type": "sensor", "unit": "kB"},
    "mem_free": {"oid": "1.3.6.1.4.1.2021.4.6.0", "type": "sensor", "unit": "kB"},
    "mem_cached": {"oid": "1.3.6.1.4.1.2021.4.15.0", "type": "sensor", "unit": "kB"},
}

ports = {
    "port_name": {"oid": "1.3.6.1.2.1.31.1.1.1.1", "type": "text"},  # ifName
    "port_alias": {"oid": "1.3.6.1.2.1.31.1.1.1.18", "type": "text"},  # ifAlias
    "port_admin": {
        "oid": "1.3.6.1.2.1.2.2.1.7",
        "type": "switch",
        "vmap": {"on": "1", "off": "2"},
    },  # ifAdminStatus
    "port_oper": {
        "oid": "1.3.6.1.2.1.2.2.1.8",
        "type": "binary_sensor",
        "vmap": {"on": "1", "off": "2"},
    },  # ifOperStatus
    "port_highspeed": {
        "oid": "1.3.6.1.2.1.31.1.1.1.15",
        "type": "sensor",
        "unit": "mbps",
        "calc": "direct",
    },
    "in_octets": {
        "oid": "1.3.6.1.2.1.31.1.1.1.6",
        "type": "sensor",
        "device_class": "data_rate",
        "calc": "diff",
    },
    "out_octets": {
        "oid": "1.3.6.1.2.1.31.1.1.1.10",
        "type": "sensor",
        "device_class": "data_rate",
        "calc": "diff",
    },
    "in_errors": {"oid": "1.3.6.1.2.1.2.2.1.14", "type": "sensor", "calc": "diff"},
    "out_errors": {"oid": "1.3.6.1.2.1.2.2.1.20", "type": "sensor", "calc": "diff"},
    # No PoE in generic profile
    "poe_status": {"oid": "na"},
    "poe_usage": {"oid": "na"},
    "poe_power": {"oid": "na"},
}
