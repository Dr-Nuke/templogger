import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import redis

import templogger.config as config


def timestamp_now():
    return datetime.datetime.now().replace(microsecond=0)


def datetime_to_ms(t):
    return int(t.timestamp()*1000)


def ms_to_pandas_dt(series):
    return pd.to_datetime(series, unit="ms")


def safe_append_csv(df, fpath):
    if not fpath.is_file():
        fpath.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(fpath, index=False, header='column_names')
    else:  # else it exists so append without writing the header
        df.to_csv(fpath, index=False, mode='a', header=False)


def sensor_data_logging(df: pd.DataFrame, prefix: str, fdir: Path, time: datetime.datetime):
    fname = "_".join([prefix, time.strftime("data_%Y-%m-%d.csv")])
    fpath = fdir / fname
    config.logger.info(f"logging {prefix} sensor data to {fpath}")
    safe_append_csv(df, fpath)


def make_key(loc: str, metric: str, agg: str) -> str:
    """Creates a Redis key for a given location, metric, and aggregation."""
    return f"sensor:{loc}:{metric}:{agg}"


def df_prep_for_redis(df):
    df["time"] = pd.to_datetime(df.time)
    epoch = pd.to_datetime(np.datetime64('1970-01-01T00:00:00', 'ms'))
    df["time_ms"] = ((df.time - epoch) / pd.Timedelta("1ms")).astype(int)
    df["nan"] = df.isna().any(axis=1)
    df = df.sort_values("time", ascending=False)  # latest records come first
    return df


def get_redis():
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    return r


def push_raw_sht_data_redis(r, df: pd.DataFrame):
    success = 0
    ignores = 0
    ts = r.ts()
    for _, row in df.iterrows():
        if row["nan"]:
            continue
        location = row['location']
        timestamp = row["time_ms"]
        for metric in config.METRICS_SHT:
            val = row[metric]
            key = make_key(location, metric, "raw")
            try:
                ts.add(key, timestamp, float(val))
                success += 1
            except redis.ResponseError as e:  # ignore the pre-retention-error
                if "TSDB: Timestamp is older than retention" not in str(e):
                    raise e
                ignores += 1
    config.logger.info(
        f"ingested {success} records and ignored {ignores} due to retention")


def push_raw_co2_data_redis(r, df: pd.DataFrame):
    success = 0
    ignores = 0
    ts = r.ts()
    for _, row in df.iterrows():
        if row["nan"]:
            continue
        location = row['location']
        timestamp = row["time_ms"]

        val = row["co2"]
        key = make_key(location, config.METRICS_CO2[0], "raw")
        try:
            ts.add(key, timestamp, float(val))
            success += 1
        except redis.ResponseError as e:  # ignore the pre-retention-error
            if "TSDB: Timestamp is older than retention" not in str(e):
                raise e
            ignores += 1
    config.logger.info(
        f"ingested {success} records and ignored {ignores} due to retention")
