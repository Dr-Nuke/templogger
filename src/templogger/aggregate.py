"""
Periodic aggregation script.
Called from run_all_sensors.sh after sensor data collection (~10 min cycle).
Computes hourly and daily means from raw Redis data.
Writes results to Redis TimeSeries and aggregate CSV files.

Usage: python src/templogger/aggregate.py
"""

import datetime

import pandas as pd
import redis

from templogger.config import SENSOR_TYPES, logger
from templogger.utils import (
    compute_aggregates, datetime_to_ms, get_redis, make_key,
    push_aggregate_redis, timestamp_now, write_aggregate_csv,
)


def fetch_raw_from_redis(r, locations: list, metrics: list,
                         start_ms: int, end_ms: int) -> pd.DataFrame:
    """Query raw Redis TimeSeries for all locations/metrics in a time range.

    Returns a DataFrame with columns: [time, location, *metrics].
    """
    ts = r.ts()
    records = []
    for loc in locations:
        for metric in metrics:
            key = make_key(loc, metric, "raw")
            try:
                data = ts.range(key, start_ms, end_ms)
            except redis.ResponseError:
                continue
            for ts_val, val in data:
                records.append({
                    "time_ms": ts_val,
                    "location": loc,
                    "metric": metric,
                    "value": val,
                })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df_pivot = df.pivot_table(
        index=["time_ms", "location"],
        columns="metric",
        values="value",
        aggfunc="first"
    ).reset_index()
    df_pivot.columns.name = None
    df_pivot["time"] = pd.to_datetime(df_pivot["time_ms"], unit="ms")
    df_pivot = df_pivot.drop(columns=["time_ms"])
    return df_pivot


def aggregate_hourly(r, sensor_type: dict, now: datetime.datetime) -> None:
    """Compute hourly aggregates for the last 3 completed hours."""
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    start = current_hour - datetime.timedelta(hours=3)

    start_ms = datetime_to_ms(start)
    end_ms = datetime_to_ms(current_hour) - 1  # up to but not including current hour

    df_raw = fetch_raw_from_redis(
        r, sensor_type["locations"], sensor_type["metrics"], start_ms, end_ms
    )
    if df_raw.empty:
        return

    df_agg = compute_aggregates(df_raw, sensor_type["metrics"], "location", freq="h")
    if df_agg.empty:
        return

    push_aggregate_redis(r, df_agg, sensor_type["metrics"], "location", "hourly")
    write_aggregate_csv(df_agg, sensor_type["hourly_dir"],
                        sensor_type["prefix"], "hourly")
    logger.info(f"hourly aggregation: wrote {len(df_agg)} records for {sensor_type['prefix']}")


def aggregate_daily(r, sensor_type: dict, now: datetime.datetime) -> None:
    """Compute daily aggregate for yesterday."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - datetime.timedelta(days=1)

    start_ms = datetime_to_ms(yesterday)
    end_ms = datetime_to_ms(today) - 1

    df_raw = fetch_raw_from_redis(
        r, sensor_type["locations"], sensor_type["metrics"], start_ms, end_ms
    )
    if df_raw.empty:
        return

    df_agg = compute_aggregates(df_raw, sensor_type["metrics"], "location", freq="D")
    if df_agg.empty:
        return

    push_aggregate_redis(r, df_agg, sensor_type["metrics"], "location", "daily")
    write_aggregate_csv(df_agg, sensor_type["daily_dir"],
                        sensor_type["prefix"], "daily")
    logger.info(f"daily aggregation: wrote {len(df_agg)} records for {sensor_type['prefix']}")


if __name__ == "__main__":
    logger.info("starting periodic aggregation")
    r = get_redis()
    now = timestamp_now()

    for type_name, sensor_type in SENSOR_TYPES.items():
        logger.info(f"aggregating {type_name}")
        aggregate_hourly(r, sensor_type, now)
        aggregate_daily(r, sensor_type, now)

    logger.info("periodic aggregation complete")
