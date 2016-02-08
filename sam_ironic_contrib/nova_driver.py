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

import base64
import gzip
import shutil
import tempfile

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils

from nova.i18n import _LE

from nova.api.metadata import base as instance_metadata
from nova.network.neutronv2 import api as neutron
from nova.virt import configdrive
from nova.virt.ironic import driver as ironic_driver
from nova.virt import netutils

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

    def _objs_to_dicts(self, objs):
        dicts = []
        for obj in objs:
            dicts.append(obj.to_dict())
        return dicts

    def _get_port_for_vif(self, ports, vif):
        for port in ports:
            if vif in port.extra['vif_port_ids']:
                return port

    def _generate_configdrive(self, instance, node, network_info,
                              extra_md=None, files=None):
        if not extra_md:
            extra_md = {}

        client = neutron.get_client(None, admin=True)

        # Get vlan to port map
        net_vlan_map = {}
        port_vlan_map = {}
        for vif in network_info:
            if vif['network']['id'] not in net_vlan_map:
                network = client.show_network(vif['network']['id'])
                net_vlan_map[vif['network']['id']] = (
                    network['network']['provider:segmentation_id'])
            port_vlan_map[vif['id']] = net_vlan_map[vif['network']['id']]

        network_metadata = netutils.get_network_metadata(network_info)

        ports = self.ironicclient.call("node.list_ports",
                                       node.uuid, detail=True)
        portgroups = self.ironicclient.call("node.list_portgroups", node.uuid)
        all_ports = ports + portgroups

        for link in network_metadata['links']:
            link['type'] = 'vlan'
            link['vlan_mac_address'] = link['ethernet_mac_address']
            link['neutron_port_id'] = link['vif_id']
            link['vlan_id'] = port_vlan_map[link['vif_id']]
            link['vlan_link'] = self._get_port_for_vif(
                all_ports, link['vif_id']).uuid
            del link['ethernet_mac_address']
            del link['vif_id']

        for port in ports:
            if port.extra.get('vif_port_ids', []):
                link = {'id': port.uuid, 'type': 'phy', 'mtu': 9000,
                        'ethernet_mac_address': port.address}
                network_metadata['links'].append(link)

        for pg in portgroups:
            ps = [port for port in ports if port.portgroup_id is pg.id]
            link = {'id': pg.uuid, 'type': 'bond',
                    'ethernet_mac_address': ports[0].address,
                    'bond_mode': '802.1ad',
                    'bond_xmit_hash_policy': 'layer3+4',
                    'bond_miimon': 100, 'bond_links': []}
            for port in ps:
                link['bond_links'].append(port.uuid)

        files.append(('ironicnetworking', 'yes'.encode()))

        i_meta = instance_metadata.InstanceMetadata(
            instance, content=files, extra_md=extra_md,
            network_metadata=network_metadata)

        with tempfile.NamedTemporaryFile() as uncompressed:
            try:
                with configdrive.ConfigDriveBuilder(instance_md=i_meta) as cdb:
                    cdb.make_drive(uncompressed.name)
            except Exception as e:
                with excutils.save_and_reraise_exception():
                    LOG.error(_LE("Creating config drive failed with "
                                  "error: %s"), e, instance=instance)

            with tempfile.NamedTemporaryFile() as compressed:
                # compress config drive
                with gzip.GzipFile(fileobj=compressed, mode='wb') as gzipped:
                    uncompressed.seek(0)
                    shutil.copyfileobj(uncompressed, gzipped)

                # base64 encode config drive
                compressed.seek(0)
                return base64.b64encode(compressed.read())
