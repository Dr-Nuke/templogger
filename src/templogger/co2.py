
import argparse
import getpass
import sys
import time
from pathlib import Path

import pandas as pd
import serial

from templogger.config import SENSORS_CO2, DATA_DIR_CO2, ROOT, logger
from templogger.utils import (df_prep_for_redis, get_redis,
                              push_raw_co2_data_redis, sensor_data_logging,
                              timestamp_now)


class Co2_Sensor:
    # a class to handle the sensor handling etc
    def __init__(self, location, device):
        self.s = serial.Serial(device, baudrate=9600, timeout=.5)
        self.s.flushInput()
        self.data_dir = DATA_DIR_CO2
        self.now = None
        self.errors = []
        self.df = None
        self.location = location
        logger.info(f"initiated sensor '{self.s.name}' with location '{self.location}'")

    def read_sensor(self):
        self.s.flushInput()
        self.s.write(b"\xFE\x04\x00\x00\x00\x04\xE5\xC6")
        time.sleep(2.4)  # wait for measurement
        resp = self.s.read(13)
        self.co2 = resp[9] * 256 + resp[10]
        logger.info(
            f"obtained CO2 from sensor {self.s.name} at {self.co2} ppm")

    def read_sensor_safely(self):
        try:
            self.read_sensor()
        except Exception as E:
            logger.error(f'sensor {self.location} had {type(E)}')
            self.co2 = None
            self.errors.append(type(E))

    def read_sensor_repeadedly(self, NOW):
        self.now = NOW
        max_tries = 5
        self.co2 = None
        self.tries = 0
        while (self.co2 is None) and (self.tries < max_tries):
            self.tries += 1
            logger.info(f"reading sensor try {self.tries}")
            self.read_sensor_safely()

    def sensor_data_logging(self):
        fname = self.now.strftime("co2_data_%Y-%m-%d.csv")
        fpath = self.data_dir / fname
        df = pd.DataFrame({"time": [self.now],
                           "co2": [none_to_nan(self.co2)],
                           "location": [self.location],
                           "tries": [self.tries],
                           "errors": [self.errors],
                           })
        self.df = df
        sensor_data_logging(df, "co2", DATA_DIR_CO2, self.now)


def none_to_nan(v):
    if v is None:
        return pd.NA
    return v


def main(NOW):
    r = get_redis()
    for sensor_props in SENSORS_CO2:
        s = Co2_Sensor(sensor_props["location"], sensor_props["device"])
        s.read_sensor_repeadedly(NOW)
        s.sensor_data_logging()

        df = df_prep_for_redis(s.df)

        push_raw_co2_data_redis(r, df)


if __name__ == '__main__':
    # get args
    parser = argparse.ArgumentParser()
    parser.add_argument("-s",
                        "--source",
                        help="adds information on who runs the script. appears in the logging",
                        type=str,
                        default=None)
    args = parser.parse_args()
    source = args.source
    if source is not None:
        logger.info(
            f"running CO2 sensors by '{getpass.getuser()}' from '{source}' from '{ROOT}' with '{sys.executable}'")

    NOW = timestamp_now()
    main(NOW)
