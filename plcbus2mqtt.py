#!/usr/bin/env python
'''
PLCBus to MQTT Gateway
(Inspired from WyzeSense to MQTT Gateway)
'''
import json
import logging
import logging.config
import logging.handlers
import os
import shutil
import signal
import subprocess
import time
import yaml

# Used for alternate MQTT connection method
# import signal
# import time

import paho.mqtt.client as mqtt

from retrying import retry
from lib.PLCBusManager import PlcBusManager

# Configuration File Locations
CONFIG_PATH = "config/"
MAIN_CONFIG_FILE = "config.yaml"
LOGGING_CONFIG_FILE = "logging.yaml"
DEVICES_CONFIG_FILE = "devices.yaml"


# Read data from YAML file
def read_yaml_file(filename):
    try:
        with open(filename) as yaml_file:
            data = yaml.safe_load(yaml_file)
            return data
    except IOError as error:
        if (LOGGER is None):
            print(f"File error: {str(error)}")
        else:
            LOGGER.error(f"File error: {str(error)}")


# Initializa logger
def init_logging():
    global LOGGER
    logging_config = read_yaml_file(CONFIG_PATH + LOGGING_CONFIG_FILE)

    log_path = os.path.dirname(logging_config['handlers']['file']['filename'])
    try:
        if (not os.path.exists(log_path)):
            os.makedirs(log_path)
    except IOError:
        print("Unable to create log folder")
    logging.config.dictConfig(logging_config)
    LOGGER = logging.getLogger(__name__)
    LOGGER.debug("Logging initialized...")


# Initialize configuration
def init_config():
    global CONFIG
    LOGGER.debug("Initializing configuration...")

    # load user config
    if (os.path.isfile(CONFIG_PATH + MAIN_CONFIG_FILE)):
        user_config = read_yaml_file(CONFIG_PATH + MAIN_CONFIG_FILE)
        CONFIG = read_yaml_file(CONFIG_PATH + MAIN_CONFIG_FILE)

    # fail on no config
    if (not 'CONFIG' in globals()):
        LOGGER.error(f"Failed to load configuration, please configure.")
        exit(1)


# Initialize MQTT client connection
def init_mqtt_client():
    global MQTT_CLIENT, CONFIG, LOGGER
    # Used for alternate MQTT connection method
    mqtt.Client.connected_flag = False

    # Configure MQTT Client
    MQTT_CLIENT = mqtt.Client(client_id=CONFIG['mqtt_client_id'], clean_session=CONFIG['mqtt_clean_session'])
    MQTT_CLIENT.username_pw_set(username=CONFIG['mqtt_username'], password=CONFIG['mqtt_password'])
    MQTT_CLIENT.reconnect_delay_set(min_delay=1, max_delay=120)
    MQTT_CLIENT.on_connect = on_connect
    MQTT_CLIENT.on_disconnect = on_disconnect
    MQTT_CLIENT.on_message = on_message
    MQTT_CLIENT.enable_logger(LOGGER)

    # Connect to MQTT
    LOGGER.info(f"Connecting to MQTT host {CONFIG['mqtt_host']}")
    MQTT_CLIENT.connect_async(CONFIG['mqtt_host'], port=CONFIG['mqtt_port'], keepalive=CONFIG['mqtt_keepalive'])

    # Used for alternate MQTT connection method
    MQTT_CLIENT.loop_start()
    while (not MQTT_CLIENT.connected_flag):
        time.sleep(1)


# Retry forever on IO Error
def retry_if_io_error(exception):
    return isinstance(exception, IOError)

# Initialize PLCBusManager
#@retry(wait_exponential_multiplier=1000, wait_exponential_max=30000, retry_on_exception=retry_if_io_error)
def init_plcbus_manager():
    global PLCBUS_MANAGER, CONFIG
    
    PLCBUS_MANAGER = PlcBusManager(LOGGER, CONFIG, state_change)
    LOGGER.debug(f"PLBBusManager initialized")


# Initialize sensor configuration
def init_devices():
    # Initialize sensor dictionary
    global DEVICES
    DEVICES = dict()

    # Load config file
    LOGGER.debug("Reading devices configuration...")
    if (os.path.isfile(CONFIG_PATH + DEVICES_CONFIG_FILE)):
        DEVICES = read_yaml_file(CONFIG_PATH + DEVICES_CONFIG_FILE)
        devices_config_file_found = True
    else:
        LOGGER.info("No sensors config file found.")
        devices_config_file_found = False

    # Send discovery topics
    if(CONFIG['hass_discovery']):
        for device in DEVICES:
            send_discovery_topics(device)


# Publish MQTT topic
def mqtt_publish(mqtt_topic, mqtt_payload):
    global MQTT_CLIENT, CONFIG
    mqtt_message_info = MQTT_CLIENT.publish(
        mqtt_topic,
        payload=json.dumps(mqtt_payload),
        qos=CONFIG['mqtt_qos'],
        retain=CONFIG['mqtt_retain']
    )
    if (mqtt_message_info.rc != mqtt.MQTT_ERR_SUCCESS):
        LOGGER.warning(f"MQTT publish error: {mqtt.error_string(mqtt_message_info.rc)}")


