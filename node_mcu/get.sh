#!/bin/bash
screen -X -S coink kill
ampy --port /dev/ttyUSB0 get $1
