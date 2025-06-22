import datetime
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytz
import redis
import seaborn as sns

from templogger.config import (AGGREGATIONS, DATA_DIR_CO2, DATA_DIR_SHT,
                               METRICS_CO2, METRICS_SHT,
                               REDIS_LAST_TEIMESTAMP_KEY, SENSORS_CO2,
                               SENSORS_SHT, logger)
from templogger.utils import (df_prep_for_redis, get_redis, make_key,
                              push_raw_co2_data_redis, push_raw_sht_data_redis,
                              timestamp_now)


def create_schema(r):
    counter_success = 0
    counter_ignore = 0
    sensor_names = [s.name for s in SENSORS_SHT]
    for location in sensor_names:
        for metric in METRICS_SHT:
            for agg, props in AGGREGATIONS.items():
                key = make_key(location, metric, agg)
                try:
                    r.ts().create(
                        key,
                        retention_msecs=props["retention"],
                        duplicate_policy=props["duplicate_policy"],
                    )
                    logger.info(f"created key {key}")
                    counter_success += 1
                except redis.ResponseError as e:
                    if "TSDB: key already exists" not in str(e):
                        raise
                    logger.info(f"key {key} already exists - skipping")
                    counter_ignore += 1

    for co2_sensor in SENSORS_CO2:
        for metric in METRICS_CO2:
            for agg, props in AGGREGATIONS.items():
                key = make_key(co2_sensor["location"], metric, agg)
                try:
                    r.ts().create(
                        key,
                        retention_msecs=props["retention"],
                        duplicate_policy=props["duplicate_policy"],
                    )
                    logger.info(f"created key {key}")
                    counter_success += 1
                except redis.ResponseError as e:
                    if "TSDB: key already exists" not in str(e):
                        raise
                    logger.info(f"key {key} already exists - skipping")
                    counter_ignore += 1

    try:
        r.set(REDIS_LAST_TEIMESTAMP_KEY, 0)
    except Exception as e:
        logger.info(f"could not set last imestamp value: {e}")

    logger.info(
        f"created {counter_success} key and ignored {counter_ignore} due to already exising")


def ingest_raw(r, data_dir, prefix):
    # get required date ranges or first day to load
    now = timestamp_now()
    timespan = AGGREGATIONS["raw"]["retention"]
    timespan_td = datetime.timedelta(milliseconds=timespan)
    then = (timestamp_now() - timespan_td).date()

    # find all files involved
    fpaths = data_dir.glob(f"{prefix}_data_*.csv")
    fpath_list = list(fpaths)
    read_files = [f for f in fpath_list if datetime.datetime.strptime(
        f.stem[-10:], '%Y-%m-%d').date() >= then]
    logger.info(
        f"reading in files \n{"\n".join([f.name for f in read_files])}")

    # load & adjust schema
    dfs = [pd.read_csv(f) for f in read_files]
    df = pd.concat(dfs).reset_index(drop=True)
    df = df_prep_for_redis(df)

    return df


if __name__ == "__main__":
    logger.info(f"starting redis rehydration")
    r = get_redis()
    ts = r.ts()
    assert r.ping()

    r.flushdb()
    create_schema(r)
    df = ingest_raw(r, DATA_DIR_SHT,"sht")
    push_raw_sht_data_redis(r, df)

    df_co2 = ingest_raw(r, DATA_DIR_CO2, "co2")
    push_raw_co2_data_redis(r, df_co2)