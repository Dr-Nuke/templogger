import pandas as pd
from pathlib import Path
import datetime

from config import logger


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
