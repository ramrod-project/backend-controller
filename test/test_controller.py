from json import dump, load
import logging
from os import environ, getenv, path, remove
from sys import path as syspath, stderr
from threading import Thread
from time import sleep, time

import brain
import docker
from pytest import fixture, raises

CONTROLLER_PATH = path.join(
    "/".join(path.abspath(__file__).split("/")[:-2])
)
CLIENT = docker.from_env()

syspath.append(CONTROLLER_PATH)

from ..controller import *
from .. import server


AUX_PLUGIN = {
    "Name": "AuxiliaryServices",
    "State": "Available",
    "DesiredState": "",
    "Interface": "",
    "ExternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"],
    "InternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
}

TEST_PLUGIN_DATA = {
    "Name": "testcontainer",
    "State": "Available",
    "DesiredState": "",
    "Interface": "",
    "ExternalPorts": ["5000"],
    "InternalPorts": ["5000"]
}

TEST_PLUGIN_DATA2 = {
    "Name": "testcontainer2",
    "State": "Available",
    "DesiredState": "",
    "Interface": "",
    "ExternalPorts": ["5000"],
    "InternalPorts": ["5000"]
}

TEST_PORT_DATA = {
    "InterfaceName": "eth0",
    "Address": "192.168.1.1",
    "TCPPorts": ["5000"],
    "UDPPorts": ["3000"]
}

TEST_PORT_DATA2 = {
    "InterfaceName": "eth0",
    "Address": "192.168.1.1",
    "TCPPorts": ["6000", "7000"],
    "UDPPorts": ["8000"]
}

TEST_MANIFEST = [
    {
        "Name": "Plugin1"
    },
    {
        "Name": "Plugin2"
    },
    {
        "Name": "Plugin3"
    },
    {
        "Name": "Plugin4"
    }
]


def docker_net_create():
    try:
        CLIENT.networks.create("test")
    except:
        pass

def docker_net_remove():
    CLIENT.networks.prune()

def give_a_container(image="alpine:3.7",
                     name="testcontainer",
                     command="sleep 30",
                     ports={},
                     detach=True,
                     network="test"):
    return CLIENT.containers.run(
        image,
        name=name,
        command=command,
        detach=detach,
        network=network,
        ports=ports
    )

@fixture(scope="function", autouse=True)
def clean_up_containers():
    yield
    for con in server.PLUGIN_CONTROLLER.get_all_containers():
        try:
            con.stop(timeout=1)
        except:
            pass
        try:
            con.remove()
        except:
            pass

@fixture(scope="module")
def env():
    """Set the environment variables for module tests
    """
    environ["STAGE"] = "TESTING"
    environ["LOGLEVEL"] = "DEBUG"
    try:
        if environ["TRAVIS_BRANCH"] not in ["dev", "qa", "master"]:
            stderr.write("TRAVIS_BRANCH must be set to dev|qa|master!\n")
    except KeyError:
        environ["TRAVIS_BRANCH"] = "dev"
    yield
    environ["STAGE"] = ""
    environ["LOGLEVEL"] = ""

@fixture(scope="function")
def clear_dbs():
    conn = brain.connect()
    yield
    brain.r.db("Controller").table("Plugins").delete().run(conn)
    brain.r.db("Controller").table("Ports").delete().run(conn)
    sleep(1)

@fixture(scope="function")
def plugin_monitor():
    mon = brain.r.db("Controller").table("Plugins").filter(
        {"Name": "testcontainer"}
    ).changes().run(brain.r.connect())
    return mon

@fixture(scope="module")
def rethink():
    # Setup for all module tests
    docker_net_create()
    tag = getenv("TRAVIS_BRANCH", "dev").replace("master", "latest")
    con = give_a_container(
        image="".join(("ramrodpcp/database-brain:", tag)),
        name="rethinkdb",
        ports={"28015/tcp": 28015}
    )
    yield con
    try:
        con.stop()
    except:
        pass
    try:
        con.remove()
    except:
        pass
    # Teardown for all module tests
    docker_net_remove()

@fixture(scope="function")
def container():
    """Give a container to a test
    """
    con = give_a_container()
    yield con
    try:
        con.stop()
    except:
        pass
    try:
        con.remove()
    except:
        pass

@fixture(scope="function")
def controller():
    """Give a Controller
    """
    cont = Controller("test", getenv("TRAVIS_BRANCH", "dev").replace("master", "latest"))
    cont.rethink_host = "localhost"
    return cont

