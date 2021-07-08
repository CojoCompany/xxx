#!/bin/bash
screen -X -S coink kill
ampy --port /dev/ttyUSB0 put node_mcu/boot.py
ampy --port /dev/ttyUSB0 put node_mcu/capture.py
ampy --port /dev/ttyUSB0 put config.json
ampy --port /dev/ttyUSB0 put node_mcu/main.py
ampy --port /dev/ttyUSB0 put node_mcu/run.py
