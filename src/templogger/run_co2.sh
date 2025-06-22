#!/bin/bash
python /home/cvraspi/myfiles/co2/main.py -s cron -c ~/myfiles/co2/

if [ $? -eq 0 ]; then
    echo "$(date +"[%a %b %d %H:%M:%S %Y]") co2 Script run successfully"
else
    echo "$(date +"[%a %b %d %H:%M:%S %Y]") co2 Script failed"
fi