@fixture(scope="function")
def brain_conn():
    """Give a connection to the brain
    """
    return brain.connect()

def test_dev_db(env, controller):
    CLIENT.networks.create("test")
    controller.dev_db()
    assert controller.container_mapping["rethinkdb"], "Should contain test db"
    con = CLIENT.containers.get("rethinkdb")
    assert con.status == "running", "Rethinkdb container should be running!"
    assert "test" in [n.name for n in CLIENT.networks.list()], "dev_db should create 'test' network in DEV environment!"
    con.stop()
    con.wait(timeout=5)
    con.remove()
    CLIENT.networks.prune()

def test_get_all_containers(env, controller, clean_up_containers):
    containers = []
    for i in [1000, 1001, 1002]:
        containers.append(give_a_container(name=str(i), network=None))
    for con in containers:
        assert con
        assert con.status == "created"
    got_containers = controller.get_all_containers()
    for container in containers:
        assert container in got_containers
        container.stop()
        container.wait(timeout=2)
        container.remove()
    
def test_stop_all_containers(env, controller, clean_up_containers):
    containers = []
    for i in [1000, 1001, 1002]:
        containers.append(give_a_container(name=str(i), network=None))
    for con in containers:
        assert con
        assert con.status == "created"
    controller.stop_all_containers()
    for con in containers:
        with raises(docker.errors.NotFound):
            con = CLIENT.containers.get(con.id)

def test_create_plugin(env, controller, rethink, brain_conn, clear_dbs, clean_up_containers):
    assert not controller.create_plugin({"Name": ""})
    controller.create_plugin(TEST_PLUGIN_DATA)
    sleep(1)
    cursor = brain.r.db("Controller").table("Plugins").filter(
        {"Name": "testcontainer"}
    ).run(brain_conn)
    res = cursor.next()
    del res["id"]
    assert res == TEST_PLUGIN_DATA
    assert not controller.create_plugin(TEST_PLUGIN_DATA)

def test_load_plugins_from_manifest(env, controller, rethink, brain_conn, clear_dbs, clean_up_containers):
    with raises(FileNotFoundError):
        controller.load_plugins_from_manifest("./manifest.json")
    with open("./manifest.json", "w") as outfile:
        dump([], outfile)
    assert not controller.load_plugins_from_manifest("./manifest.json")
    with open("./manifest.json", "w") as outfile:
        dump(TEST_MANIFEST, outfile)
    assert controller.load_plugins_from_manifest("./manifest.json")
    db_updated = False
    now = time()
    while time() - now < 5:
        for plugin in TEST_MANIFEST:
            res = brain.r.db("Controller").table("Plugins").filter(
                {"Name": plugin["Name"]}
            ).run(brain_conn).next()
            del res["id"]
            if res != {
                "Name": plugin["Name"],
                "State": "Available",
                "DesiredState": "",
                "Interface": "",
                "InternalPort": [],
                "ExternalPort": []
            }:
                sleep(0.5)
                db_updated = False
                break
            else:
                db_updated = True
        if db_updated:
            break
    assert db_updated
    db_updated2 = False
    now = time()
    while time() - now < 5:
        sleep(0.5)
        res = brain.r.db("Controller").table("Plugins").filter(
            {"Name": "AuxiliaryServices"}
        ).run(brain_conn).next()
        del res["id"]
        if res == {
            "Name": "AuxiliaryServices",
            "State": "Available",
            "DesiredState": "",
            "Interface": "",
            "ExternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"],
            "InternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
        }:
            db_updated2 = True
            break
    assert db_updated2
    assert not controller.load_plugins_from_manifest("./manifest.json")
    remove("./manifest.json")

