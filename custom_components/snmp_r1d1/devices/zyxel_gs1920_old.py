# custom_components/snmp_r1d1/devices/zyxel.py

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
#   "port_enabled": {"oid": "...", "type": "switch", "vmap": {"on": "1", "off": "2"}}                                     # binary switch
#
# INTEGER {disabled(1), searching(2), deliveringPower(3), fault(4), test(5), otherFault(6)}

# Zyxel GS1920 series device definition

config = {
    "access_test_oid": "1.3.6.1.2.1.1.4.0",  # sysContact (readable, writable if controls enabled) used for SNMP access test
    #"port_exclude": [1],       # uplink/SFP ports to skip
}

attributes = {
    "manufacturer": {"oid": "na"},                                   # vendor name not exposed
    "model": {"oid": ".1.3.6.1.4.1.890.1.15.3.1.11.0"},              # device model string
    "serial": {"oid": ".1.3.6.1.4.1.890.1.15.3.1.12.0"},             # serial number
    "port_count": {"oid": "1.3.6.1.2.1.2.1.0"},                      # number of interfaces
    "poe_budget": {"oid": ".1.3.6.1.2.1.105.1.3.1.1.2.1"},           # total PoE power budget
    "poe_port_list": {"oid": ".1.3.6.1.2.1.105.1.1.1.3.1", "mode": "WalkforList"},  # list of PoE ports
}

device = {
    "firmware": {"oid": ".1.3.6.1.4.1.890.1.15.3.1.6.0"},            # firmware version
    "uptime": {"oid": "1.3.6.1.2.1.1.3.0", "type": "sensor", "device_class": "duration", "math": "x/100", "unit": "s"},  # system uptime
    "cpu_usage": {"oid": ".1.3.6.1.4.1.890.1.15.3.2.4.0", "type": "sensor", "unit": "%"},  # CPU usage
    "memory_usage": {"oid": ".1.3.6.1.4.1.890.1.15.3.2.7.0", "type": "sensor", "unit": "%"},  # memory usage
    "poe_usage": {"oid": ".1.3.6.1.2.1.105.1.3.1.1.4.1", "type": "sensor", "unit": "W"},      # PoE usage in watts
    "temperature": {"oid": ".1.3.6.1.4.1.890.1.15.3.26.1.2.1.3.1", "type": "sensor", "device_class": "temperature", "unit": "°C"},  # system temp

    "igmp_snoop": {"oid": ".1.3.6.1.4.1.890.1.15.3.110.1.1.0", "type": "switch", "vmap": {"on": "1", "off": "2"}},  # IGMP snooping
    "mac_table": {"oid": ".1.3.6.1.2.1.17.4.3.1.1", "type": "mac_table"},
    "mac_port": {"oid": ".1.3.6.1.2.1.17.4.3.1.2", "type": "mac_port"},
}

ports = {
    "port_name": {"oid": ".1.3.6.1.2.1.31.1.1.1.18", "type": "text"},                        # interface alias
    "port_status": {"oid": ".1.3.6.1.2.1.2.2.1.8", "type": "binary_sensor"},                 # link up/down
    "port_admin": {"oid": ".1.3.6.1.2.1.2.2.1.7", "type": "switch", "vmap": {"on": "1", "off": "2"}},  # admin status
    "port_errors_in": {"oid": "1.3.6.1.2.1.2.2.1.14", "type": "sensor", "calc": "diff"},     # input errors (delta)
    "port_errors_out": {"oid": "1.3.6.1.2.1.2.2.1.20", "type": "sensor", "calc": "diff"},    # output errors (delta)
    "port_speed": {"oid": ".1.3.6.1.2.1.2.2.1.5", "type": "sensor", "device_class": "data_rate", "math": "x/1000000", "unit": "Mbit/s"},  # speed bps→Mbps
    "port_traffic_in": {"oid": "1.3.6.1.2.1.2.2.1.10", "type": "sensor", "device_class": "data_rate", "calc": "diff", "math": "(x*8)/1000000", "unit": "Mbit/s"},  # ingress traffic
    "port_traffic_out": {"oid": "1.3.6.1.2.1.2.2.1.16", "type": "sensor", "device_class": "data_rate", "calc": "diff", "math": "-(x*8)/1000000","unit": "Mbit/s"},  # egress traffic
    "poe_enabled": {"oid": ".1.3.6.1.2.1.105.1.1.1.3.1", "type": "switch", "vmap": {"on": "1", "off": "2"}},  # PoE enable/disable
    "poe_power": {"oid": ".1.3.6.1.4.1.890.1.15.3.59.2.1.1.1", "type": "sensor", "device_class": "power", "unit": "mW"},  # PoE power per port
    "poe_status": {"oid": ".1.3.6.1.2.1.105.1.1.1.6.1", "type": "sensor", "vmap": {"0": "off", "1": "disabled", "2": "searching", "3": "delivering", ">4": "fault"}},  # PoE status
}
