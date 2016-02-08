#!/usr/bin/env python
# Copyright 2016 Cisco Systems
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import glob
import json
import logging
import os
import subprocess
import sys

ignored_interfaces = ('sit', 'tunl', 'bonding_master', 'teql',
                      'ip6_vti', 'ip6tnl', 'bond', 'lo')
post_up = "    post-up route add -net {net} netmask {mask} gw {gw} || true\n"
pre_down = "    pre-down route del -net {net} netmask {mask} gw {gw} || true\n"

log = logging.getLogger("net_config")


def _exists_debian_interface(name):
    file_to_check = '/etc/network/interfaces.d/{name}.cfg'.format(name=name)
    return os.path.exists(file_to_check)


def write_debian_interfaces(interfaces, sys_interfaces):
    eni_path = '/etc/network/interfaces'
    eni_d_path = eni_path + '.d'
    files_to_write = dict()
    files_to_write[eni_path] = "auto lo\niface lo inet loopback\n"
    files_to_write[eni_path] += "source /etc/network/interfaces.d/*.cfg\n"

    for iname, interface in interfaces.items():
        if (interface['mac_address'] not in sys_interfaces and
                interface['link_mac'] not in sys_interfaces):
            continue

        if 'vlan_id' in interface:
            vlan_raw_device = sys_interfaces[interface['link_mac']]
            interface_name = "{0}.{1}".format(
                vlan_raw_device,
                interface['vlan_id'])
        else:
            interface_name = sys_interfaces[interface['mac_address']]

        if _exists_debian_interface(interface_name):
            continue

        iface_path = os.path.join(eni_d_path, '%s.cfg' % interface_name)

        if interface['type'] == 'ipv4_dhcp':
            result = "auto {0}\n".format(interface_name)
            result += "iface {0} inet dhcp\n".format(interface_name)
            if vlan_raw_device is not None:
                result += "    vlan-raw-device {0}\n".format(vlan_raw_device)
                result += "    hw-mac-address {0}\n".format(
                    interface['mac_address'])
            files_to_write[iface_path] = result
            continue
        if interface['type'] == 'ipv6':
            link_type = "inet6"
        elif interface['type'] == 'ipv4':
            link_type = "inet"
        # We do not know this type of entry
        if not link_type:
            continue

        result = "auto {0}\n".format(interface_name)
        result += "iface {name} {link_type} static\n".format(
            name=interface_name, link_type=link_type)
        if vlan_raw_device:
            result += "    vlan-raw-device {0}\n".format(vlan_raw_device)
        result += "    address {0}\n".format(interface['ip_address'])
        result += "    netmask {0}\n".format(interface['netmask'])
        for route in interface['routes']:
            if route['network'] == '0.0.0.0' and route['netmask'] == '0.0.0.0':
                result += "    gateway {0}\n".format(route['gateway'])
            else:
                result += post_up.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
                result += pre_down.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
        files_to_write[iface_path] = result
    return files_to_write


def get_config_drive_interfaces(net):
    interfaces = {}

    if 'networks' not in net or 'links' not in net:
        log.debug("No config-drive interfaces defined")
        return interfaces

    networks = {}
    for network in net['networks']:
        networks[network['link']] = network

    vlans = {}
    phys = {}
    bonds = {}
    for link in net['links']:
        if link['type'] == 'vlan':
            vlans[link['id']] = link
        elif link['type'] == 'phy':
            phys[link['id']] = link
        elif link['type'] == 'bond':
            bonds[link['id']] = link

    for link in vlans.values():
        if link['vlan_link'] in phys:
            vlan_link = phys[link['vlan_link']]
        elif link['vlan_link'] in bonds:
            vlan_link = bonds[link['vlan_link']]
        link['link_mac'] = vlan_link['ethernet_mac_address']
        link['mac_address'] = link.get('vlan_mac_address',
                                       vlan_link['ethernet_mac_address'])

    for link in phys.values():
        link['mac_address'] = link.get('ethernet_mac_address')

    for i, network in networks.items():
        link = vlans.get(i, phys.get(i, bonds.get(i)))
        if not link:
            continue
        link['type'] = network['type']
        link['network_id'] = network['network_id']
        interfaces[i] = link

    return interfaces


def get_sys_interfaces():
    sys_root = os.path.join('/sys/class/net')
    interfaces = [f for f in os.listdir(sys_root)
                  if not f.startswith(ignored_interfaces)]
    inter_map = {}
    for interface in interfaces:
        mac = open('%s/%s/address' % (sys_root, interface), 'r').read().strip()
        inter_map[mac] = interface
    return inter_map


def restart_networking():
    subprocess.call(['sudo', 'ifdown', '--exclude=lo', '-a'])
    subprocess.call(['sudo', 'ifup', '--exclude=lo', '-a'])


def main():
    files = glob.glob("/etc/network/interfaces.d/*")
    cmd = ['sudo', 'rm', '-f', '/etc/network/interfaces']
    cmd.extend(files)
    subprocess.call(cmd)

    subprocess.call(['sudo', 'mkdir', '-p', '/mnt/config'])
    subprocess.call(['sudo', 'mount', '/dev/disk/by-label/config-2',
                     '/mnt/config'])
    data = json.load(open("/mnt/config/openstack/latest/network_data.json"))
    interfaces = get_config_drive_interfaces(data)
    sys_interfaces = get_sys_interfaces()
    files = write_debian_interfaces(interfaces, sys_interfaces)
    for k, v in files.items():
        with open(k, 'w') as outfile:
            outfile.write(v)
    restart_networking()

if __name__ == '__main__':
    sys.exit(main())
