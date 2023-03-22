#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import http.client
import socket
import json
import time
import os
from ipaddress import ip_network
from urllib.parse import urlparse

import requests
from requests_toolbelt.adapters.source import SourceAddressAdapter

import subprocess
from threading import Thread

import docker

class Manager(Thread):
    def __init__(self, core_manager_address, core_manager_port=443, ldap_host = None, src_address = None):
        Thread.__init__(self)

        self.core_manager_address = core_manager_address
        self.core_manager_port = core_manager_port
        self.src_address = src_address

        self.d = docker.from_env()
        self.expected_networks = {}
        self.expected_machines = {}

        self.s = requests.Session()
        self.s.mount('http://', SourceAddressAdapter(src_address))

        ldap_hostname = urlparse(ldap_host).hostname
        parts = ldap_hostname.split(".")
        parts = [part for part in parts]
        self.domain = ".".join(parts[1:])        

    def run(self):
        while True:
            self.ipa_get_join_hash() # TODO: Should be called inside compare_machines and only for the machines that accually being started
            self.compare_machines()
            time.sleep(2)

    def ipa_get_join_hash(self, machine_name = "openbox-latest-user1"):
        keytab_path = "/opt/keytabs/{}.{}/krb5.keytab".format(machine_name, self.domain) # TODO: Make it more dynamic
        if is_file(keytab_path):
            return

        # Check and load the keytab of the node
        machine_keytab_path = "/etc/krb5.keytab" # TODO: Make it more dynamic
        if not is_file(machine_keytab_path):
            return
        command = "kinit -k -t {}".format(machine_keytab_path)
        output = subprocess.run(command.split(" "), stdout=subprocess.PIPE, text=True)
        print(output) # TODO: write it to syslog instead

        # Get the keytab of the cubicle
        # TODO: Make it more dynamic
        command = "ipa-getkeytab -Y GSSAPI -r -p host/{}.{} -k {}".format(machine_name, self.domain, keytab_path) # -r is important, otherwise the keytab could be rewritten.
        output = subprocess.run(command.split(" "), stdout=subprocess.PIPE, text=True)
        print(output) # TODO: write it to syslog instead
        
    def connect(self, address, port, method, uri):
        res = None
        try:
            match method:
                case 'GET' | 'get':
                    res = self.s.get('http://{}:{}{}'.format(address, port, uri), timeout=2)
                case 'POST' | 'post':
                    # placeholder
                    print("[Manager] POST is still an unknown method for self.connect")
                case _:
                    print("[Manager] Unknown method were used in self.connect")
        except requests.exceptions.Timeout as e:
            print("[Manager] Connection timed out for http://{}:{}{}".format(address, port, uri))
            raise Exception(e)

        if res is None:
            return b''
        
        return res.content
    
    def is_docker_network_deployed(self, name):
        networks = [n.name for n in self.d.networks.list()]
    
        if name in networks:
            return True
        
        return False
    
    def create_docker_network(self, name):
        if self.is_docker_network_deployed(name):
            return True

        if name not in self.expected_networks:
            return False
            
        if 'range' not in self.expected_networks[name]:
            return False

        ip_range = ip_network(self.expected_networks[name]['range'])

        ipam_pool = docker.types.IPAMPool(
            subnet = str(ip_range),
            gateway = str(list(ip_range.hosts())[0]), # First adress in range
        )
   
        ipam_config = docker.types.IPAMConfig(
            pool_configs = [ipam_pool]
        )
        try:
            self.d.networks.create(
                name,
                driver = 'bridge',
                ipam = ipam_config,
            )
        except Exception as e:
            print("[Manager] Error: {}".format(e))
            return False
    
        return True
    
    def remove_docker_network(self, name):
        networks = self.d.networks.list()
        for network in networks:
            if network.name != name:
                continue
            network.reload() # https://github.com/docker/docker-py/issues/1775
            if len(network.containers) > 0:
                print("[Manager] Network still got active containers")
                return False
    
            try:
                self.d.networks.get(network.name).remove()
            except Exception as e:
                print("[Manager] Error: {}".format(e))
                return False
    
            return True
    
        # Default returns false since nothing have been changed.
        return False
    
    # TODO: Filter based on name and not all + loop + if-check. probably fine for now
    def is_docker_machine_deployed(self, name):
        for machine_name in self.get_docker_machines():
            if machine_name != name:
                continue
            return True
        return False
    
    def add_docker_machine(self, machine):
        name = machine['name']
        if not self.is_docker_machine_deployed(name):
            if not self.is_docker_network_deployed(machine['network']):
                self.create_docker_network(machine['network'])
            try:
                # TODO: check if homefolder actually exist. make som skel-copy too?
                # TODO: Maybe not trust 'name'?
                hostname = name + ".{}".format(self.domain)
                c = self.d.containers.create(
                    machine['image'],
                    restart_policy = {
                        'Name': 'always',
                    },
                    detach = True,
                    environment = {
                        'OWNER': machine['owner'],
                        'HASH': 'NOT IMPLEMENTED YET',
                    },
                    name = name,
                    hostname = hostname,
                    network = machine['network'],
                    volumes = {
                        '/opt/homefolders/' + machine['owner'] + '/': {
                            'bind': '/home/' + machine['owner'],
                            'mode': 'rw'
                        },
                        '/opt/keytabs/' + hostname + '/': {
                            'bind': '/opt/keytabs',
                            'mode': 'rw'
                        },
                    },
                    cap_add = ["sys_nice"],
                )
                c.start()
            except Exception as e:
                print("[Manager] Error: {}".format(e))
                return False
    
        return True
    
    # the variable machine is the original machine
    def add_docker_novnc_machine(self, machine):
        name = machine['name'] + '_novnc'
        if not self.is_docker_machine_deployed(name):
            if not self.is_docker_network_deployed(machine['network']):
                self.create_docker_network(machine['network'])

            try:
                if not isinstance(machine['novnc_port'], int):
                    try:
                        machine['novnc_port'] = int(machine['novnc_port'])
                    except ValueError:
                        print("[Manager] machine['novnc_port'] is not a integer")

                c = self.d.containers.create(
                    'divisora/novnc:latest', # TODO: make dynamic!
                    restart_policy = {
                        'Name': 'always',
                    },
                    detach = True,
                    environment = {
                        'VNC_SERVER': machine['name'] + ':5900', # TODO: make dynmic? 5900 = :0 default för VNC
                    },
                    name = machine['name'] + '_novnc',
                    network = machine['network'],
                    ports = {
                        '6080/tcp': machine['novnc_port'],  # TODO: make dynamic? 6080 = default for NoVNC
                    },
                )
                c.start()
            except Exception as e:
                print("[Manager] Error: {}".format(e))
                return False
    
        return True
    
    def remove_docker_machine(self, name):
        res = False
        if self.is_docker_machine_deployed(name):
            try:
                machine = self.d.containers.get(name)
                networks = machine.attrs['NetworkSettings']['Networks'] # Save network settings before erase of machine
                machine.stop()
                machine.remove()
                for network in networks:
                    self.remove_docker_network(network)
            except Exception as e:
                print("[Manager] Error: {}".format(e))
            else:
                res = True
            finally:
                # TODO: will run twice since we have novnc-machine and machine for the user. Maybe check for a 'vnc' identifier
                keytab_path = "/opt/keytabs/{}.{}/krb5.keytab".format(name, self.domain)
                #print("Remove keytab from {}".format(keytab_path))
                if is_file(keytab_path):
                    command = "rm -f {}".format(keytab_path)
                    output = subprocess.run(command.split(" "), stdout=subprocess.PIPE, text=True)
                    #print(output)
        
        return res
    
    def get_docker_machines(self, filters={'label': ['se.domain.app-type=user']}):
        return [c.name for c in self.d.containers.list(all=True, filters=filters)]
    
    def get_expected_networks(self):
        try:
            body = self.connect(self.core_manager_address, self.core_manager_port, 'GET', '/api/network')
        except Exception as e:
            raise Exception("{}".format(e))
        
        if len(body) < 1:
            raise Exception("No data received")
        
        expected_networks = json.loads(body)['result']
        expected_networks = { line['name']: line for line in expected_networks }

        # Return {} if with got empty response.
        # results from api is []
        if len(expected_networks) < 1:
            return {}
    
        return expected_networks
    
    def get_expected_machines(self):
        try:
            body = self.connect(self.core_manager_address, self.core_manager_port, 'GET', '/api/cubicle')
        except Exception as e:
            raise Exception("{}".format(e))

        if len(body) < 1:
            raise Exception("No data received")

        expected_machines = json.loads(body)['result']
        expected_machines = { line['name']: line for line in expected_machines }

        # Return {} if with got empty response.
        # results from api is []
        if len(expected_machines) < 1:
            print("low")
            return {}

        return expected_machines

    def update_expected_information(self):
        # Update the expected networks
        try:
            expected_networks = self.get_expected_networks()
        except Exception as e:
            raise Exception(e)
        else:
            self.expected_networks = expected_networks

        # Update the expected machines / cubicles
        try:
            expected_machines = self.get_expected_machines()
        except Exception as e:
            raise Exception(e)
        else:
            self.expected_machines = expected_machines

    def compare_machines(self):
        try:
            self.update_expected_information()
        except Exception as e:
            print("[Manager] Error: {}".format(e))
            return # Do not touch any machine if we cannot trust the result
    
        # Add machines that do not yet exist
        for machine in self.expected_machines.values():
            # Add cubicle
            if not self.is_docker_machine_deployed(machine['name']):
                print("[Manager] Adding machine {}".format(machine['name']))
                self.add_docker_machine(machine)
            # Add NoVNC to that cubicle
            if not self.is_docker_machine_deployed(machine['name'] + '_novnc'):
                print("[Manager] Adding machine {}".format(machine['name'] + '_novnc'))            
                self.add_docker_novnc_machine(machine)
    
        # Remove machines that no longer are in the expected list
        for machine in self.get_docker_machines():
            if machine in self.expected_machines:
                continue
            if machine.removesuffix('_novnc') in self.expected_machines:
                continue
            # Note: No need to run is_docker_machine_deployed() since get_docker_machine does the same thing.
            # If you have alot of docker-machines running, it might be worth checking again.
            print("[Manager] Removing machine {}".format(machine))  
            self.remove_docker_machine(machine)

