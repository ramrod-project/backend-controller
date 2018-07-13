from json import dump
from multiprocessing import Process
from os import environ, remove
from requests import ReadTimeout
from time import sleep, time

import brain
import docker
from pytest import fixture, raises

from ..controller import *
from .. import server
from plugin_controller.test.test_controller import brain_conn, clear_dbs, controller, env, rethink

CLIENT = docker.from_env()


@fixture(scope="function")
def server_proc():
    """Give a server Process

    Returns:
        {multiprocessing.Process} -- controller's main loop,
        not started yet.
    """
    server.RETHINK_HOST = "localhost"
    server.PLUGIN_CONTROLLER.rethink_host = "localhost"
    server.PLUGIN_CONTROLLER.network_name = "test"
    server.MANIFEST_FILE = "./test-manifest.json"
    server.PLUGIN_CONTROLLER.tag = environ["TRAVIS_BRANCH"]
    proc = Process(target=server.main)
    yield proc
    try:
        proc.terminate()
    except Exception as ex:
        print(ex)

@fixture(scope="module")
def give_manifest():
    """Give a plugin manifest
    """
    with open("./test-manifest.json", "w") as outfile:
        dump([{"Name": "Harness"}, {"Name": "Harness2"}], outfile)
    yield
    remove("./test-manifest.json")

@fixture(scope="function", autouse=True)
def clean_up_containers():
    yield
    for con in server.PLUGIN_CONTROLLER.get_all_containers():
        try:
            con.stop(timeout=1)
        except:
            pass
        try:
            con.remove(force=True)
        except:
            pass

