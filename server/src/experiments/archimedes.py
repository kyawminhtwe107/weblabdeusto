#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2005 onwards University of Deusto
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#
# This software consists of contributions made by many individuals,
# listed below:
#
# Author: Luis Rodriguez-Gil <luis.rodriguezgil@deusto.es>
#
import base64

import traceback
import urllib2
import json
import threading
import weakref
from voodoo import log

from voodoo.lock import locked
from voodoo.log import logged
from voodoo.override import Override
from weblab.experiment.experiment import Experiment
import weblab.core.coordinator.coordinator as Coordinator

#module_directory = os.path.join(*__name__.split('.')[:-1])



from multiprocessing.pool import ThreadPool


# Assign a process-level workpool. Previously we relied on a class-local workpool which was
# active only while there were users logged in, but making it global makes it easier to make
# the experiment concurrent, and to work around a certain scheduling bug.
WORKPOOL = ThreadPool(8)

DEFAULT_ARCHIMEDES_BOARD_TIMEOUT = 2


class Archimedes(Experiment):
    """
    The archimedes experiment. Unittests for this class can be found in the test folder (test_archimedes).
    """

    def __init__(self, coord_address, locator, cfg_manager, *args, **kwargs):
        super(Archimedes, self).__init__(*args, **kwargs)

        self.DEBUG = False

        self._cfg_manager = cfg_manager

        # IP of the board, raspberry, beagle, or whatever.
        # self.board_location    = self._cfg_manager.get_value('archimedes_board_location', 'http://192.168.0.161:2001/')

        self.archimedes_instances = self._cfg_manager.get_value('archimedes_instances')

        self.board_timeout = self._cfg_manager.get_value('archimedes_board_timeout', DEFAULT_ARCHIMEDES_BOARD_TIMEOUT)

        self.webcams_info = self._cfg_manager.get_value('webcams_info', [])
        self.real_device = self._cfg_manager.get_value("archimedes_real_device", True)

        self.opener = urllib2.build_opener(urllib2.ProxyHandler({}))

        self.initial_configuration = {}
        for pos, webcam_config in enumerate(self.webcams_info):
            num = pos + 1
            webcam_url = webcam_config.get('webcam_url')
            mjpeg_url = webcam_config.get('mjpeg_url')
            mjpeg_width = webcam_config.get('mjpeg_width')
            mjpeg_height = webcam_config.get('mjpeg_height')

            if webcam_url is not None:
                self.initial_configuration['webcam%s' % num] = webcam_url
            if mjpeg_url is not None:
                self.initial_configuration['mjpeg%s' % num] = mjpeg_url
            if mjpeg_width is not None:
                self.initial_configuration['mjpegWidth%s' % num] = mjpeg_width
            if mjpeg_height is not None:
                self.initial_configuration['mjpegHeight%s' % num] = mjpeg_height


    @Override(Experiment)
    @logged("info")
    def do_start_experiment(self, client_initial_data, server_initial_data):
        """
        Callback run when the experiment is started.
        """
        if self.DEBUG:
            print "[Archimedes] do_start_experiment called"

        # Work around for a Python bug in ThreadPool. It has actually been fixed in latest
        # python versions.
        if not hasattr(threading.current_thread(), "_children"):
            threading.current_thread()._children = weakref.WeakKeyDictionary()

        current_config = self.initial_configuration.copy()

        # Immediately pull all the balls up (so that all balls start up)
        # Carry out the operation in parallel.
        responses = WORKPOOL.map(lambda board: self._send(board, "up"), self.archimedes_instances.values())
        # Ignore the response. Assume it worked.

        # The client initial data is meant to contain a structure that defines what the client should show.
        return json.dumps(
            {"initial_configuration": json.dumps(current_config), "view": client_initial_data, "batch": False})


    def handle_command_allinfo(self, command):
        """
        Handles an ALLINFO command, which has the format: ALLINFO:instance1:instance2...
        """
        boards = command.split(":")[1:]
        response = {}

        # Carry out the operation in parallel.
        infos = WORKPOOL.map(self.obtain_board_info, [self.archimedes_instances.get(b) for b in boards])
        for i in range(len(boards)):
            response[boards[i]] = infos[i]

        return json.dumps(response)

    def obtain_board_info(self, board):
        """
        Obtains the info for a specific board by carrying out an HTTP request to it.
        @param board: The URL / IP to the board.
        @return: json-able data object with the info for the specified board. A string containing
        the word ERROR if a problem occurs.
        """
        if board is None:
            info = "ERROR"

        info = {}

        load = self._send(board, "load")
        level = self._send(board, "level")
        status = self._send(board, "status")

        if load == "ERROR":
            info["load"] = "ERROR"
        else:
            num = load.split("=")[1]
            info["load"] = num

        if level == "ERROR":
            info["level"] = "ERROR"
        else:
            num = level.split("=")[1]
            info["level"] = num

        if status == "ERROR":
            info["ball_status"] = "ERROR"
        else:
            info["ball_status"] = status

        return info


    @Override(Experiment)
    @logged("info")
    def do_send_command_to_device(self, command):
        """
        Callback run when the client sends a command to the experiment
        @param command Command sent by the client, as a string.

        The supported commands are the following:
        [<instance>]:<command>
        <command>

        If an instance is specified, the command is as such: first:UP
        If not: UP

        If an instance is not specified the default instance is used
        (instance with the name: default).

        This is to keep supporting easily the first version of the experiment (single instance).

        Replies with the data, or with "ERROR:<something>".
        """
        if self.DEBUG:
            print "[Archimedes]: do_send_command_to_device called: %s" % command

        # HANDLE NON-INSTANCE-SPECIFIC COMMANDS
        # We expect a command like: "ALLINFO:archimedes1:archimedes2"
        if command.startswith("ALLINFO"):
            return self.handle_command_allinfo(command)


        # HANDLE INSTANCE-SPECIFIC COMMANDS
        if ":" in command:
            s = command.split(":")
            target_board = s[0]
            board_command = s[1]
        else:
            target_board = "default"
            board_command = command

        # Convert the target_board from its instance name (received by the client)
        # to the real address of its board.
        target_board = self.archimedes_instances.get(target_board)
        if target_board is None:
            return "ERROR: Instance doesn't exist."

        if board_command == "UP":
            return self._send(target_board, "up")
        elif board_command == "DOWN":
            return self._send(target_board, "down")
        elif board_command == "SLOW":
            return self._send(target_board, "slow")
        elif board_command == "LEVEL":
            resp = self._send(target_board, "level")
            if resp == "ERROR":
                return resp
            num = resp.split("=")[1]
            return num
        elif board_command == "LOAD":
            resp = self._send(target_board, "load")
            if resp == "ERROR":
                return resp
            num = resp.split("=")[1]
            return num

        elif board_command == "IMAGE":
            resp = self._send(target_board, "image")
            if resp == "ERROR":
                return resp
            img = base64.b64encode(resp)
            return img
        elif board_command == "PLOT":
            return self._send(target_board, "plotload")
        else:
            return "Unknown command. Allowed commands: " + "[UP | DOWN | SLOW | LEVEL | LOAD | IMAGE | PLOT]"

    def _send(self, board_location, command):
        if self.real_device:
            try:
                if self.DEBUG: print "[Archimedes]: Sending to board: ", command

                if not board_location.endswith("/"):
                    board_location += "/"

                return self.opener.open(board_location + command, timeout=self.board_timeout).read()
            except:
                log.log(Archimedes, log.level.Error, "Error: " + traceback.format_exc())
                return "ERROR"
        else:
            if self.DEBUG: print "[Archimedes]: Simulating request: ", command
            return self.simulate_instance_reply_to_command(command)

    def simulate_instance_reply_to_command(self, command):
        """
        Simulates an instance's reply to a command, for testing purposes.
        """
        if command == 'up':
            return "ball_up"
        elif command == 'down':
            return "ball_down"
        elif command == 'slow':
            return "ball_slow"
        elif command == 'level':
            return "LOAD=1200"
        elif command == 'status':
            return 'BALL_UP'
        elif command == 'load':
            return "LOAD=1300"
        elif command == "image":
            # A test image.
            img = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x80\x00\x00\x00\x80\x08\x06\x00\x00\x00\xc3>a\xcb\x00\x00\x0f\xe4IDATx\x9c\xed]Kl\x1bG\x9a\xfe\xaa\x1f|\x89\xa2Z\x96,{!\xd9\xd6#P\xbc~\x042\xb0\xca\xc3\xd0%\x07\x01Y8\x88s\xca\x1e\x83\x01f\x90\xc3\x00sZ\xec=\x87\x00\x8e\xf7\x12\x03I\x80 \xc1\xec!\xc9^\x02\xac\x17\xd9\x04F\x02g3\xf0\xc1\x19[\xda1\xa0$\xb2\x03E\x96\xb0\xd6c\x94\x8c\xcc&-\xbe\xfaUs \x8b,\xb5(\xa9)USM\xaa?\xa0\xc0&\xd9\x8f\xea\xfa\xbf\xfa\xfe\xbf\x1e]\r\x84\x08\x11"D\x88\x10!B\x84\x08\x11"D\x88\x10!B\x84\x08\x11"D\x88\x10!B\x84h[\x90\x83\xba\xf0\xa9S\xa7H2\x99Dgg\':::H"\x91@GG\x07\x89\xc7\xe3PU\x95\xc8\xb2\x0cI\x92@)=\xb0<\xee\x17\x84\x10J)\x85m\xdb0\x0c\x83\x16\n\x05\xe4r9\x9a\xcf\xe7i.\x97C6\x9b\xc5\xec\xec,=\xd0<6\xfb\x82\x03\x03\x03D\xd34\xa4R)\x92J\xa5HWW\x97\x94\xc9d\xc8\xfa\xfa:QU\x95\xdc\xbe}[\xaa\x93\xb7V#\x81\xdb\xa8\xf4\xe4\xc9\x93t``\x80\x1a\x86A\xc7\xc6\xc6\x9cl6Ku]\xa7\xd9l\x96f2\x19\xaa\xeb:VWW\x9bN\x86\xa6\x15\xec\x91#GHww74M\x934M#\xc5bQ\x9a\x9b\x9b\x93\x7f\xf9\xe5\x17\t\x80;\x11.\xb5:(\x00\xa7\xb2mW\xb6\x1d\x00\xce\xb9s\xe7\xec\x91\x91\x11G\xd7u\xaa\xeb\xba\xa3\xeb:M\xa7\xd3\xc8f\xb3M#\x82\xef\x05\xac\xaa*\xd14\r\xdd\xdd\xddRww7\xb1,KZ\\\\\x94\xd7\xd7\xd7\x15\x002\x00\xe5\xec\xd9\xb3\xdaK/\xbd\xf4\xf2\xe4\xe4\xe4\xd9\xd1\xd1\xd1\x93\x84\x10\xa9\x95\xa5\xdf\x05J\x08\x01\xa5\xd4\xbey\xf3\xe6\xec\xd4\xd4\xd4\xcd\x0f?\xfcp\x1a\x80UI\xf6\xd3O?m\xf7\xf7\xf7\xdb\xba\xae;\xe9t\xdaI\xa7\xd3\xd0u\xbd)$\xf0\xb5\x90\x15E!\x9a\xa6\x91\x9e\x9e\x1e\xd2\xdd\xdd-=x\xf0@\xd6u]\x01\xa0\x02P\x9f\x7f\xfe\xf9\x13W\xaf^\xfd\xd7\xf3\xe7\xcf_\x96$)\xeeg^\x82\x84\\.7{\xf5\xea\xd5\x7f\x7f\xe7\x9dwn\xa0L\x02\x13\x80566fI\x92\xe4<~\xfc\xd8I\xa7\xd34\x93\xc9\xf8N\x02\xdf\x08 I\x12I\xa5R\xa4\xa7\xa7GR\x14E\xfa\xe9\xa7\x9f\xaa\x86\x8f\xc7\xe3\xb1\xf7\xde{\xef\xf7\xaf\xbe\xfa\xea\x1f$IJPZ\xbeO\xf6\xd9\xce \x84\x80\x90r\xb1/,,|\xfd\xfa\xeb\xaf\xff\xdb\xcc\xcc\xcc_\x01\x18\x00\xccT*e\r\r\r\xd9\xe9t\xday\xfc\xf8\xb1\xb3\xb1\xb1\xe1k\xa1\xf8E\x00\x92H$Hoo\xaf\xa4\xaa\xaa<??\xaf\x00\x88\x00\x88\x9c9s\xa6\xef\x93O>\xb96<<\xfc"\xa5\x14\x94R8\x8e\x03\xc7q\xc0\xbe\xb7+\x08!\x90$\xa9\x9a\x08!0\x0cc\xed\x8d7\xde\xf8\xdd\xf5\xeb\xd7\xff\x0f@\t\x80\xd1\xd9\xd9i\x9e:u\xca^__w\xd2\xe9\xb4S,\x16}+\x14\xd9\x87s\x12EQH*\x95\x92\x14E\x91\x17\x17\x17\x15\x00Q\x00\xb1\x0b\x17.\x0c\xdd\xb8q\xe3\xb3\xbe\xbe\xbe\x0b\xacyd\xdb6,\xcb\x82eYp\x1c\x07\xb6mW\t\xd1\xce\x89\x11]Q\x94\xe4+\xaf\xbcr\xf9\xc9\x93\'\xb3SSS\xcb\x00\xa8a\x18\xc8\xe7\xf3\xe8\xed\xed\xa5\x96e\xc10\x0c\xdf*\x86h\x02\x10\x00\x88\xc5br$\x12\x91VWWU\x94\x8d\x1f?s\xe6L\xff\x97_~\xf9\x9f\xc9dr\xd8\xb6m\x98\xa6Y5\xbcm\xdb\xd5\xda\xdf\xee\xa9\x1e\t$IR_|\xf1\xc5\x7f^^^\xbe\xf7\xfd\xf7\xdf\xff\x15e\x12\xd0B\xa1@5M\x83eY\xd40\x0c\xc1\xa6\xe2\x0c&\xf2|\x84\x10)\x91H\xc8\xb9\\NEY\xf6\xe3\xd1h4\xf9\xddw\xdf\xfd\xc7\xe0\xe0\xe0E\xbe\xc6\xb7\xbb\xe4\xef\x06I\x92 \xcb2\x14E\x81,\xcbp\x1cG\x7f\xf9\xe5\x97\xff\xe5\xce\x9d;\x0f\x00\xe4\x01\x94b\xb1\x98\xd9\xd9\xd9i\xe9\xban\x9b\xa6\tl\xedc\xd8_\x1e\x04\x9e\x8b\xa0L\x00R*\x95d\x00L\xfa\xe3W\xae\\\xf9\xcd\xe0\xe0\xe0E\xcb\xb2`\x9a&L\xd3\xacJ\xfdA\xd7\xc8\x83LL\t\r\xc3\x80eY\x90$I\xfb\xf8\xe3\x8f\xdf9v\xec\x98\x06 \x0e R,\x16\x15\x00R$\x12\x11i\xab*D\xba\x00\x02@\xa2\x94*\x8e\xe30\xe9O\x0c\r\r\x1d\xbbv\xed\xda5\x001\xde\xf0!j`\x84 \x84 \x99L\x1e\xed\xef\xefW>\xff\xfc\xf3iT:\x8e\xf2\xf9<\xed\xec\xec\xa4\xa5R\x89\x8aVL\xd1\xac\x92P\xe9\xdcAY\xfec\xaf\xbd\xf6\xdaeY\x96\xbbB\xe3o\x0f\xa6\x06,\x1e\xba|\xf9\xf2\xeb\x13\x13\x13\xa3\xa8\xa8\x00\x00%\x9b\xcd\xca\x8a\xa2\x08W\x01\x91\n\xc0\x8c_\xad\xfd\x00\x92o\xbf\xfd\xf6o\xbb\xba\xba\x06\xd9\xcd\x85\xa8\x0f>\x1e\x92eY\x1e\x19\x19\xd1>\xfd\xf4\xd3?\xa1\xd2m\xec8\x8e-\xcb2\xb5m;\x901\x00\xeb\xb7\xe7I\x10\x01\x10\xd34\xedXX\xf3\xbd\xc1q\x9cj\x80<>>>y\xe1\xc2\x85\x13(W&\x15\x80b\x9a&\x1b+\x11\x06E\xe0\xb9x\x02T]\x80m\xdbJH\x00\xef`$\x90$I\x99\x9c\x9c\x9c\xbcw\xef\xde2\xca\x1dD&\xa5\xd4B9.\x10\x06\xd1\xad\x007\x01\xa2\xb6m\x93\xd0\xf8\xde\xc1\xc7\x03\x97.]\xfa\'\x001T\xe2\x00\x94\xcbV\xa8\n\x88R\x00\xde\x05\xf0$Pm\xdb&\xc0\xe1\xe8\xe7\x17\x05\xd6#z\xfc\xf8\xf1\x93\xa8\x11@E\x8d\x00@\xb9\xbc\xf7]\xa8\xc2\xfb\x01P#\x80\x0c@\xa6\x94\x92\xd0\xf8\x8d\x81\xf5\x18J\x92\xd4\x81\xca\x18\n\x02\xae\x00\x0c\xbc\nH\xa8\x10\x00\x08\x15\xa0Q0\x15@\xad\xf6\xf3\x04\x10\xd6\x83+Z\x01\xf8m\t\xe5\x8e\xa1\xd0\xf8{@\xa5\xdc\x086\x1b_\x86\xe0\xd9R~(\xc0\xa6\x14*\xc0\xdeQ\t\x9e\x99\xe1ye\x15\x06?\xfa\x97\xb7034\xfe\xdeP\xa9<\xbc\xf1Y\xd9\ns\x01\xa2\x15\x80a\x8bL\x85$h\x1c\x952c\x86\xe7\'\xcb\n\x83/#L<\xc2\x18`\xdf`\xc6\xe7\xbf\x076\x06\xd8\x16!\t\x1aG\xa5\xccx\xd9\x17>\x85\xcfw\x02\xecG\x01\x1c\xc7A&\x93\x81eY\x82s\xd5\x1c\x10B\xa0i\x1a\x14Eh1\x0b%AS\x14\xa0Q\x12PJ\xf1\xc3\x0f?`ff\x06\xc5b\xd1\xc7\x9c\xf9\x0fI\x920::\x8a\xf1\xf1qD"\x91\x83\xce\xce\x164E\x01\xf8O/\xb8u\xeb\x16\xe6\xe6\xe6\xaa\xd3\xa7[\x19\x8e\xe3\xe0\xfe\xfd\xfbX[[\xc3\xa5K\x97\x1a"A3\xdcf\xd3\x14\xc0+\xe6\xe7\xe7\xf1\xf3\xcf?\x83=\x1c*I\xe5\xf8\xa7\xd5\xc8\xc0\xee\x99M\x00M\xa7\xd3\x98\x9e\x9e\xc6\xc5\x8b\x17\x0f8g\x9b\x11\xb8 \xf0\xfe\xfd\xfbP\x14\x05\x8a\xa2@U\xd5*\x11Z\x11\xcc\xf8l.\xe4\xc3\x87\x0f\xf1\xc2\x0b/x>\xbe-\x14\xa0Q\xff\xbf\xb1\xb1\x81h4\x8ah4\x8aH$R}\x80\x02\x00\xfa\xfa\xfa0>>\xeeWV\x85`ff\x06KKK\x9b&\x7f\x9a\xa6\x89b\xb1\x88R\xa9\x04\xdb\xb6!\xcb\xde&b\xb5\x05\x01\x80\xc6H\xc0j\x7f,\x16\x83,\xcb\x9b\xa4\x9f\xfd\x1ed\xa8\xaa\xbaE\xb1\xd8\xf7F\xe3\xa1\xb6 @#7-I\x12TU\xad+\xfd\xfc\x83\x15A\x06\x9f?F^Y\x96\xa1\xaa*,\xcbB\xe5I\xe1\x83\xca\xde\x164-\x06\xf0\x02BH\xd5\xf0|\xcdg\x86\xe7\x1f!\x0b"$I\xaa\xce|\xae\xa7\x02L\xd1\x0e\x9d\x02xu\x01ln<\xdb\xe6\x7fw\x1c\xa7\xfa\x10E\x90\t\xc0\xf2\xb7S\xab%T\x80m\xc0\x17\x9a\xbb\x00\x19\t\x82\xee\x06\xf8g\xfeZ\x01\x81\n\x02\xdd\xb5\xde\xbd\xdd\x88\x9a\x1c\x04\xdc\x8f~\xed\xb4\x8f\xd7\xf3\xf9\x8d\xc0\xb8\x00\xe6\x1b\xb7\xdb\xd7M\x82\xa0b7\xc37\x12\x147\xe3>\x03\xd3\xc3\x12d\xa3zE\x90j\xb6W\x04F\x01\xd8\xbe\xdbm{\x91\xd7\x83\xc6nyl4\xff\x87\xda\x05\x88(\xc0\x83B=\x99w\xff\x16\x94{h\t\x17\xd0\nF\x07\xb6W0\xf7oA\xba\x97\xc0\xb5\x02v\xdb7\xc8d\x10\x1d\xc4\x1e:\x17\xe0>\xae\xdey\xea\xfd\x17T\xec\xd7\x05\x1c\xbaV\xc0n\xb5\xc7\xbd\x1d4x\x89a\xf8\xcf  0\n\xc0\xf6\xe5\x8f\xa9w\x8e\xa0\xbb\x80z\xdb\xbb\xfd\xe7\xe5|~!01\x00?\x06\xe0%\x0e\x08*vrUAtcMy.\xc0\xeb~A\xf1\x8b{\x85\xe8\x9a}\xa8\x14\x80\xed\xb7\xd3\xf1Aw\x01\xc0f7\xe6\x1e\xd2f\x9fA\xca\x7f`b\x00/. h\xf2\xe9\xc6n\x91~\xa3.\xa0-\x14\xa0\xd1 p\xbb\xfd\x83Z\x83x4\x92\x7f/\x15\xe2P6\x03\xb7\xfbo\xb7}\x82\x86\xfd*X\xb3\xee3P\n\xb0\xdd\xfe\xad\x12\x03\xec\xa6\x00{)\x0b\xbf\xd1\xb4 p\xb7Y<\x8dH^Pg\x04\xed4\xdd\xbbQ\x17\xd0\xacg!\x02\xf7`\xc8n\xc7\x07]\x01\xbc\xee\xb7\xdb\xbem\xe5\x02\xbc\xcc\x93\xdbI\xe6\xdb\xcd\x05xQ\xb0f\xddg`\xfa\x01\xdcm\xe6\xed\x82\xa8V \x00\xff}\xa7}\xbd\x9c\xcfo\x04\xae\'p\xa7\xe8\xb9\x91\xf3\x1d\x04vsS\x8d6\x89\x9b\x81@\xb5\x02\xbc\xd4\x98VR\x00\xf7\x7f\x80\xf7i\xe3\xcd\x9a^\x1e(\x17\xe0\xa5\x06\xf1\x9fA\xc5N\xf9\xf4z\x0fm\xd5\x11\xe4\xb5\xf6\xef&\x9d^\xcfuP\xd8\xcdU\x85\x1dA\x1e\xf6\x8dF\xa30M\xb3n+ \x97\xcba~~\x1e\x95\x97\'\x05\x0e\x8a\xa2 \x9b\xcdn\xdb\nH&\x93\xec\x8d\xe8\x9e\xce\xd76.\x00\xf0&y\x00p\xf4\xe8Q\xac\xac\xac\xd4\xdd\'\x9b\xcdbuu\x15\xa6i\x06\xae3\x88\x10\x82H$\x82D"\x81x|\xeb[p)\xa5\x18\x1a\x1aj\xc8\x054\x03\x81Y \x82\xed\xf3\xd4SOauu\x15\x94\xd2\xba\x85\xc0\x9e\x1c\x0e\xda\xaa!\xfc+a\xebA\xd34\x8c\x8c\x8c\xa0X,\x1e.\x17\x00x\'\x81m\xdbH&\x938w\xee\x1c~\xfc\xf1\xc7-\xb5E\x92\xa4\xea\x92kA\x8b\x05\x08!\xd5\xf7\xff\x01\x9b\xf3\x17\x8f\xc7\xf1\xdcs\xcf\x01\xf0\x1e\xdd7+\x08\x0c\xd4\xd3\xc1\xac\x97lxx\x18\x9a\xa6\xe1\xe1\xc3\x87\xc8\xe7\xf3\x00j\xcb\xa7\x07\xf9\xf53l\r\x00~q\x8b#G\x8e\xe0\xf4\xe9\xd3P\x14\x05\xc5b1py\x0fT\x10\xc8^\xa4\xa8(\nzzz\xd0\xdb\xdb\xbb\xe5\\\xad\x00\xb7+\xe0\x17\x8aboL\xdd\r^\xbb\x8c\xf7\x8b@-\x11\xc3\x16\x81P\x14\xa5\xbaZH+\xc2\xdd\x82\xb1,\x0b\xa5R\xa9\xa1\xc5-\xda&\x06h\xa4\xe7\x8e\xd2\xf2\x8aZ\xcc\xff1"\xb4\xda\x1a\x81\x0c\xac\x16\x1b\x86Q]!\xac\xd1\xe3\xfdF\xa0\\\x00\xdb\xbfT*\xc1\xb2\xac\xaa?\xdd\x0b\x98;\x11\x85\xbd\xacNF)\xdd\x14\xb74b\xd0\xb6Q\x00\x86Fo\x9eI\xe7^j\xff\xca\xca\nfgg\x91\xcdf\x1b>v;\x8c\x8e\x8eV\x83\xb9F\xb0\xd7\xb1\x8b\xb6\x1b\x0e\xde\xeb1\x8d\x1c\xcb\x0c\xaf\xeb\xbap\xb71;;\x8b\x85\x85\x05\x9c?\x7f\x1e\'N\x9chY\xb7\xe4F\xa0\x82\xc0\xbdB\xd7uLOO#\x93\xc9\x00@\xb5\xa3HTg\x11\xf3\xe5\x85B\x01w\xef\xde\xc5\xfc\xfc<\xc6\xc6\xc6\xa0i\x9a\x90\xf3ow\xcdC\xad\x00\x8d\xe0\xd7_\x7fE&\x93\xa9\xb6\x1cdY\x86\xa2(BH\xc0\x8c\xcfb\n\xdb\xb6\xb1\xbe\xbe\x8e\xa9\xa9)LNN\n\xba\x83\x83C\xe0\x82\xc0\xbd\x82\x19\x9d_dZT3\x92\x11@\x96\xe5j[\xde\xef\x9e\xba\xb6R\x00\xc0?\x15`\x830,1\x12\xb01\x83\xfd\xfajf\x08\xd6\xcd\xab(\n\x0c\xc3\xa8\x0e\xf8\xf8u_mC\x00\xbfo\x84\x10\x82\x9e\x9e\x9e\xea\xea\xe2\xfc\x02\xd3"\x025\xfe\\l!hB\x08\x06\x06\x06\xaaK\xc3\xb62Z^\x01\x00\xa0\xb7\xb7\x17\x83\x83\x83X[[\xab\xfb@\xa6(\xb0\x18\xe3\xf8\xf1\xe38}\xfa4J\xa5\x92/\xd7\xf1\xeb\x9c\xf5\xd0\xf2A ;\xf7\xb3\xcf>\xdb\x94\x81"\xe6\x06\xd8\xfa\xff\xcd\xb87?\xd1\xf2\xcd@\x16\xa5SJ\x11\x89D|o\x9f\xb3\xde=\xb6j\xb9\x9f\xf7\xd5\x0c\xb4\xbc\x02\xb0\x916\xc30\x00\xf8\xffH\x15\xa5\x14\x86aT\x9b\x84\xad\x8e\xb6\x88\x01,\xcbB\xa1P\xa8\x0e$\xf9\xa5\x02|\x17\xb5\xdf\xd3\xd2\xda\xa6\x15\x00\xf8/g\xcc\xf77k\xb2h\xab-\t\xbf\x13Z>\x06\xe0\xaf\x13\xb4\xd96\xfbA\xb3\x08\x16\x98\xe7\x02BlE+\xbb\x00\xcaR+<\xd0q\x98\xe1\x87\x02l\xb2\xb4m\xdbvh\xfc\xc6Qqi6j\xe5I\xb9$\x0c\xa2\t@\xddiuu\xf5o\x82\xafqh\xb0\xb4\xb4\xb4\n\xc0\xa9$\x06\xa1D\x10\xe9\x02\xa8k\xdb\x01\xe0\xdc\xbau\xeb\xc7\xd1\xd1\xd1gC\x15h\x0c\x94R|\xf1\xc5\x17wP#\x00K\x81W\x00>\xb3\xf6\xcd\x9b7\xff\x92\xcf\xe7\x9f\x08\xbeN\xdbcyy\xf9\xd1W_}\xf5\x17\x006*e\x89\xcd\xee@\x08D\xcd\xbb&(\x93I\xa9\xa4\x08\x80\x18\x80\x98i\x9aQJ)y\xe6\x99g\xce\x08\xbaV\xdb\xc30\x8c\xe2\x9bo\xbe\xf9\xc7t:\xbd\x06\xe0\t\x80\r\x00y\x00E\x00\x06\x00\x0b\x82\xd4@\x94\x020\x9fT\xad\xf9\x00\xccJ2\xae_\xbf\xfe\xe7\xb9\xb9\xb9\x87\x82\xae\xd5\xd6\xa0\x94\xd2w\xdf}\xf7\xbf\x16\x16\x16V\x00\x94P6\xb8\x89\x9a\x120\xc3\x0bQ\x01\x91\n\xc0T@FY\x05T\x94\x95 J)\x8d\xde\xbbw\xef\xd1\xc4\xc4\xc4\xd9x\xbdGgC\x00\x00\x1c\xc7q>\xfa\xe8\xa3/\xbf\xf9\xe6\x9b{\x00\xb2\x95\xe4V\x00F\x86\xc0\x12\x80w\x05j%E\xf2\xf9<fffV&&&\xfeQU\xd5\x88\xa0\xeb\xb6\r,\xcb\xb2?\xf8\xe0\x83\x1b_\x7f\xfd53~\x065\x12\xe4\x00\x14P\x93\xff\xc0\x11\x00\xa8\x91@\xc6V%P\x00(\xba\xae\x1b\xd3\xd3\xd3\xff?>>>\x92H$\x82\xfd\x1e\xf8&B\xd7\xf5\x8d\xb7\xdez\xeb\xbf\xef\xde\xbd{\x1f5\xa33\x02l\xa0L\x00V\xfb\x85\xf9\x7f@,\x01\x80\x9a\n\xf0\xee\x80}\xca\x00\xe4l6k\xde\xbe}{axx\xf8h__\x9f\x7f\xf3\xaa[\x04w\xee\xdc\x99\xbbr\xe5\xca\xff<z\xf4h\x195\xc3oW\xfb\xf9X@\x08\xfcP\x00wbn\xa1\xfa\xbdP(X\xdf~\xfb\xed\xfc\xfa\xfa\xfaF\x7f\x7f\xbf\x96L&\x13\x02\xf3\xd1\x12X\\\\\\{\xff\xfd\xf7\xff\xf7\xb3\xcf>\xbbS(\x14t\xd4\x0c\x9f\x01\xa0\xa3\xe6\xff}\x89\xfe\x19D\x0e\x9c\xf3\x06W\x01DQn\n&\x01t\x02\xe8\x02\xa0UR\xaa\x92:\x00\xc4\xc7\xc6\xc6N\x0e\r\r\xfdC\x7f\x7f\x7f\x97T\x9e\xd1\xc1\xce\xd5.\xa8\xf6\xde---e\x1e<x\xf0hvvv\x05e\xc3\xe6P\x96\xf9\'\xd8L\x82\'\xa8\x11\x80\xb5\x06l\x08\xf4\xff\x80\xf8B\xe6\xa5\x9f\xb5\x02\xe2\xa8\x91 \xe5J\x1d\x00\x12(\x13\x85\x05\x8cn\xc5hu\xb8\x9b\xc8\xacy\\\xac\xa4\r\xd4\x08\xc0d\x9f\xf9\xfd\xedj\xbf0\x02\x88\x1e\rd7JP\xce0\x1f\x13\xb0\xff\x19\x8bY!\x14P&I\x04\xb5\xd6\x03\x7fL+\x93\x80\xef\xb9c\xf7m\xa1l\xd0"\xca\x06f\n\xc0\'\x16\xf4\xb9\x9b}\xc2\x07\x83\xfc\x1a\x0e\xde.Ha\x05a\xa1,k\x05\xd4\x14 \x82Z\x8b\x81W\x80V\'\x00\xaf\x00\x16*\x9dc\xa8\x91\x9f\x91 \xcf\xa5"\xca\xe5\xc3G\xfd\xc2\xc7\x01\x00\x7f\x08\xc02\xc9H\xc0\xd7~\xbe\x10\x18\x01r(\xc7\x0b\x8c\x00\xac\xe5 \xb9\x8eoE\xf0e\xc1\xf7\x90\x1a\x95T\xa8\xa4"\xf7\xb9S\xef\x9fp\xf89!\x04\xa8\x19\x9c\xaf\x05n\x19\x8c\xa2\xe6\xffY\xed\xe7\xe3\x80V\x87{\x80\x8cU\x00V\tXb\xa4`\xb5\xdew\xe3\x03\xfe\xce\tt+A=\x02\x94\xc0u\x14\xa1\xd6_\xd0\x0e\xf2\xcfPo\x9c\xc4\xc2fw`r\xbf\xb9%\xdf\xd7q\xf4f\x140\xbb\x06_\xb3\x99\xb1y\xa3\xb3\xd4N-\x00\x06\xb7\x02\xf2\x01!\xbf\xcd+\x05;\xceW4\xab\x90\xf9\x88\xbe^Oa=\xd9o\x17\x12\xf0\xb5\x98\xd5jF\x84z\x93=\x84\x8f\xf9\xef\x84f\x17\xb0\xdb\xb8|\xe7\x11\xd0>\xcd?7\xdc\x04\xe0\xb7\x19!\xdc\xfb6\x05\x07Y\xc8\xa4\xcev;\x1a\x9f\xc1]\xb3\xddS\xe8B\x84\x08\x11"D\x88\x10!B\x84\x08\x11\xa2\t\xf8;\x1e\xe2N\xc0\x01\x90M\x97\x00\x00\x00\x00IEND\xaeB`\x82'
            return img
        elif command == 'plotload':
            return "1:33.21"
        return "UNKNOWN"


    @Override(Experiment)
    @logged("info")
    def do_send_file_to_device(self, content, file_info):
        """
        Callback for when the client sends a file to the experiment
        server.
        """
        if self.DEBUG:
            print "[Archimedes] do_send_file_to_device called"
        return "ok"


    @Override(Experiment)
    @logged("info")
    def do_dispose(self):
        """
        Callback to perform cleaning after the experiment ends.
        """
        if self.DEBUG:
            print "[Archimedes] do_dispose called"

        return json.dumps({ Coordinator.FINISH_FINISHED_MESSAGE : True, Coordinator.FINISH_DATA_MESSAGE : "You're kicked out"})