# Send discovery topics
def send_discovery_topics(device):
    global DEVICES, CONFIG

    LOGGER.info(f"Publishing discovery topics for {device}")

    sensor_name = DEVICES[device]['name']
    sensor_type = DEVICES[device]['type']

    config_payload = {
        "~": f"{CONFIG['hass_topic_root']}/{sensor_type}/plc_{device}",
        "name": sensor_name,
        "unique_id": f"plc_{device}",
        "cmd_t": "~/set",
        "stat_t": "~/state",
        "schema": "json"
    }
    
    if "brightness" in DEVICES[device] and DEVICES[device]['brightness'] == True:
        config_payload["brightness"] = "true"
        config_payload["brightness_scale"] = 100
    
    config_topic = f"{CONFIG['hass_topic_root']}/{sensor_type}/plc_{device}/config"
    mqtt_publish(config_topic, config_payload)
    LOGGER.debug(f"  {config_topic}")
    LOGGER.debug(f"  {json.dumps(config_payload)}")


# Clear any retained topics in MQTT
def clear_topics(device):
    global CONFIG
    LOGGER.info("Clearing sensor topics")
    state_topic = f"{CONFIG['self_topic_root']}/{device}"
    mqtt_publish(state_topic, None)

    # clear discovery topics if configured
    if(CONFIG['hass_discovery']):
        entity_types = ['state', 'signal_strength', 'battery']
        for entity_type in entity_types:
            sensor_type = (
                "binary_sensor" if (entity_type == "state")
                else "sensor"
            )
            entity_topic = f"{CONFIG['hass_topic_root']}/{sensor_type}/wyzesense_{device}/{entity_type}/config"
            mqtt_publish(entity_topic, None)


def on_connect(MQTT_CLIENT, userdata, flags, rc):
    global CONFIG
    if rc == mqtt.MQTT_ERR_SUCCESS:
        # Used for alternate MQTT connection method
        MQTT_CLIENT.connected_flag = True
        LOGGER.info(f"Connected to MQTT: {mqtt.error_string(rc)}")
    
        MQTT_CLIENT.subscribe(f"{CONFIG['hass_topic_root']}/+/+/set", CONFIG['mqtt_qos'])
        
        MQTT_CLIENT.subscribe(HA_STATUS_TOPIC)
        MQTT_CLIENT.message_callback_add(HA_STATUS_TOPIC, on_ha_status_message)
    else:
        LOGGER.warning(f"Connection to MQTT failed: {mqtt.error_string(rc)}")


def on_disconnect(MQTT_CLIENT, userdata, rc):
    MQTT_CLIENT.message_callback_remove(HA_STATUS_TOPIC)

    # Used for alternate MQTT connection method
    MQTT_CLIENT.connected_flag = False
    LOGGER.info(f"Disconnected from MQTT: {mqtt.error_string(rc)}")


# Process messages
def on_message(MQTT_CLIENT, userdata, msg):
    LOGGER.debug(f"MQTT message: {msg.topic}: {msg.payload.decode('utf-8')}")
    topic = msg.topic.split('/')
    device = topic[2][4:]
    if device in DEVICES:
        LOGGER.debug(f"Found device {DEVICES[device]}")
        state = json.loads(msg.payload)
        LOGGER.debug(state)
        
        # Below command to provide immediate feedback (ie without waiting for PLC bus answer - Debug only
        state_topic = f"{CONFIG['hass_topic_root']}/{DEVICES[device]['type']}/plc_{device}/state"
        mqtt_publish(state_topic, state)
        
        if "brightness" in state and "brightness" in DEVICES[device] and DEVICES[device]['brightness'] == True:
            LOGGER.debug(f"Received brightness {state['brightness']}")
        
        # Triggering PLC command
        PLCBUS_MANAGER.plcbus_cmnd(device, state['state'], CONFIG['usercode'], state['brightness']
            if "brightness" in state and "brightness" in DEVICES[device] and DEVICES[device]['brightness'] == True else None)
        
    else:
        LOGGER.error("Invalid message received, discarding")

def on_ha_status_message(MQTT_CLIENT, userdata, msg):
    LOGGER.info(f"MQTT HA status message: {msg.topic}: {msg.payload.decode('utf-8')}")
    if msg.payload.decode('utf-8') == "online":
        init_devices()


# Process changes from PLC manager
def state_change(device, status):
    LOGGER.info(f"Updating {device}: {status}")
    state_topic = f"{CONFIG['hass_topic_root']}/{DEVICES[device]['type']}/plc_{device}/state"
    state = { "state": f"{status}" }
    mqtt_publish(state_topic, state)


if __name__ == "__main__":
    # Initialize logging
    init_logging()

    # Initialize configuration
    init_config()

    # Set MQTT Topics
    HA_STATUS_TOPIC = f"{CONFIG['hass_topic_root']}/status"

    # Initialize MQTT client connection
    init_mqtt_client()

    # Initialize PLCBus
    init_plcbus_manager()

    # Initialize sensor configuration
    init_devices()

    # Loop forever until keyboard interrupt or SIGINT
    try:
        while True:
            #MQTT_CLIENT.loop_forever(retry_first_connection=False)

            # Used for alternate MQTT connection method
            signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        # Used with alternate MQTT connection method
        MQTT_CLIENT.loop_stop()
        MQTT_CLIENT.disconnect()
        PLCBUS_MANAGER.api.stop()
        PLCBUS_MANAGER._probe_thr.stop()