class Health(Thread):
    def __init__(self, core_manager_address, core_manager_port = 443, src_address = None):
        Thread.__init__(self)
        self.core_manager_address = core_manager_address
        self.core_manager_port = core_manager_port
        self.src_address = src_address

        self.d = docker.from_env()

    def run(self):
        while True:
            self.send()
            time.sleep(2)

    def send(self):
        health = {
            'stats': {
                'up': 0,
                'other': 0,
            },
            'containers': [],
        }
        for container in self.d.containers.list():
            c = [{
                'name': container.name,
                'state': container.status,
                'started_at': container.attrs['State']['StartedAt'],
            }]
            #print(container.stats(decode=None, stream = False))
            #print(container.attrs['State']['StartedAt'])
            health["containers"].extend(c)

            if(container.status == 'running'):
                health['stats']['up'] += 1
            else:
                health['stats']['other'] += 1

        print(health)
        try:
            import random
            connection = http.client.HTTPConnection(self.core_manager_address, self.core_manager_port, timeout=2, source_address=(self.src_address, random.randint(10000, 65535)))
            headers = {
                'Content-type': 'application/json'
            }
            connection.request('POST', '/api/health', json.dumps(health), headers)
            response = connection.getresponse()
        except Exception as e:
            print("[Health] Error: {}".format(e))
        else:
            print("[Health] Status: {} and reason: {}".format(response.status, response.reason))
        finally:
            connection.close()

