#!/bin/bash
screen -X -S coink kill
screen -dmS coink /dev/ttyUSB0 115200
screen -x coink
screen -X -S coink kill
