#!/usr/bin/env python
#-*-*- encoding: utf-8 -*-*-
#
# Copyright (C) 2013 onwards University of Deusto
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#
# This software consists of contributions made by many individuals,
# listed below:
#
# Author: Pablo Orduña <pablo@ordunya.com>
#

from weblab.login.comm.webs import PLUGINS

class LoginApp(object):

    def __init__(self, cfg_manager, server):
        self.cfg_manager = cfg_manager
        self.server      = server


    def __call__(self, environ, start_response):

        TOKEN = 'login/web'

        path = environ['PATH_INFO']
        relative_path = path[path.find(TOKEN) + len(TOKEN):]

        for PluginClass in PLUGINS:
            if relative_path.startswith(PluginClass.path or 'url.not.provided'):
                plugin = PluginClass(self.cfg_manager, self.server, environ)
                return plugin(environ, start_response)

        # Otherwise
        start_response("404 Not Found", [('Content-Type','text/plain')])
        return ["No plug-in registered for that path."]
