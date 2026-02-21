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


# === Aggregation utilities ===

def compute_aggregates(df: pd.DataFrame, metrics: list, location_col: str, freq: str) -> pd.DataFrame:
    """Group raw data by location and time period, compute mean.

    Args:
        df: DataFrame with 'time' (datetime), location column, and metric columns.
        metrics: Metric column names to aggregate.
        location_col: Column containing the sensor location.
        freq: Pandas frequency string: 'h' for hourly, 'D' for daily.

    Returns:
        DataFrame with columns [time, location_col, *metrics], sorted by time+location.
        Rows where all metrics are NaN are dropped.
    """
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    # only keep columns we need
    cols = [location_col, "time"] + [m for m in metrics if m in df.columns]
    df = df[cols]
    df["period"] = df["time"].dt.floor(freq)
    result = df.groupby([location_col, "period"])[metrics].mean().reset_index()
    result = result.rename(columns={"period": "time"})
    # drop rows where all metric columns are NaN
    result = result.dropna(subset=metrics, how="all")
    # round to 2 decimal places
    result[metrics] = result[metrics].round(2)
    result = result.sort_values(["time", location_col]).reset_index(drop=True)
    return result


def write_aggregate_csv(df: pd.DataFrame, agg_dir: Path, prefix: str, agg_level: str) -> None:
    """Write aggregate data to CSV files, idempotently (no duplicate rows).

    File naming:
        hourly: {prefix}_hourly_{YYYY-MM}.csv (monthly files)
        daily:  {prefix}_daily_{YYYY}.csv     (yearly files)
    """
    if df.empty:
        return

    agg_dir.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])

    # determine file grouping key
    if agg_level == "hourly":
        df["_file_key"] = df["time"].dt.strftime("%Y-%m")
    else:  # daily
        df["_file_key"] = df["time"].dt.strftime("%Y")

    for file_key, group in df.groupby("_file_key"):
        fname = f"{prefix}_{agg_level}_{file_key}.csv"
        fpath = agg_dir / fname
        group = group.drop(columns=["_file_key"])

        # format time column
        if agg_level == "daily":
            group["time"] = group["time"].dt.strftime("%Y-%m-%d")
        else:
            group["time"] = group["time"].dt.strftime("%Y-%m-%d %H:%M:%S")

        if fpath.is_file():
            existing = pd.read_csv(fpath)
            combined = pd.concat([existing, group], ignore_index=True)
            combined = combined.drop_duplicates(subset=["time", "location"], keep="last")
            combined = combined.sort_values(["time", "location"]).reset_index(drop=True)
            combined.to_csv(fpath, index=False)
        else:
            group = group.sort_values(["time", "location"]).reset_index(drop=True)
            group.to_csv(fpath, index=False)

    config.logger.info(f"wrote {agg_level} aggregate CSV to {agg_dir}")


def push_aggregate_redis(r, df: pd.DataFrame, metrics: list, location_col: str, agg_level: str) -> None:
    """Push aggregated data to Redis TimeSeries."""
    success = 0
    ignores = 0
    ts = r.ts()
    for _, row in df.iterrows():
        location = row[location_col]
        timestamp_ms = datetime_to_ms(pd.to_datetime(row["time"]))
        for metric in metrics:
            val = row.get(metric)
            if pd.isna(val):
                continue
            key = make_key(location, metric, agg_level)
            try:
                ts.add(key, timestamp_ms, float(val))
                success += 1
            except redis.ResponseError as e:
                if "TSDB: Timestamp is older than retention" not in str(e):
                    raise e
                ignores += 1
    config.logger.info(
        f"pushed {success} {agg_level} records to Redis, ignored {ignores} due to retention")


def load_raw_csvs(data_dir: Path, prefix: str, start_date, end_date,
                  value_col_map: dict = None) -> pd.DataFrame:
    """Load raw CSV files from a date range (inclusive).

    Args:
        data_dir: Directory containing raw CSVs.
        prefix: 'sht' or 'co2'.
        start_date: First date to include (datetime.date).
        end_date: Last date to include (datetime.date).
        value_col_map: Optional column rename dict (e.g. {'co2': 'CO2'}).

    Returns:
        DataFrame with 'time' (datetime), 'location', and metric columns.
        Empty DataFrame if no matching files found.
    """
    fpaths = sorted(data_dir.glob(f"{prefix}_data_*.csv"))
    read_files = []
    for f in fpaths:
        try:
            file_date = datetime.datetime.strptime(f.stem[-10:], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_date <= file_date <= end_date:
            read_files.append(f)

    if not read_files:
        return pd.DataFrame()

    dfs = []
    for f in read_files:
        try:
            df = pd.read_csv(f)
            dfs.append(df)
        except Exception as e:
            config.logger.warning(f"skipping {f.name}: {e}")

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)
    if value_col_map:
        df = df.rename(columns=value_col_map)
    df["time"] = pd.to_datetime(df["time"])
    return df


def get_last_aggregated_time(agg_dir: Path, prefix: str, agg_level: str):
    """Read aggregate CSV files and return the latest 'time' value (the marker).

    Returns None if no aggregate CSVs exist or all are empty.
    """
    pattern = f"{prefix}_{agg_level}_*.csv"
    fpaths = sorted(agg_dir.glob(pattern), reverse=True)  # latest period first

    for fpath in fpaths:
        try:
            df = pd.read_csv(fpath)
            if df.empty:
                continue
            df["time"] = pd.to_datetime(df["time"])
            return df["time"].max()
        except Exception:
            continue

    return None
