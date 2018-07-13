"""Controller

This is the controller module.

TODO:
- Maintain local mapping of plugin names to containers.
- Use local mapping as cache and update as needed
"""
from json import load
import logging
from os import environ, getenv
import re
from time import asctime, gmtime, sleep, time

import brain
import docker
from requests import exceptions


logging.basicConfig(
    filename="logfile",
    filemode="a",
    format='%(date)s %(name)-12s %(levelname)-8s %(message)s'
)


CLIENT = docker.from_env()

PLUGIN_IMAGE = "ramrodpcp/interpreter-plugin:"
AUX_SERVICES_IMAGE = "ramrodpcp/auxiliary-services:"
AUX_SERVICES_NAME = "AuxiliaryServices"
AUX_PLUGIN = {
    "Name": AUX_SERVICES_NAME,
    "State": "Available",
    "DesiredState": "",
    "Interface": "",
    "ExternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"],
    "InternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
}

CONTAINERS_EXCEPTED = [
    "database",
    "backend",
    "websockets",
    "frontend",
    "rethinkdb",
    "controller"
]

LOGLEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}


class Controller():
    """This class is used to control plugin containers.

    Raises:
        TypeError -- if the protocol given for the port mapping
        is not 'TCP' or 'UDP'.
        ValueError -- if the port given for the port mapping
        is not a valid port number.
        docker.errors.ContainerError -- if a conainer can't be
        found.
    """


    def __init__(self, network_name, tag):
        self.logger = logging.getLogger("controller")
        self.logger.setLevel(environ.get("LOGLEVEL", "DEBUG"))
        self.logger.addHandler(logging.StreamHandler())
        self.container_mapping = {}
        self.network_name = network_name
        self.tag = tag
        self.rethink_host = "rethinkdb"
        if getenv("STAGE", "PROD") == "TESTING":
            self.rethink_host = "localhost"

    def _check_db_errors(self, response):
        """Check responses from database for errors

        Checks responses from rethinkdb for errors and
        logs them as appropriate. Returns True for no
        errors.

        Arguments:
            response {dict} -- rethinkdb operation response.

        Returns:
            {bool} -- True - no errors, False - errors
        """
        if response["errors"] > 0:
            self.log(
                40,
                response["first_error"]
            )
            return False
        return True

    def load_plugins_from_manifest(self, manifest):
        """Load plugins into db from manifest.json

        Also loads in the auxiliary services container
        (if available).

        Arguments:
            manifest {str} -- filename for manifest.

        Returns:
            {bool} -- True - succeeded, False - failed.
        """
        manifest_loaded = []
        with open(manifest, "r") as readfile:
            manifest_loaded = load(readfile)
        if len(manifest_loaded) == 0:
            return self._check_db_errors({
                "errors": 1,
                "first_error": "No plugins found in manifest!"
            })
        try:
            CLIENT.images.get("".join([
                AUX_SERVICES_IMAGE,
                self.tag
            ]))
            if not self.create_plugin(AUX_PLUGIN):
                return False
        except docker.errors.ImageNotFound:
            self._check_db_errors({
                "errors": 1,
                "first_error": "Auxiliary plugin not available!"
            })
        for plugin in manifest_loaded:
            if not self.create_plugin({
                "Name": plugin["Name"],
                "State": "Available",
                "DesiredState": "",
                "Interface": "",
                "ExternalPort": [],
                "InternalPort": []
            }):
                return False
        return True

    def create_plugin(self, plugin_data):
        """Creates a plugin in the 'Plugins' table of
        the 'Controller' database. plugin_data should contain
        the following:

        {
            Name<str>,
            State<str> [default: "Available"],
            DesiredState<str> [default: ""],
            Interface<str>(IP) [default: ""],
            ExternalPort<list<str> >,
            InternalPort<list<str> >
        }

        Arguments:
            plugin_data {dict} -- data for plugin
        """
        if plugin_data["Name"] == "":
            return self._check_db_errors({
                "errors": 1,
                "first_error": "Plugin must be given a valid name!"
            })
        result = brain.queries.create_plugin_controller(
            plugin_data,
            conn=brain.connect(host=self.rethink_host)
        )
        return self._check_db_errors(result)

    def _create_port(self, port_data):
        """Creates a port entry in the 'Ports' table of
        the 'Controller' database. port_data should contain
        the following:

        {
            InterfaceName<str> [default: "All"],
            Address<str(IP) [default: ""],
            TCPPorts<list<str> >,
            UDPPorts<list<str> >
        }

        Arguments:
            port_data {dict} -- data for port
        """
        result = brain.queries.create_port_controller(
            port_data,
            conn=brain.connect(host=self.rethink_host)
        )
        return self._check_db_errors(result)

    def update_plugin(self, plugin_data):
        """Updates the plugin info to match the current
        state of its container.

        Takes plugin data and current state.

        Arguments:
            plugin_data {dict} -- data for plugin.
        """
        result = brain.queries.update_plugin_controller(
            plugin_data,
            conn=brain.connect(host=self.rethink_host)
        )
        self.container_mapping[plugin_data["Name"]] = \
            self.get_container_from_name(plugin_data["Name"], timeout=3)
        return self._check_db_errors(result)

    def dev_db(self):
        """Spins up db for dev environment

        When operating in a dev environment ("STAGE"
        environment variable is "DEV")

        Arguments:
            port_mapping {dict} -- a mapping for keeping
            track of used {host port: container}
            combinations.
        """
        if self.get_container_from_name("rethinkdb", timeout=3):
            self.log(20, "Found rethinkdb container!")
            return True
        try:
            self.container_mapping["rethinkdb"] = CLIENT.containers.run(
                "".join(("ramrodpcp/database-brain:", self.tag)),
                name="rethinkdb",
                detach=True,
                ports={"28015/tcp": 28015},
                network=self.network_name,
                remove=False
            )
        except brain.r.ReqlError as ex:
            self.log(50, "Could not start db!: {}".format(ex))
            return False
        sleep(3)
        return True

    def log(self, level, message):
        """Log a message

        Arguments:
            level {int} -- 10,20,30,40,50 are valid
            log levels.
            message {str} -- a string message to log.
        """
        self.logger.log(
            level,
            message,
            extra={
                'date': asctime(gmtime(time()))
            }
        )

    def launch_plugin(self, plugin_data):
        """Launch a plugin container

        Arguments:
            plugin_data {dict} -- data for plugin.

        Returns:
            {Container} -- a Container object corresponding
            to the launched container.
        """
        port_data = {
            "InterfaceName": "",
            "Address": plugin_data["Interface"],
            "TCPPorts": [],
            "UDPPorts": []
        }

        for i in range(len(plugin_data["ExternalPort"])):
            proto = plugin_data["ExternalPort"][i].split("/")[-1]
            ext_port = plugin_data["ExternalPort"][i].split("/")[0]
            if proto == "tcp":
                port_data["TCPPorts"].append(ext_port)
            else:
                port_data["UDPPorts"].append(ext_port)

        existing = self.get_container_from_name(plugin_data["Name"], timeout=2)
        if existing:
            if self.restart_plugin(plugin_data):
                return existing
            return None

        self._create_port(port_data)

        # ---Right now only one port mapping per plugin is supported---
        # ---hence the internal_ports[0].                           ---
        return self.run_container(plugin_data)

    def _get_ports_config(self, plugin_data):
        ports_config = {}
        for i in range(len(plugin_data["ExternalPort"])):
            ext_port_proto = plugin_data["ExternalPort"][i]
            int_port = plugin_data["InternalPort"][i].split("/")[0]
            ports_config[ext_port_proto] = int_port
        return ports_config

    def run_container(self, plugin_data):
        """Runs a container given parameters

        Arguments:
            plugin_data {dict} -- data for plugin.

        Returns:
            {container} -- a docker container object.
        """
        image_name = ""
        environment = {
            "STAGE": environ["STAGE"],
            "LOGLEVEL": environ["LOGLEVEL"]
        }
        volumes = {}
        ports_config = self._get_ports_config(plugin_data)
        # ---Right now only one port mapping per plugin is supported---
        # ---hence the internal_ports[0].                           ---
        if plugin_data["Name"] == AUX_SERVICES_NAME:
            image_name = AUX_SERVICES_IMAGE
            try:
                CLIENT.volumes.get("brain-volume")
                volumes = {
                    "brain-volume": {"bind": "/www/files/brain", "mode": "ro"}
                }
            except docker.errors.NotFound:
                self.log(
                    30,
                    "brain-volume not found! Files in \
                    brain will not be available."
                )
        else:
            image_name = PLUGIN_IMAGE
            environment["PLUGIN"] = plugin_data["Name"]
            environment["PORT"] = plugin_data["InternalPort"][0].split("/")[0]
        con = CLIENT.containers.run(
            "".join((image_name, self.tag)),
            name=plugin_data["Name"],
            environment=environment,
            detach=True,
            network=self.network_name,
            ports=ports_config,
            volumes=volumes
        )
        if self.wait_for_plugin(plugin_data):
            return con
        return None

    def wait_for_plugin(self, plugin_data, timeout=10):
        """Wait for container to start

        Arguments:
            plugin_data {dict} -- plugin data
            timeout {int} -- timeout ot wait for container

        Raises:
            docker.errors.ContainerError -- if container
            not found, raise error.

        Returns:
            {bool} -- True - container running,
            False - container not running after 10 seconds
        """
        now = time()
        status = None
        while time() - now < timeout:
            con = self.get_container_from_name(plugin_data["Name"])
            if not con:
                sleep(0.5)
                continue
            status = con.status
            if status == "running":
                plugin_data["State"] = "Active"
                plugin_data["DesiredState"] = ""
                self.update_plugin(plugin_data)
                self.container_mapping[plugin_data["Name"]] = con
                self.log(
                    20,
                    "{} is running!".format(plugin_data["Name"])
                )
                return True
            sleep(1)
        return False

    def restart_plugin(self, plugin_data):
        """Restart a plugin by name.

        Arguments:
            plugin_data {dict} -- plugin data

        Returns:
            {bool} -- True: restarted False: not restarted
        """
        con = self.get_container_from_name(plugin_data["Name"], timeout=3)
        if not con:
            return False
        plugin_data["State"] = "Restarting"
        self.update_plugin(plugin_data)
        self.log(
            20,
            "Restarting {}...".format(plugin_data["Name"])
        )
        con.restart(timeout=10)
        return self.wait_for_plugin(plugin_data)

    def stop_plugin(self, plugin_data):
        """Stop a plugin by name.

        Arguments:
            plugin_data {dict} -- plugin data

        Returns:
            {bool} -- True: stopped False: not found
        """
        con = self.get_container_from_name(plugin_data["Name"], timeout=3)
        if con:
            self.log(
                20,
                "Stopping {}...".format(plugin_data["Name"])
            )
            con.stop(timeout=10)
            plugin_data["State"] = "Stopped"
            self.update_plugin(plugin_data)
            return True
        return False

    def plugin_status(self, plugin_data):
        """Return the status of a plugin container

        Arguments:
            plugin_data {dict} -- plugin data

        Returns:
            {str} -- "Active", "Restarting", or "Stopped"
        """
        cursor = brain.queries.get_plugin_by_name_controller(
            plugin_data["Name"],
            conn=brain.connect(host=self.rethink_host)
        )
        try:
            return cursor.next()["State"]
        except brain.r.ReqlCursorEmpty:
            self.log(
                30,
                "".join((plugin_data["Name"], " not found in database!"))
            )
        return None

    @staticmethod
    def get_all_containers():
        """Return all plugin containers

        Returns:
            containers {list} -- list of plugins
            containers.
        """
        pattern = re.compile('|'.join(
            [''.join(['.*?', c, '.*?']) for c in CONTAINERS_EXCEPTED]
        ))
        containers = []
        for con in CLIENT.containers.list(all=True):
            match = pattern.match(con.name)
            if not match:
                containers.append(con)
        return containers

    def stop_all_containers(self):
        """Clean up containers and network

        Stops all running containers and prunes
        the network (in dev environment).

        Arguments:
            containers {list} -- a list of all the containers
            ran by the container.
        """
        self.log(
            20,
            "Kill signal received, stopping container(s)..."
        )
        for container in self.get_all_containers():
            try:
                container.stop(timeout=5)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        if environ["STAGE"] == "DEV":  # pragma: no cover
            try:
                rdb = CLIENT.containers.get("rethinkdb")
                rdb.stop(timeout=10)
                rdb.remove(force=True)
            except docker.errors.NotFound:
                pass
            self.log(
                20,
                "Pruning networks..."
            )
            CLIENT.networks.prune()

    def get_container_from_name(self, plugin_name, timeout=1):
        """Return a container object given a plugin name.

        First attempts to reload the attributes of an existing
        container in the container_mapping, then attempts
        to get a new container from the CLIENT.

        Arguments:
            plugin_name {str} -- name of a plugin.

        Returns:
            con {container} -- a docker.container object corresponding
            to the plugin name.
        """
        # --- *Try* to get a new container...this is very   ---
        # --- unreliable/unpredictable so if there are any  ---
        # --- errors, we just return None (and don't update ---
        # --- the status for now)                           ---
        now = time()
        while time() - now < timeout:
            try:
                return CLIENT.containers.get(plugin_name)
            except:
                pass
            sleep(0.05)
        return None
