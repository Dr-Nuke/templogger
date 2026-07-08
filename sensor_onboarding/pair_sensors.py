#!/usr/bin/env python3
"""
Automates BlueZ pairing of the SHT43 sensors across all production adapters.

Drives `bluetoothctl` via pexpect: selects each adapter, discovers each
sensor, initiates pairing, accepts the numeric-comparison confirmation on the
host side, and trusts the device. The one thing this script cannot do is
press the physical confirm button on the sensor's e-ink screen — it prints
the passkey and pauses there, waiting for you to do that.

Already-paired (adapter, sensor) combinations are detected via `bluetoothctl
info` and skipped, so it's safe to re-run after a partial run.

Usage:
    python sensor_onboarding/pair_sensors.py --adapters hci0 hci1 hci2 hci3 --sensors C6:D5:00:27:D9:5F C6:D5:00:27:D5:65
    python sensor_onboarding/pair_sensors.py --adapters hci0 --sensors C6:D5:00:27:D9:5F --verify

Both --adapters and --sensors are required — there are no built-in defaults,
since both are specific to your hardware. To find your adapter names, run
`hciconfig -a` and check the "Bus:" field: USB dongles show `Bus: USB`, the
Raspberry Pi's onboard chip shows `Bus: UART`. Don't assume the onboard chip
is hci0 — its index isn't guaranteed; verify the Bus field for each one.

Run from the repo root (templogger/). See sensor_onboarding/pairing_guide.md for the
manual walkthrough this automates.
"""

import argparse
import re
import subprocess
import sys
import time

import pexpect

# bluetoothctl colorizes its prompt/output under a pty (ANSI escape codes,
# plus \x01/\x02 readline start/end-of-non-printing markers). The escape
# codes themselves contain literal "[" characters (e.g. "\x1b[0;94m"), which
# breaks naive "[...]#" pattern matching. Strip these from the raw stream
# before anything gets matched, rather than trying to make every pattern
# tolerate embedded escape codes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|[\x01\x02]")


class CleanSpawn(pexpect.spawn):
    def read_nonblocking(self, size=1, timeout=None):
        data = super().read_nonblocking(size=size, timeout=timeout)
        return _ANSI_RE.sub("", data)


PROMPT = r"\[.*?\]#\s*"
PAIR_TIMEOUT = 60
SCAN_TIMEOUT = 30


def adapter_bd_addresses() -> dict[str, str]:
    out = subprocess.run(["hciconfig", "-a"], capture_output=True, text=True, check=True).stdout
    mapping: dict[str, str] = {}
    current = None
    for line in out.splitlines():
        m = re.match(r"^(hci\d+):", line)
        if m:
            current = m.group(1)
        m2 = re.search(r"BD Address: ([0-9A-Fa-f:]{17})", line)
        if m2 and current:
            mapping[current] = m2.group(1)
    return mapping


def already_paired(child: pexpect.spawn, mac: str) -> bool:
    child.sendline(f"info {mac}")
    idx = child.expect([r"Paired: yes", r"Paired: no", r"not available", pexpect.TIMEOUT], timeout=10)
    return idx == 0


MAX_ATTEMPTS = 3

# Reasons in expect() index order for the `pair` outcome, used for readable
# failure messages instead of dumping raw (possibly empty/misleading) buffer text.
_PAIR_FAIL_REASONS = ["not-available", "bluez-error"]


def _disconnect(child: pexpect.spawn, mac: str) -> None:
    # Devices support a single BLE connection; leaving one connected after
    # pairing makes it invisible (non-advertising) to every other adapter's
    # scan. Always release it, best-effort, whether pairing succeeded or not.
    child.sendline(f"disconnect {mac}")
    child.expect([r"Successful disconnected", r"Not connected", pexpect.TIMEOUT], timeout=10)


