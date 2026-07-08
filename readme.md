This is a logger for the sensirion SHTX sensors on development boards
https://sensirion.com/products/catalog/SHT4x-Smart-Gadget

Quick start:
* rename config_template to config
* in there, add your sensors with MACS and Names
* adjust the list of bluetooth adapters
* run the [Redis Stack Docker Container](https://hub.docker.com/r/redis/redis-stack)

## SHT43 boards with firmware >= v0.5.3 (2024-02-28): encryption & pairing

Starting with firmware v0.5.3 (see the [Sensirion/sht43-demoboard-ble-firmware](https://github.com/Sensirion/sht43-demoboard-ble-firmware)
changelog), the SHT43 demo board's Battery/Humidity/Temperature GATT
characteristics require `SECURE_ACCESS` — an encrypted BLE link. Without it,
the first read fails with `ATT error: 0x0e` and the sensor drops the
connection. This does **not** affect older SHT31/SHT41 Smart Gadgets, which
have no such requirement.

Encryption requires the host to have bonded (paired) with the sensor first
via BlueZ. Once bonded, `bleak`/BlueZ auto-negotiate encryption on every
reconnect — no code changes needed. But bonds are per (Bluetooth adapter,
sensor) pair: if you run multiple adapters (see `ADAPTERS` in `config.py`),
each new SHT43 sensor needs to be paired with *every* adapter that might be
assigned to it.

**Before adding a new SHT43 sensor to `SENSORS_SHT`:**
1. Find your adapters: `hciconfig -a` — check the `Bus:` field (`USB` vs
   `UART`); don't assume the onboard chip is `hci0` by index, its
   enumeration order isn't guaranteed.
2. Pair every (adapter, sensor) combination. Either automate it:
   ```
   python sensor_onboarding/pair_sensors.py --adapters hci0 hci1 ... --sensors <MAC1> <MAC2> ...
   ```
   (drives `bluetoothctl` for you; only the physical confirm-button press on
   the sensor stays manual) — or follow the manual walkthrough in
   `sensor_onboarding/pairing_guide.md`.
3. Verify with `python sensor_onboarding/test_sensor.py --adapter <hciN> <MAC>` —
   Battery/Humidity/Temperature should read real values, not `Not connected`.
4. Only then uncomment the sensor in `config.py` and do a manual
   `run_all_sensors.sh` run before trusting it to cron.

Running the collector against an unbonded SHT43 sensor can cascade into BLE
stack failures affecting *other*, unrelated sensors — see
`sensor_onboarding/pairing_guide.md` for details if something goes wrong.

generic vscode run config:
       
     {
        "name": "templogger",
        "type": "python",
        "justMyCode": false,
        "request": "launch",
        "program": "${workspaceFolder}/src/templogger/collector.py",
        "console": "integratedTerminal",
        "cwd": "/path/to/templogger",
        "env": {
            "PYTHONPATH": "${workspaceFolder}/src"
            },

intended for use with systemd service