# host = ldaps://ldap.example.com
# keytab = /etc/krb5.keytab
def is_credentials_valid(host, keytab = None):
    from ldap import initialize, SERVER_DOWN, INVALID_CREDENTIALS

    if 'KRB5_CLIENT_KTNAME' not in os.environ:
        os.environ['KRB5_CLIENT_KTNAME'] = keytab if keytab is not None else '/etc/krb5.keytab'
    else:
        print("[Configuration] Using environment variable for keytab ({})".format(os.environ['KRB5_CLIENT_KTNAME']))

    # Initilize connection to LDAP
    try:
        conn = initialize(host)
    except Exception as e:
        print("[Configuration] Error: {}".format(e))
        return False

    # Interact with LDAP through GSSAPI and keytab
    try:
        conn.sasl_non_interactive_bind_s('GSSAPI')
    except INVALID_CREDENTIALS:
        print("[Configuration] The keytab ({}) is not valid".format(keytab))
        return False
    except Exception as e:
        print("[Configuration] Error: {}".format(e))
        print("[Configuration] Cannot contact LDAP server ({})".format(host))
        return False

    print("[Configuration] LDAP account is {}".format(conn.whoami_s()))
    conn.unbind()
    return True

def is_library(name):
    # https://stackoverflow.com/questions/14050281/how-to-check-if-a-python-module-exists-without-importing-it
    from importlib.util import find_spec
    return find_spec(name) is not None

