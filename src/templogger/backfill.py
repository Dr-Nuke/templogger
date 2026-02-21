"""
One-time historical backfill script.
Processes ALL raw CSV files and writes hourly/daily aggregate CSVs.
Does not push to Redis (that is rehydrate_redis.py's job).

Usage: python src/templogger/backfill.py
"""

import pandas as pd

from templogger.config import SENSOR_TYPES, logger
from templogger.utils import compute_aggregates, write_aggregate_csv


def backfill_sensor_type(sensor_type: dict) -> None:
    """Process all raw CSVs for one sensor type, computing hourly+daily aggregates."""
    data_dir = sensor_type["data_dir"]
    prefix = sensor_type["prefix"]
    metrics = sensor_type["metrics"]
    value_col_map = sensor_type["value_col_map"]

    fpaths = sorted(data_dir.glob(f"{prefix}_data_*.csv"))
    logger.info(f"backfill {prefix}: found {len(fpaths)} raw CSV files")

    for i, fpath in enumerate(fpaths):
        try:
            df = pd.read_csv(fpath)
        except Exception as e:
            logger.warning(f"backfill: skipping {fpath.name}: {e}")
            continue

        if df.empty:
            continue

        if value_col_map:
            df = df.rename(columns=value_col_map)

        df["time"] = pd.to_datetime(df["time"])

        # hourly aggregates
        df_hourly = compute_aggregates(df, metrics, "location", freq="h")
        if not df_hourly.empty:
            write_aggregate_csv(df_hourly, sensor_type["hourly_dir"], prefix, "hourly")

        # daily aggregates
        df_daily = compute_aggregates(df, metrics, "location", freq="D")
        if not df_daily.empty:
            write_aggregate_csv(df_daily, sensor_type["daily_dir"], prefix, "daily")

        if (i + 1) % 100 == 0:
            logger.info(f"backfill {prefix}: processed {i + 1}/{len(fpaths)} files")

    logger.info(f"backfill {prefix}: complete ({len(fpaths)} files processed)")


if __name__ == "__main__":
    logger.info("starting historical backfill")
    for type_name, sensor_type in SENSOR_TYPES.items():
        backfill_sensor_type(sensor_type)
    logger.info("historical backfill complete")
