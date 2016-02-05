# Copyright 2015 Cisco Systems
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

from oslo_config import cfg
from oslo_log import log as logging

from nova.virt.ironic import driver as ironic_driver

LOG = logging.getLogger(__name__)

CONF = cfg.CONF


class DynamicNetworkIronicDriver(ironic_driver.IronicDriver):
    """Hypervisor driver for Ironic - bare metal provisioning."""

    def macs_for_instance(self, instance):
        return None

    def _plug_vifs(self, node, instance, network_info):
        ports = self.ironicclient.call("node.list_ports", node.uuid)
        portgroups = self.ironicclient.call("node.list_portgroups", node.uuid)

        vid = 0
        for vif in network_info:
            if not portgroups:
                port = ports[vid % len(ports)]
                port = self.ironicclient.call("port.get", port.uuid)
                port.extra['vif_port_ids'] = port.extra.get('vif_port_ids', [])
                port.extra['vif_port_ids'].append(vif['id'])
                patch = [{'op': 'add', 'path': '/extra/vif_port_ids',
                          'value': port.extra.get('vif_port_ids', [])}]
                self.ironicclient.call("port.update", port.uuid, patch)
            else:
                portgroup = portgroups[vid % len(portgroups)]
                portgroup.extra['vif_port_ids'] = (
                    portgroup.extra.get('vif_port_ids', []))
                portgroup.extra['vif_port_ids'].append(vif['id'])
            vid += 1
        for portgrp in portgroups:
            patch = [{'op': 'add', 'path': '/extra/vif_port_ids',
                      'value': portgrp.extra.get('vif_port_ids', [])}]
            self.ironicclient.call("portgroup.update", portgrp.uuid, patch)

    def _unplug_vifs(self, node, instance, network_info):
        ports = self.ironicclient.call("node.list_ports", node.uuid)
        portgroups = self.ironicclient.call("node.list_portgroups", node.uuid)
        for port in ports:
            patch = [{'op': 'remove', 'path': '/extra/vif_port_ids'}]
            try:
                self.ironicclient.call("port.update", port.uuid, patch)
            except Exception:
                pass
        for portgrp in portgroups:
            patch = [{'op': 'remove', 'path': '/extra/vif_port_ids'}]
            try:
                self.ironicclient.call("portgroup.update", portgrp.uuid, patch)
            except Exception:
                pass
