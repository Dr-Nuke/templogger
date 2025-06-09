import pandas as pd
from pathlib import Path
import datetime
import redis

from config import logger

def timestamp_now():
    return datetime.datetime.now().replace(microsecond=0)

def safe_append_csv(df, fpath):
    if not fpath.is_file():
        fpath.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(fpath, index=False, header='column_names')
    else:  # else it exists so append without writing the header
        df.to_csv(fpath, index=False, mode='a', header=False)


def sensor_data_logging(df: pd.DataFrame, prefix: str, fdir: Path, time: datetime.datetime):
    fname = "_".join([prefix, time.strftime("_data_%Y-%m-%d.csv")])
    fpath = fdir / fname
    logger.info(f"logging sensor data to {fpath}")
    safe_append_csv(df, fpath)

def ensure_timeseries_exists(location, metric, period):
    key = f"sensor:{location}:{metric}:{period}"
    try:
        r.ts().create(
            key,
            retention_msecs=RETENTION[period],
            labels={
                'location': location,
                'metric': metric,
                'period': period
            }
        )
    except redis.ResponseError as e:
        if "TSDB: key already exists" not in str(e):
            raise(e)
    return key


def push_raw_data(df: pd.DataFrame):
    df = df.where(pd.notnull(df), None)
    df['time'] = pd.to_datetime(df['time'])

    for _, row in df.iterrows():
        location = row['location']
        timestamp = int(row['time'].timestamp() * 1000)

        for metric in METRICS:
            val = row[metric]
            if val is None:
                continue

            key = ensure_timeseries_exists(location, metric, 'raw')
            r.ts().add(key, timestamp, float(val))