def _try_pair(child: pexpect.spawn, mac: str, label: str) -> tuple[bool, str]:
    child.sendline("scan on")
    found = False
    deadline = time.time() + SCAN_TIMEOUT
    while time.time() < deadline:
        idx = child.expect([re.escape(mac), pexpect.TIMEOUT], timeout=5)
        if idx == 0:
            found = True
            break
    child.sendline("scan off")
    if not found:
        return False, f"not discovered within {SCAN_TIMEOUT}s"

    child.sendline(f"pair {mac}")
    idx = child.expect([
        r"Confirm passkey (\d+) \(yes/no\):",
        r"Pairing successful",
        r"Failed to pair",
        r"not available",
        pexpect.TIMEOUT,
    ], timeout=PAIR_TIMEOUT)

    if idx == 0:
        code = child.match.group(1)
        print(f"  >>> sensor should show passkey {code} — press its confirm button NOW <<<")
        child.sendline("yes")
        idx2 = child.expect([r"Pairing successful", r"Failed to pair", pexpect.TIMEOUT], timeout=PAIR_TIMEOUT)
        if idx2 != 0:
            return False, "pairing did not complete after confirmation" if idx2 == 1 else "confirmation timed out"
    elif idx == 1:
        pass  # paired without a confirmation step
    elif idx in (2, 3):
        return False, _PAIR_FAIL_REASONS[idx - 2]
    else:
        return False, "pair command timed out"

    child.sendline(f"trust {mac}")
    child.expect([r"trust succeeded", pexpect.TIMEOUT], timeout=10)
    return True, "ok"


def pair_one(child: pexpect.spawn, mac: str, label: str) -> bool:
    print(f"\n--- {label} ({mac}) ---")

    if already_paired(child, mac):
        print("  already paired, skipping")
        _disconnect(child, mac)
        return True

    for attempt in range(1, MAX_ATTEMPTS + 1):
        ok, reason = _try_pair(child, mac, label)
        _disconnect(child, mac)  # release the link either way before the next attempt/adapter
        if ok:
            print(f"  paired + trusted (attempt {attempt}/{MAX_ATTEMPTS})")
            return True
        print(f"  ! attempt {attempt}/{MAX_ATTEMPTS} failed: {reason}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(2)

    print(f"  ! giving up after {MAX_ATTEMPTS} attempts")
    return False


def pair_adapter(adapter: str, bd_addr: str, sensors: dict[str, str]) -> dict[str, bool]:
    print(f"\n=== {adapter} ({bd_addr}) ===")
    child = CleanSpawn("bluetoothctl", encoding="utf-8", timeout=15)
    results: dict[str, bool] = {}
    try:
        child.expect(PROMPT)
        child.sendline(f"select {bd_addr}")
        child.expect(PROMPT)
        child.sendline("agent on")
        child.expect(PROMPT)
        child.sendline("default-agent")
        child.expect(PROMPT)

        for mac, label in sensors.items():
            results[mac] = pair_one(child, mac, label)

        child.sendline("quit")
    finally:
        child.close(force=True)
    return results


def verify_adapter(adapter: str, macs: list[str]) -> None:
    print(f"\n=== verifying {adapter} via test_sensor.py ===")
    proc = subprocess.run(
        [sys.executable, "sensor_onboarding/test_sensor.py", "--adapter", adapter, *macs],
        capture_output=True, text=True,
    )
    print(proc.stdout[-3000:])
    if "FAILED" in proc.stdout:
        print(f"  ! {adapter}: some reads still failing, see full log under sensor_onboarding/test_logs/")
    else:
        print(f"  {adapter}: all reads OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapters", nargs="+", required=True, metavar="HCI",
                        help="e.g. hci0 hci1 — find yours with `hciconfig -a`")
    parser.add_argument("--sensors", nargs="+", required=True, metavar="MAC",
                        help="e.g. C6:D5:00:27:D9:5F")
    parser.add_argument("--verify", action="store_true", help="run test_sensor.py per adapter after pairing")
    args = parser.parse_args()

    bd_map = adapter_bd_addresses()
    sensors = {mac: mac for mac in args.sensors}

    summary: dict[str, dict[str, bool]] = {}
    for adapter in args.adapters:
        bd_addr = bd_map.get(adapter)
        if not bd_addr:
            print(f"! unknown adapter {adapter}, skipping")
            continue
        summary[adapter] = pair_adapter(adapter, bd_addr, sensors)
        if args.verify:
            verify_adapter(adapter, list(sensors.keys()))

    print("\n=== Summary ===")
    for adapter, results in summary.items():
        for mac, ok in results.items():
            print(f"  {adapter:6s} {mac:18s} {'OK' if ok else 'FAILED/SKIPPED'}")


if __name__ == "__main__":
    main()