def test_create_port(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    assert controller._create_port(TEST_PORT_DATA)
    sleep(1)
    cursor = brain.r.db("Controller").table("Ports").filter(
        {"InterfaceName": "eth0"}
    ).run(brain_conn)
    res = cursor.next()
    del res["id"]
    assert res == TEST_PORT_DATA
    assert not controller._create_port(TEST_PORT_DATA)
    assert controller._create_port(TEST_PORT_DATA2)
    cursor = brain.r.db("Controller").table("Ports").filter(
        {"InterfaceName": "eth0"}
    ).run(brain_conn)
    res = cursor.next()
    udp_combined = TEST_PORT_DATA["UDPPorts"] + TEST_PORT_DATA2["UDPPorts"]
    for udp_port in udp_combined:
        assert udp_port in res["UDPPorts"]
    tcp_combined = TEST_PORT_DATA["TCPPorts"] + TEST_PORT_DATA2["TCPPorts"]
    for tcp_port in tcp_combined:
        assert tcp_port in res["TCPPorts"]

def test_update_plugin(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    res = brain.r.db("Controller").table("Plugins").insert(
        TEST_PLUGIN_DATA
    ).run(brain_conn)
    assert res["errors"] == 0
    sleep(1)
    TEST_PLUGIN_DATA["DesiredState"] = "Start"
    assert controller.update_plugin(TEST_PLUGIN_DATA)
    sleep(1)
    cursor = brain.r.db("Controller").table("Plugins").filter(
        {"Name": TEST_PLUGIN_DATA["Name"]}
    ).run(brain_conn)
    res = cursor.next()
    assert res["DesiredState"] == "Start"

def test_get_container_from_name(env, container, controller, rethink, clear_dbs, clean_up_containers):
    con = controller.get_container_from_name("testcontainer")
    assert container.id == con.id

def test_launch_container(env, controller, rethink, clear_dbs, clean_up_containers):
    con = controller.launch_plugin({
        "Name": "Harness",
        "State": "Available",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["6000/tcp"],
        "InternalPort": ["5005/tcp"]
    })
    sleep(3)
    con = CLIENT.containers.get(con.id)
    assert con.status == "running"
    assert con.name == "Harness"
    con.stop()
    con.remove()
    CLIENT.networks.prune()

def test_launch_auxiliary(env, controller, rethink, clear_dbs):
    con = controller.launch_plugin({
        "Name": "AuxiliaryServices",
        "State": "Available",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"],
        "InternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
    })
    sleep(3)
    con = CLIENT.containers.get(con.id)
    assert con.status == "running"
    assert con.name == "AuxiliaryServices"
    con.stop()
    con.remove()
    CLIENT.networks.prune()

def test_wait_plugin(env, container, controller, rethink, clear_dbs):
    container = CLIENT.containers.get(container.id)
    assert container.status == "running"
    container.stop()
    container.wait(timeout=3)
    assert not controller.wait_for_plugin({"Name": "testcontainer", "State": "Stopped"}, timeout=5)
    start_thread = Thread(target=container.start)
    start_thread.start()
    assert controller.wait_for_plugin({"Name": "testcontainer", "State": "Stopped"})


def test_restart_plugin(env, container, controller, rethink, clear_dbs, plugin_monitor, clean_up_containers):
    assert not controller.restart_plugin({"Name": "fake"})
    container = CLIENT.containers.get(container.id)
    assert container.status == "running"
    test_data = TEST_PLUGIN_DATA
    test_data["State"] = "Running"
    assert controller.restart_plugin(test_data)
    container.stop()
    assert controller.restart_plugin(test_data)
    container = CLIENT.containers.get(container.id)
    assert container.status == "running"


def test_plugin_status(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    assert not controller.plugin_status({"Name": "fake"})
    brain.r.db("Controller").table("Plugins").insert(
        TEST_PLUGIN_DATA2
    ).run(brain_conn)
    assert controller.plugin_status(TEST_PLUGIN_DATA2) == "Available"
    brain.r.db("Controller").table("Plugins").filter({"Name": "testcontainer2"}).update(
        {"State": "Running"}
    ).run(brain_conn)
    assert controller.plugin_status(TEST_PLUGIN_DATA2) == "Running"
    brain.r.db("Controller").table("Plugins").filter({"Name": "testcontainer2"}).update(
        {"State": "Stopped"}
    ).run(brain_conn)
    assert controller.plugin_status(TEST_PLUGIN_DATA2) == "Stopped"

def test_stop_container(env, container, controller, rethink, clean_up_containers):
    container = CLIENT.containers.get(container.id)
    assert container.status == "running"
    controller.stop_plugin({"Name": "testcontainer", "State": "Running"})
    sleep(1)
    con = CLIENT.containers.get("testcontainer")
    assert con.status == "exited"
    con.remove()

def test_update_states(env, rethink, clear_dbs, brain_conn, clean_up_containers):
    """Test updating the state of a container in
    the db.
    """
    server.PLUGIN_CONTROLLER.rethink_host = "localhost"
    containers = []
    for i in [1000, 1001, 1002]:
        name = str(i)
        containers.append(give_a_container(name=name))
        result = brain.queries.create_plugin_controller(
            {
                "Name": name,
                "State": "Available",
                "DesiredState": "",
                "Interface": "",
                "ExternalPort": ["".join((str(i + 5000), "/tcp"))],
                "InternalPort": ["".join((str(i + 5000), "/tcp"))]
            },
            conn=brain_conn
        )
        assert result["errors"] == 0
    for con in containers:
        assert con
        assert con.status == "created"
        server.PLUGIN_CONTROLLER.container_mapping[name] = con
    server.update_states()
    sleep(1)
    for plugin_name, plugin_con in server.PLUGIN_CONTROLLER.container_mapping.items():
        result = brain.queries.get_plugin_by_name_controller(plugin_name, conn=brain_conn).next()
        assert result["State"] == "Active"
        plugin_con.stop()
    server.update_states()
    sleep(1)
    for plugin_name, _ in server.PLUGIN_CONTROLLER.container_mapping.items():
        result = brain.queries.get_plugin_by_name_controller(plugin_name, conn=brain_conn).next()
        assert result["State"] == "Stopped"
        con.remove()
    server.PLUGIN_CONTROLLER.container_mapping = {}

def test_handle_state_change(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    """Test handling a state change of a container.
    """
    server.PLUGIN_CONTROLLER.rethink_host = "localhost"
    server.PLUGIN_CONTROLLER.network_name = "test"
    server.PLUGIN_CONTROLLER.tag = environ["TRAVIS_BRANCH"]
    # --- Start a plugin
    result = brain.queries.create_plugin_controller(
        {
            "Name": "Harness",
            "State": "Available",
            "DesiredState": "",
            "Interface": "",
            "ExternalPort": [],
            "InternalPort": []
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    old_stage = environ["STAGE"]
    environ["STAGE"] = "DEV"
    server.handle_state_change({
        "Name": "Harness",
        "State": "Available",
        "DesiredState": "Activate",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    environ["STAGE"] = old_stage
    sleep(3)
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"]
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].name == "Harness"
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].status == "running"
    result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
    assert result["State"] == "Active"
    assert result["DesiredState"] == ""
    server.handle_state_change({
        "Name": "Harness",
        "State": "Active",
        "DesiredState": "Stop",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    sleep(3)
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"]
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].status == "exited"
    result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
    assert result["State"] == "Stopped"
    assert result["DesiredState"] == ""
    server.PLUGIN_CONTROLLER.container_mapping["Harness"].remove()
    server.PLUGIN_CONTROLLER.container_mapping = {}

def test_check_states(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    """Test checking the states of the various containers
    and updating as necessary"""
    server.PLUGIN_CONTROLLER.rethink_host = "localhost"
    server.PLUGIN_CONTROLLER.network_name = "test"
    server.PLUGIN_CONTROLLER.tag = environ["TRAVIS_BRANCH"]
    result = brain.queries.create_plugin_controller(
        {
            "Name": "Harness",
            "State": "Available",
            "DesiredState": "Activate",
            "Interface": "",
            "ExternalPort": ["".join((str(5000), "/tcp"))],
            "InternalPort": ["".join((str(5000), "/tcp"))]
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    cursor = brain.queries.RPC.run(brain_conn)
    server.check_states(cursor)
    sleep(3)
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"]
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].name == "Harness"
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].status == "running"
    result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
    assert result["State"] == "Active"
    assert result["DesiredState"] == ""
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Stop",
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    cursor = brain.queries.RPC.run(brain_conn)
    server.check_states(cursor)
    sleep(3)
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"]
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].name == "Harness"
    assert server.PLUGIN_CONTROLLER.container_mapping["Harness"].status == "exited"
    result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
    assert result["State"] == "Stopped"
    assert result["DesiredState"] == ""

def test_aux_volume_mount(env, controller, rethink, clear_dbs, brain_conn, clean_up_containers):
    CLIENT.volumes.create(name="brain-volume")
    con = controller.launch_plugin(AUX_PLUGIN)
    now = time()
    running = False
    while time() - now < 4:
        con = CLIENT.containers.get(con.id)
        if con.name == "AuxiliaryServices" and con.status == "running":
            running = True
            break
        sleep(0.3)
    assert running
    con.stop()
    con.remove()
    CLIENT.networks.prune()
    CLIENT.volumes.prune()