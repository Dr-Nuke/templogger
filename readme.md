This is a logger for the sensirion SHTX sensors on development boards
https://sensirion.com/products/catalog/SHT4x-Smart-Gadget

Quick start:
* rename config_template to config
* in there, add your sensors with MACS and Names
* adjust the list of bluetooth adapters

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