#!/usr/bin/python
# -*- coding: utf-8 -*-

""" This file is part of B{Domogik} project (U{http://www.domogik.org}).

License
=======

B{Domogik} is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

B{Domogik} is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Domogik. If not, see U{http://www.gnu.org/licenses}.

Plugin purpose
==============

PLCBUS client

Implements
==========

- plcbusManager.__init__(self)
- plcbusManager.plcbus_cmnd_cb(self, message)
- plcbusManager.plcbus_send_ack(self, message)

@author: Francois PINET <domopyx@gmail.com>
@copyright: (C) 2007-2016 Domogik project
@license: GPL(v3)
@organization: Domogik
"""

from lib.PLCBusAPI import PLCBUSAPI
import threading
import time
import re
import json
import logging

class PlcBusManager:
    ''' Manage PLCBus technology, send and receive order/state
    '''

    def __init__(self, log, config, state_cb):
        '''
        Manages the plcbus domogik plugin
        '''
        # Load config
        log.debug("Reading config")

        ### get all config keys
        plcbus_device = config['device']
        self._usercode = config['usercode']
        self._probe_inter = int(config['probe-interval'])
        self._probe_list = config['probe-list']
        self.log = log
        self.state_cb = state_cb

        # Init Plcbus
        self.api = PLCBUSAPI(self.log, plcbus_device, self._command_cb, self._message_cb)
        if self._probe_inter == 0:
            self.log.warning(
                "The probe interval has been set to 0. This is not correct. The plugin will use a probe interval of 5 seconds")
            self._probe_inter = 5
        self._probe_status = {}
        self._probe_thr = Timer(self._probe_inter, self._send_probe, self.log)
        self._probe_thr.start()


    def _send_probe(self):
        ''' Send probe message 

        '''
        self.log.debug("send_probe(self)")
        for h in self._probe_list:
            self.log.debug("send get_all_id")
            self.api.send("GET_ALL_ID_PULSE", h, self._usercode, 0, 0)
            time.sleep(1)
            self.log.debug("send get_all_on_id")
            self.api.send("GET_ALL_ON_ID_PULSE", h, self._usercode, 0, 0)
            time.sleep(1)


    def plcbus_cmnd(self, dev, cmd, user, brightness):
        '''
        General callback for all command messages
        '''
        level = 0
        rate = 2
        
        if brightness is not None:
            level = brightness
            cmd = 'PRESET_DIM'

        self.log.debug("%s received : device = %s, user code = %s, level = " \
                       "%s, rate = %s" % (cmd, dev, user, level, rate))
        self.api.send(cmd, dev, user, level, rate)

        if cmd == 'PRESET_DIM' and level == 0:
            self.log.debug("cmd : %s " % cmd)
            self.log.debug("level : %s " % level)
            self.api.send("OFF", dev, user)

        if cmd == 'PRESET_DIM' and level != 0:
            self.log.debug('WORKAROUD : on fait suivre le DIM d un ON pour garder les widgets switch allumes')
            self.log.debug("DEBUG cmd : %s " % cmd)
            self.log.debug("DEBUG level : %s " % level)
            self.api.send("ON", dev, user)


    def _command_cb(self, f):
        ''' Called by the plcbus library when a command has been sent.
        If the commands need an ack, this callback will be called only after the ACK has been received
        @param : plcbus frame as an array
        '''
        if f["d_command"] == "GET_ALL_ID_PULSE":
            #print("elif fd_command =GET ALL  PULSE ")
            #           data = int("%s%s" % (f["d_data1"], f["d_data2"]))
            #        Workaround with autodetection problem force data to 511
            #        to consider discover of device with value from 0 to 9
            #        Could also be set to 4095 to force from 0 to F
            #data = 511
            
            data = int("%s" % f["d_data2"]) + int("%s" % f["d_data1"]) * 256
            house = f["d_home_unit"][0]
            for i in range(0, 16):
                unit = data >> i & 1
                code = "%s%s" % (house, i + 1)
                if unit and not code in self._probe_status:
                    self._probe_status[code] = ""
                    self.log.info("New device discovered : %s" % code)
                elif (not unit) and code in self._probe_status:
                    del self._probe_status[code]
            
        elif f["d_command"] == "GET_ALL_ON_ID_PULSE":
            self.log.debug("elif fd_command =GET ALL ON ID PULSE ")
            data = "%s%s" % (bin(f["d_data1"])[2:].zfill(8), bin(f["d_data2"])[2:].zfill(8))
            self.log.debug("f : %s" % f)
            self.log.debug("data : %s" % data)
            house = f["d_home_unit"][0]
            item = 16
            for c in data:
                unit = int(c)
                code = "%s%s" % (house, item)
                self.log.debug("State : %s " % code, unit)
                if code in self._probe_status and (self._probe_status[code] != str(unit)):
                    self.log.debug('DEBUG entering into IF detection GET_ALL_ON')
                    self._probe_status[code] = str(unit)
                    if unit == 1:
                        command = 1
                    else:
                        command = 0
                    self.log.info("New status for device : %s is now %s " % (code, command))
                    self.state_cb(code, command)
                item = item - 1
        else:
            self.log.debug("DEBUG setting device state")
            self.state_cb(f["d_home_unit"], f["d_command"])


    def _message_cb(self, message):
        self.log.debug("Message : %s " % message)


class Timer():
    """
    Timer will call a callback function each n seconds
    """
#    _time = 0
#    _callback = None
#    _timer = None

    def __init__(self, time, cb, log):
        """
        Constructor : create the internal timer
        @param time : time of loop in second
        @param cb : callback function which will be call eact 'time' seconds
        """
        self._stop = threading.Event()
        self._timer = self.__InternalTimer(time, cb, self._stop, log)
        self.log = log
        self.log.debug(u"New timer created : %s " % self)

    def start(self):
        """
        Start the timer
        """
        self._timer.start()

    def get_stop(self):
        """ Returns the threading.Event instance used to stop the XplTimer
        """
        return self._stop

    def get_timer(self):
        """
        Waits for the internal thread to finish
        """
        return self._timer

    def __del__(self):
        self.log.debug(u"__del__ Manager")
        self.stop()

    def stop(self):
        """
        Stop the timer
        """
        self.log.debug(u"Timer : stop, try to join() internal thread")
        self._stop.set()
        self._timer.join()
        self.log.debug(u"Timer : stop, internal thread joined, unregister it")

    class __InternalTimer(threading.Thread):
        '''
        Internal timer class
        '''
        def __init__(self, time, cb, stop, log):
            '''
            @param time : interval between each callback call
            @param cb : callback function
            @param stop : Event to check for stop thread
            '''
            threading.Thread.__init__(self)
            self._time = time
            self._cb = cb
            self._stop = stop
            self.name = "internal-timer"
            self.log = log

        def run(self):
            '''
            Call the callback every X seconds
            '''
            while not self._stop.isSet():
                self._cb()
                self._stop.wait(self._time)