def is_tool(name):
    # https://stackoverflow.com/questions/11210104/check-if-a-program-exists-from-a-python-script
    from distutils.spawn import find_executable
    return find_executable(name) is not None

def is_file(name):
    # https://www.geeksforgeeks.org/python-check-if-a-file-or-directory-exists/
    from os.path import exists
    return exists(name)

def is_compliant():
    # Check if python version is correct
    # TODO: redundant? probably get an error at startup anyway?
    import platform
    version = platform.python_version_tuple()
    if int(version[0]) != 3 or int(version[1]) < 10:
        print("[Configuration] Version {} is not supported. Must be 3.10+".format('.'.join(map(str, version))))

    # Check if library is available
    for lib in ['docker', 'ldap', 'lol']:
        print("[Configuration] Checking support for {}".format(lib))
        if is_library(lib):
            continue
        print("[Configuration] Missing '{}' library".format(lib))

    # Check is binary is available
    for binary in ['ipa']:
        print("[Configuration] Checking support for {}".format(binary))
        if is_tool(binary):
            continue
        print("[Configuration] Missing '{}' binary".format(binary))

    # Check if file is available
    for file in ['/etc/krb5.keytab']:
        print("[Configuration] Checking support for {}".format(file))
        if is_file(file):
            continue
        print("[Configuration] Missing '{}' file".format(file))

    # Check if LDAP is working
    print("[Configuration] Checking LDAP connection")
    if not is_credentials_valid('ldap://ipa.domain.internal'):
        print("[Configuration] Connection to LDAP could not be established")

def main():
    argParser = argparse.ArgumentParser()
    argParser.add_argument("-s", "--server", help="Address to Core Manager")
    argParser.add_argument("-p", "--port", type=int, default=443, help="Port to Core Manager")
    argParser.add_argument("-l", "--ldap", type=str, help="LDAP server. eg. ldaps://ipa.domain.internal:389")
    argParser.add_argument("-i", "--src", help="Source address")

    args = argParser.parse_args()
    
    print("[Configuration] Core Manager: {}:{}".format(args.server, args.port))
    if args.server == None:
        print("[Configuration] Server-value cannot be None")
        exit(-1)

    is_compliant()

    thread1 = Manager(args.server, args.port, args.ldap, args.src)
    #thread2 = Health(args.server, args.port, args.src)

    thread1.start()
    #thread2.start()

    thread1.join()
    #thread2.join()

if __name__ == "__main__":
    main()