def test_make_available(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test automatic creation of plugin entry
    in the Controller.Plugins table.
    """
    server_proc.start()
    sleep(3)
    result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
    assert result["Name"] == "Harness"
    assert result["State"] == "Available"
    assert result["DesiredState"] == ""
    assert result["Interface"] == ""
    assert result["ExternalPort"] == []
    assert result["InternalPort"] == []
    result = brain.queries.get_plugin_by_name_controller("Harness2", conn=brain_conn).next()
    assert result["Name"] == "Harness2"
    assert result["State"] == "Available"
    assert result["DesiredState"] == ""
    assert result["Interface"] == ""
    assert result["ExternalPort"] == []
    assert result["InternalPort"] == []
    result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
    assert result["Name"] == "AuxiliaryServices"
    assert result["State"] == "Available"
    assert result["DesiredState"] == ""
    assert result["Interface"] == ""
    assert result["ExternalPort"] == ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
    assert result["InternalPort"] == ["20/tcp", "21/tcp", "80/tcp", "53/udp"]

def test_available_to_start(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test starting a plugin which is already in
    the database as 'Available'.
    """
    server_proc.start()
    sleep(3)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Activate",
            "ExternalPort": ["".join((str(5000), "/tcp"))],
            "InternalPort": ["".join((str(5000), "/tcp"))]
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    db_updated = False
    now = time()
    while time() - now < 3:
        sleep(0.5)
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Active" and result["DesiredState"] == "":
            db_updated = True
            break
    assert db_updated
    # check ports

def test_active_to_stop(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test stopping a running plugin.
    """
    server_proc.start()
    sleep(3)
    server.PLUGIN_CONTROLLER.launch_plugin({
        "Name": "Harness",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Stop"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    exited = False
    now = time()
    while time() - now < 15:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if con.status == "exited":
            exited = True
            break
    assert exited
    db_updated = False
    now = time()
    while time() - now < 3:
        sleep(0.5)
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Stopped" and result["DesiredState"] == "":
            db_updated = True
            break
    assert db_updated

def test_active_to_restart(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test restarting an active plugin.
    """
    server_proc.start()
    sleep(3)
    con = server.PLUGIN_CONTROLLER.launch_plugin({
        "Name": "Harness",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Restart"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    db_updated = False
    now = time()
    while time() - now < 20:
        sleep(0.1)
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Restarting":
            db_updated = True
            break
    assert db_updated
    db_updated2 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Active" and result["DesiredState"] == "":
            db_updated2 = True
            break
        sleep(0.5)
    assert db_updated2
    con.stop(timeout=1)
    con.remove(force=True)

def test_stopped_to_activate(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test starting a stopped plugin.
    """
    server_proc.start()
    sleep(3)
    con = server.PLUGIN_CONTROLLER.launch_plugin({
        "Name": "Harness",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    con.stop(timeout=5)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "State": "Stopped",
            "DesiredState": ""
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    exited = False
    now = time()
    while time() - now < 15:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if con.status == "exited":
            exited = True
            break
    assert exited
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Activate"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    started= False
    now = time()
    while time() - now < 15:
        con = CLIENT.containers.get("Harness")
        if not con:
            continue
        if con.status == "running":
            started = True
            break
    assert started
    db_updated = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Active" and result["DesiredState"] == "":
            db_updated = True
            break
        sleep(0.5)
    assert db_updated

def test_invalid_transitions(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test invalid state transitions.
    """
    server_proc.start()
    sleep(3)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Stopped"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    sleep(3)
    db_not_updated = False
    now = time()
    result = None
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Available" and result["DesiredState"] == "":
            db_not_updated = True
            break
        sleep(0.5)
    assert db_not_updated
    sleep(1)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Restart"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    sleep(3)
    db_not_updated2 = False
    now = time()
    result = None
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Available" and result["DesiredState"] == "":
            db_not_updated2 = True
            break
        sleep(0.5)
    assert db_not_updated2
    con = server.PLUGIN_CONTROLLER.launch_plugin({
        "Name": "Harness",
        "DesiredState": "",
        "Interface": "",
        "ExternalPort": ["".join((str(5000), "/tcp"))],
        "InternalPort": ["".join((str(5000), "/tcp"))]
    })
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Activate"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    sleep(3)
    db_not_updated3 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Active" and result["DesiredState"] == "":
            db_not_updated3 = True
            break
        sleep(0.5)
    assert db_not_updated3
    sleep(1)
    con.stop(timeout=5)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "State": "Stopped",
            "DesiredState": ""
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    exited = False
    now = time()
    while time() - now < 15:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("Harness")
        if con.status == "exited":
            exited = True
            break
    assert exited
    db_updated = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Stopped" and result["DesiredState"] == "":
            db_updated = True
            break
        sleep(0.5)
    assert db_updated
    sleep(1)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "Harness",
            "DesiredState": "Stop"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    sleep(3)
    db_not_updated4 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("Harness", conn=brain_conn).next()
        if result["Name"] == "Harness" and result["State"] == "Stopped" and result["DesiredState"] == "":
            db_not_updated4 = True
            break
        sleep(0.5)
    assert db_not_updated4
    sleep(1)

def test_auxiliary(brain_conn, clear_dbs, env, rethink, server_proc, give_manifest, clean_up_containers):
    """Test stopping a running plugin.
    """
    server_proc.start()
    sleep(3)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "AuxiliaryServices",
            "DesiredState": "Activate",
            "ExternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"],
            "InternalPort": ["20/tcp", "21/tcp", "80/tcp", "53/udp"]
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    running = False
    now = time()
    while time() - now < 10:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("AuxiliaryServices")
        if not con:
            continue
        if con.status == "running":
            running = True
            break
    assert running
    db_updated = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["State"] == "Active" and result["DesiredState"] == "":
            db_updated = True
            break
        sleep(0.5)
    assert db_updated
    sleep(1)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "AuxiliaryServices",
            "DesiredState": "Restart"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    db_updated_desired = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["DesiredState"] == "Restart":
            db_updated_desired = True
            break
        sleep(0.1)
    assert db_updated_desired
    db_updated2 = False
    now = time()
    while time() - now < 20:
        sleep(0.1)
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["State"] == "Restarting":
            db_updated2 = True
            break
    assert db_updated2
    db_updated3 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["State"] == "Active" and result["DesiredState"] == "":
            db_updated3 = True
            break
        sleep(0.5)
    assert db_updated3
    sleep(1)
    result = brain.queries.update_plugin_controller(
        {
            "Name": "AuxiliaryServices",
            "DesiredState": "Stop"
        },
        conn=brain_conn
    )
    assert result["errors"] == 0
    db_updated_desired2 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["DesiredState"] == "Stop":
            db_updated_desired2 = True
            break
        sleep(0.1)
    assert db_updated_desired2
    exited = False
    now = time()
    while time() - now < 15:
        sleep(0.5)
        con = server.PLUGIN_CONTROLLER.get_container_from_name("AuxiliaryServices")
        if con.status == "exited":
            exited = True
            break
    assert exited
    db_updated4 = False
    now = time()
    while time() - now < 12:
        result = brain.queries.get_plugin_by_name_controller("AuxiliaryServices", conn=brain_conn).next()
        if result["Name"] == "AuxiliaryServices" and result["State"] == "Stopped" and result["DesiredState"] == "":
            db_updated4 = True
            break
        sleep(0.5)
    assert db_updated4
    sleep(1)
