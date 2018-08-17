import json
import sys
import time
import array

import urequests

import machine
from machine import I2C
from machine import Pin
from machine import Signal
from network import WLAN
from network import STA_IF


scan_address = 53

write_address = 0x6a
read_address = 0x6b

register_config = 0x10
register_mod1 = 0x11

reply = bytearray(7)
trigger = 1 << 7
request = bytearray([read_address, trigger + 0x00])

num_measures= 500

x_array = array.array('h', (0 for i in range(num_measures)))
y_array = array.array('h', (0 for i in range(num_measures)))
z_array = array.array('h', (0 for i in range(num_measures)))
t_array = array.array('h', (0 for i in range(num_measures)))


def coin_detected():
    threshold = x_array[0] * 0.8
    for i in range(num_measures):
        if x_array[i] < threshold:
            return i


def twos_complement(val, bits):
    if (val & (1 << (bits - 1))) != 0:
        val = val - (1 << bits)
    return val


def read_registers(i2c):

    i2c.start()
    ack = i2c.write(request)
    i2c.readinto(reply)
    i2c.stop()

    return reply, ack


def write_register(i2c, register, value):
    """
    Write a register with the given value.
    """
    message = bytearray([write_address, register, value])
    i2c.start()
    ack = i2c.write(message)
    i2c.stop()
    if not ack == 3:
        message = 'Writing register failed after {} bytes!\n'
        sys.stderr.write(message.format(ack))


def read_sensor(i2c):
    reply, ack = read_registers(i2c)

    x_high = reply[0]
    x_low = (reply[4] >> 4)
    y_high = reply[1]
    y_low = (reply[4] & 0x0F)
    z_high = reply[2]
    z_low = (reply[5] & 0x0F)
    t_high = reply[3]
    t_low = (reply[5] >> 6)

    x = twos_complement((x_high << 4) + x_low, 12)
    y = twos_complement((y_high << 4) + y_low, 12)
    z = twos_complement((z_high << 4) + z_low, 12)
    t = twos_complement((t_high << 2) + t_low, 10)

    return reply, (x, y, z, t), ack


def get_sensor_values(i2c):
    for i in range(100):
        reply, readings, ack = read_sensor(i2c)
        status = reply[6]
        pd0 = bool(status & 0b100)
        if not pd0:
            message = 'Power-down flag 0 (PD0) was zero! Retrying...\n'
            sys.stderr.write(message.format(ack))
            continue
        pd3 = bool(status & 0b1000)
        if not pd3:
            message = 'Power-down flag 3 (PD3) was zero! Retrying...\n'
            sys.stderr.write(message.format(ack))
            continue
        tbit = bool(status & 0b10000)
        if tbit:
            message = 'T-bit was one! Retrying...\n'
            sys.stderr.write(message.format(ack))
            continue
        frame_counter = status & 0b11
        break
    return reply, readings, ack, i, pd0, frame_counter


class Node:
    def __init__(self):
        """
        Initialize the node.

        Performs the hardware setup and configuration loading from
        `config.json` file.
        """
        machine.freq(160000000)

        self.led = Signal(Pin(2, Pin.OUT), invert=True)
        self.led.off()

        self.i2c = I2C(scl=Pin(5), sda=Pin(4), freq=400000)

        self.station = WLAN(STA_IF)

        self.config = json.load(open('config.json'))

    def setup_sensor(self):
        """
        Configure the sensor to use Master Controlled Mode.

        Also, enable clock stretching and disable interruptions. Sensor read
        out is suppressed during ongoing ADC conversions.

        Returns
        -------
        The number of acklowledges the sensor sent back. Should be 3.
        """
        devices = self.i2c.scan()
        if not scan_address in devices:
            message = 'Sensor "{}" does not seem to be available!\n'
            sys.stderr.write(message.format(scan_address))
            return

        write_register(self.i2c, register_config, 0b00001001)
        write_register(self.i2c, register_mod1, 0b10000101)

    def connect_wifi(self):
        """
        Connect the node to a wireless network.

        The wireless network must be defined in the `config.json` file.
        """
        self.station.active(True)
        config = self.config['wifi']
        self.station.connect(config['ssid'], config['password'])
        for i in range(10):
            if self.station.isconnected():
                break
            sys.stdout.write('Waiting for connection to be established...\n')
            time.sleep(1)
        else:
            message = 'Could not establish connection...\n'
            sys.stderr.write(message.format(ack))
            led.on()

    def update_savings(self, savings):
        """
        Update the remote servers with the new savings.
        """
        url = 'https://api.thingspeak.com/update'
        url += '?api_key={key}&field1={savings}'
        url = url.format(key=self.config['thingspeak_key'], savings=savings)
        reply = urequests.get(url)
        return reply

    def read_coin(self):
        """
        Read magnetic sensor values `num_measures` times. The board's LED is
        turned on when the readings start and turned off when done.
        """
        self.led.on()
        for i in range (num_measures):
            reply, (x, y, z, t), ack = read_sensor(self.i2c)
            # Make sure we never try to store a value higher than the size of
            # the `t_array` elements
            t_array[i] = time.ticks_us() % 65536
            x_array[i] = x
            y_array[i] = y
            z_array[i] = z
        self.led.off()

    def save_readings(self, file_name):
        """
        Save magnetic sensor values from internal memory to a file with
        the name 'file_name'.
        """
        with open(file_name, 'w') as f:
            f.write('t,x,y,z\n')
            for i in range (num_measures):
                line = '{t},{x},{y},{z}\n'.format(
                    t=t_array[i],
                    x=x_array[i],
                    y=y_array[i],
                    z=z_array[i],
                )
                f.write(line)

    def send_readings(self, file_name):
        """
        Send magnetic sensor values from a file to the server detailed
        on the file config.json.
        """
        with open(file_name, 'r') as f:
            url_post = 'http://{host}:{port}/whatesp'.format(
                   host=self.config['server']['host'],
                   port=self.config['server']['port']
               )
            response = urequests.post(url_post, data=f.read())

    def save_select_readings(self, file_name, start):
        """
        Save relevant magnetic sensor values from internal memory to
        a file with the name 'file_name'.
        """
        with open(file_name, 'w') as f:
            f.write('t,x,y,z\n')
            for i in range(max(0, start - 10), min(num_measures, start + 50)):
                line = '{t},{x},{y},{z}\n'.format(
                    t=t_array[i],
                    x=x_array[i],
                    y=y_array[i],
                    z=z_array[i]
                )
                f.write(line)

    def loop(self):
        """
        Read and send coin values forever.
        """
        while True:
            self.read_coin()
            start = coin_detected()
            print('Start at %s' % start)
            if not start:
                continue
            print('Saving readings...')
            self.save_select_readings('1_sel.csv', start)
            print('Sending readings...')
            self.send_readings('1_sel.csv')


def main():
    node = Node()
    node.setup_sensor()
    node.connect_wifi()
    node.loop()

if __name__ == '__main__':
    main()
