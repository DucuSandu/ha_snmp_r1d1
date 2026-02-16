# custom_components/snmp_r1d1/devices/generic.py

config = {
   "access_test_oid": "1.3.6.1.2.1.1.4.0",  # sysContact (readable, writable if controls enabled) used for SNMP access test
}

attributes = {
        "manufacturer": {"oid": "na"},
        "model": {"oid": "na"},
        "serial": {"oid": "na"},
        "firmware": {"oid": "1.3.6.1.2.1.1.1.0"},     # sysDescr
        "sys_object_id": {"oid": "1.3.6.1.2.1.1.2.0"}, # sysObjectID
        "port_count": {"oid": "1.3.6.1.2.1.2.1.0"},    # ifNumber
        "poe_budget": {"oid": "na"},
        "poe_port_list": {"oid": "na"},
    }

device = {
        "sys_name":   {"oid": "1.3.6.1.2.1.1.5.0", "type": "text"},
        "sys_uptime": {"oid": "1.3.6.1.2.1.1.3.0", "type": "sensor", "calc": "direct"},

        # CPU (UCD-SNMP)
        "cpu_user":   {"oid": "1.3.6.1.4.1.2021.11.9.0", "type": "sensor", "unit": "%"},
        "cpu_system": {"oid": "1.3.6.1.4.1.2021.11.10.0", "type": "sensor", "unit": "%"},
        "cpu_idle":   {"oid": "1.3.6.1.4.1.2021.11.11.0", "type": "sensor", "unit": "%"},

        # Load averages
        "load_1":     {"oid": "1.3.6.1.4.1.2021.10.1.3.1", "type": "sensor"},
        "load_5":     {"oid": "1.3.6.1.4.1.2021.10.1.3.2", "type": "sensor"},
        "load_15":    {"oid": "1.3.6.1.4.1.2021.10.1.3.3", "type": "sensor"},

        # Memory
        "mem_total":  {"oid": "1.3.6.1.4.1.2021.4.5.0",  "type": "sensor", "unit": "kB"},
        "mem_free":   {"oid": "1.3.6.1.4.1.2021.4.6.0",  "type": "sensor", "unit": "kB"},
        "mem_cached": {"oid": "1.3.6.1.4.1.2021.4.15.0", "type": "sensor", "unit": "kB"},
    }

ports = {
        "port_name":     {"oid": "1.3.6.1.2.1.31.1.1.1.1",  "type": "text"},   # ifName
        "port_alias":    {"oid": "1.3.6.1.2.1.31.1.1.1.18", "type": "text"},   # ifAlias
        "port_admin":    {"oid": "1.3.6.1.2.1.2.2.1.7", "type": "switch", 
                          "vmap": {"on": "1", "off": "2"}},                   # ifAdminStatus
        "port_oper":     {"oid": "1.3.6.1.2.1.2.2.1.8", "type": "binary_sensor", 
                          "vmap": {"on": "1", "off": "2"}},                   # ifOperStatus
        "port_highspeed":{"oid": "1.3.6.1.2.1.31.1.1.1.15", "type": "sensor", "unit": "mbps", "calc": "direct"},
        "in_octets":     {"oid": "1.3.6.1.2.1.31.1.1.1.6",  "type": "sensor", "device_class": "data_rate", "calc": "diff"},
        "out_octets":    {"oid": "1.3.6.1.2.1.31.1.1.1.10", "type": "sensor", "device_class": "data_rate", "calc": "diff"},
        "in_errors":     {"oid": "1.3.6.1.2.1.2.2.1.14", "type": "sensor", "calc": "diff"},
        "out_errors":    {"oid": "1.3.6.1.2.1.2.2.1.20", "type": "sensor", "calc": "diff"},

        # No PoE in generic profile
        "poe_status": {"oid": "na"},
        "poe_usage":  {"oid": "na"},
        "poe_power":  {"oid": "na"},
    }

