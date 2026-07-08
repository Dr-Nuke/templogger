# SHT43 Bonding Guide — bluetoothctl Pairing Walkthrough

Goal: bond each new SHT43 sensor with each of your production BLE adapters, so
BlueZ can negotiate encryption (required for GATT reads) regardless of which
adapter the collector's worker queue assigns a sensor to on a given run.

See the "SHT43 boards" section in the root `readme.md` for the short version
of why this is needed. Short version of the short version: SHT43 firmware
from a certain version onward requires `SECURE_ACCESS` (an encrypted link)
for Battery/Humidity/Temperature reads. Without a prior bond, the first read
fails with `ATT error: 0x0e` and the sensor drops the connection.

**Do not add unbonded SHT43 sensors to `SENSORS_SHT` in `config.py` until all
pairings in this guide are verified.** Running the production collector
against an unbonded SHT43 can cause a cascading BLE stack failure — repeated
failed connect/disconnect cycles across all adapters, overlapping cron runs,
and knock-on failures for unrelated sensors.

---

## Find your adapters first

```
hciconfig -a
```

For each `hciN`, note the `BD Address` and the `Bus:` field. **Don't assume
adapter identity by index** — e.g. it's tempting to assume `hci0` is always
the Raspberry Pi's onboard chip and higher indices are USB dongles, but the
enumeration order isn't guaranteed and can differ per device/boot. The
reliable signal is the `Bus:` field: USB dongles report `Bus: USB`, the
onboard chip reports `Bus: UART`. Cross-check against `ADAPTERS` in your
`config.py` to know which adapters are actually in production use.

---

## Worked example: one adapter, one sensor

Substitute your own adapter's BD address and your sensor's MAC throughout.

### Step 1 — Open bluetoothctl

```
bluetoothctl
```

### Step 2 — Select the adapter

```
select <ADAPTER_BD_ADDRESS>
```

Confirm the prompt now shows that controller as `[default]`.

### Step 3 — Enable the pairing agent (skip if already registered)

```
agent on
default-agent
```

### Step 4 — Discover the device

BlueZ needs to have seen the device on **this** adapter before `pair` will
work — a bare MAC address that hasn't been discovered yet returns `Device
... not available`, which is different from a connection failure.

```
scan on
```

Wait for a line like:

```
[NEW] Device <SENSOR_MAC> ...
```

Some sensors have shown intermittent/low-duty-cycle advertising — if it
doesn't show up within ~30 s, leave `scan on` running a bit longer, or retry.
Once seen:

```
scan off
```

### Step 5 — Pair

```
pair <SENSOR_MAC>
```

The sensor's e-ink screen will show a numeric code. When `bluetoothctl`
prompts `Confirm passkey ... (yes/no)`:
1. Check the code on the sensor screen matches.
2. Press the confirm button **on the sensor**.
3. Type `yes` at the `bluetoothctl` prompt.

A successful pair prints `Pairing successful`.

### Step 6 — Trust the device

Marks it so BlueZ auto-accepts reconnects without re-prompting:

```
trust <SENSOR_MAC>
```

### Step 7 — Disconnect

Each SHT43 sensor supports only **one active BLE connection at a time**.
Leaving it connected after pairing makes it invisible (non-advertising) to
every other adapter's scan — you'll need to bond the same sensor with your
other production adapters too, so release it now:

```
disconnect <SENSOR_MAC>
quit
```

### Step 8 — Verify the bond actually enables reads

```
python sensor_onboarding/test_sensor.py --adapter <hciN> <SENSOR_MAC>
```

Check Step 4 of the script's output: Battery/Humidity/Temperature should now
show decoded values instead of `Not connected` / `ATT error: 0x0e`.

---

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `Device <MAC> not available` on `pair` | Device object not yet known to BlueZ on this adapter | `scan on` until the device appears, `scan off`, retry `pair` |
| `pair` times out / device never appears in scan | Sensor not currently advertising (low duty cycle, asleep, or — if previously paired elsewhere — currently connected on another adapter and therefore not advertising at all) | Retry; check `hcitool -i <hciN> con` on every adapter to see if the sensor is already connected somewhere and disconnect it there first |
| Numeric code prompt never appears | Agent not registered, or device already bonded/cached from a previous partial attempt | Re-run `agent on` / `default-agent`; if previously partially paired, `remove <MAC>` first, then retry `pair` |
| `trust` succeeds but `test_sensor.py` still shows `ATT error: 0x0e` | `trust` and `pair` are independent — trusting an unpaired device still "succeeds" even though no bond/encryption keys exist | Run `bluetoothctl info <MAC>` and check for `Paired: yes`. If `no`, `remove <MAC>` and retry `pair`, watching for the explicit `Pairing successful` line (not just `trust succeeded`) |
| A sensor that paired fine on one adapter can't be discovered on the next adapter | The sensor is still connected to the previous adapter (see Step 7 — disconnect is easy to forget) | `hcitool -i <hciN> con` on each adapter to find which one holds it, then disconnect it from there |

---

## Scaling to many (adapter, sensor) combinations

**Scripted option:** `sensor_onboarding/pair_sensors.py` automates everything above except
the physical button press — it drives `bluetoothctl` for you, resolves
adapter BD addresses itself, skips combos already paired, retries each combo
up to 3 times, always disconnects afterward (so it never blocks the next
adapter), and pauses with the passkey on screen for you to confirm on the
sensor.

```
python sensor_onboarding/pair_sensors.py --adapters hci0 hci1 hci2 hci3 --sensors <MAC1> <MAC2> <MAC3>
python sensor_onboarding/pair_sensors.py --adapters hci0 --sensors <MAC1> --verify   # also runs test_sensor.py afterwards
```

The manual steps above are what it automates — useful as reference/fallback
if a particular combo needs hands-on troubleshooting.

Bonds are per **(adapter, sensor)** pair — a bond made on one adapter is
invisible to the others. If you have N adapters and M new sensors, that's
N×M pairings, each requiring its own physical button-press confirmation.
Within one manual `bluetoothctl` session you can pair all M sensors to the
current adapter before moving to the next adapter — `select` only needs to
change once per adapter, saving N−1 `select` calls versus doing it fully
per-combination.

### Verifying everything at once

```
python sensor_onboarding/test_sensor.py --adapter <hciN> <MAC1> <MAC2> <MAC3>
```

Run once per adapter. All (adapter × sensor) combinations should show
successful reads before touching `config.py`.

---

## After all pairings are verified

Uncomment the new sensors in `SENSORS_SHT` in `config.py`, then do a manual
`run_all_sensors.sh` run and check the log before re-enabling cron.
