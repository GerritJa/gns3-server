#!/usr/bin/env python
#
# Copyright (C) 2016 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import sys
import json
import asyncio
import aiohttp

from ..config import Config
from .project import Project
from .compute import Compute
from ..version import __version__

import logging
log = logging.getLogger(__name__)


class Controller:
    """The controller is responsible to manage one or more compute servers"""

    def __init__(self):
        self._computes = {}
        self._projects = {}

        if sys.platform.startswith("win"):
            config_path = os.path.join(os.path.expandvars("%APPDATA%"), "GNS3")
        else:
            config_path = os.path.join(os.path.expanduser("~"), ".config", "GNS3")
        self._config_file = os.path.join(config_path, "gns3_controller.conf")

    def save(self):
        """
        Save the controller configuration on disk
        """
        data = {"computes": [{"host": c.host,
                              "port": c.port,
                              "protocol": c.protocol,
                              "user": c.user,
                              "password": c.password,
                              "compute_id": c.id
                              } for c in self._computes.values()],
                "version": __version__}
        os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
        with open(self._config_file, 'w+') as f:
            json.dump(data, f, indent=4)

    @asyncio.coroutine
    def load(self):
        """
        Reload the controller configuration from disk
        """
        if not os.path.exists(self._config_file):
            return
        try:
            with open(self._config_file) as f:
                data = json.load(f)
        except OSError as e:
            log.critical("Cannot load %s: %s", self._config_file, str(e))
            return
        for c in data["computes"]:
            compute_id = c.pop("compute_id")
            yield from self.add_compute(compute_id, **c)

    def is_enabled(self):
        """
        :returns: whether the current instance is the controller
        of our GNS3 infrastructure.
        """
        return Config.instance().get_section_config("Server").getboolean("controller")

    @asyncio.coroutine
    def add_compute(self, compute_id, **kwargs):
        """
        Add a server to the dictionary of compute servers controlled by this controller

        :param compute_id: Compute server identifier
        :param kwargs: See the documentation of Compute
        """

        # We disallow to create from the outside the
        if compute_id == 'local':
            return self._create_local_compute()

        if compute_id not in self._computes:
            compute_server = Compute(compute_id=compute_id, controller=self, **kwargs)
            self._computes[compute_id] = compute_server
            self.save()
        return self._computes[compute_id]

    def _create_local_compute(self):
        """
        Create the local compute node. It is the controller itself.
        """
        server_config = Config.instance().get_section_config("Server")
        self._computes["local"] = Compute(compute_id="local",
                                          controller=self,
                                          protocol=server_config.get("protocol", "http"),
                                          host=server_config.get("host", "localhost"),
                                          port=server_config.getint("port", 3080),
                                          user=server_config.get("user", ""),
                                          password=server_config.get("password", ""))
        return self._computes["local"]

    @property
    def computes(self):
        """
        :returns: The dictionary of compute server managed by this controller
        """
        return self._computes

    def get_compute(self, compute_id):
        """
        Returns a compute server or raise a 404 error.
        """
        try:
            return self._computes[compute_id]
        except KeyError:
            if compute_id == "local":
                return self._create_local_compute()
            raise aiohttp.web.HTTPNotFound(text="Compute ID {} doesn't exist".format(compute_id))

    @asyncio.coroutine
    def add_project(self, project_id=None, **kwargs):
        """
        Creates a project or returns an existing project

        :param kwargs: See the documentation of Project
        """
        if project_id not in self._projects:
            project = Project(project_id=project_id, **kwargs)
            self._projects[project.id] = project
            for compute_server in self._computes.values():
                yield from project.add_compute(compute_server)
            return self._projects[project.id]
        return self._projects[project_id]

    def get_project(self, project_id):
        """
        Returns a compute server or raise a 404 error.
        """
        try:
            return self._projects[project_id]
        except KeyError:
            raise aiohttp.web.HTTPNotFound(text="Project ID {} doesn't exist".format(project_id))

    def remove_project(self, project):
        del self._projects[project.id]

    @property
    def projects(self):
        """
        :returns: The dictionary of computes managed by GNS3
        """
        return self._projects

    @staticmethod
    def instance():
        """
        Singleton to return only on instance of Controller.

        :returns: instance of Controller
        """

        if not hasattr(Controller, '_instance') or Controller._instance is None:
            Controller._instance = Controller()
        return Controller._instance

    def emit(self, action, event, **kwargs):
        """
        Send a notification to clients scoped by projects
        """

        if "project_id" in kwargs:
            try:
                project_id = kwargs.pop("project_id")
                self._projects[project_id].emit(action, event, **kwargs)
            except KeyError:
                pass
        else:
            for project_instance in self._projects.values():
                project_instance.emit(action, event, **kwargs)