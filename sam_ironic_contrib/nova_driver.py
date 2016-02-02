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

import base64
import gzip
import shutil
import tempfile

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils

from nova.i18n import _LE

from nova.api.metadata import base as instance_metadata
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

    def _get_portgroup_info(self, node):
        pgs = self.ironicclient.call("node.list_portgroups", node.uuid,
                                     detail=True)
        ports = self.ironicclient.call("node.list_ports", node.uuid,
                                       detail=True)
        for pg in pgs:
            pg.bond_links = []
            for port in ports:
                if port.portgroup_uuid == pg.uuid:
                    pg.bond_links.append(port)
        return pgs

    def _get_member_link(self, member_link, bond_id, ifc_num):
        link = {
            'id': '%sSlave%d' % (bond_id, ifc_num),
            'vif_id': None,
            'type': 'ethernet',
            'mtu': None,
            'ethernet_mac_address': member_link.address,
        }
        return link

    def _generate_configdrive(self, instance, node, network_info,
                              extra_md=None, files=None):
        """Generate a config drive.

        :param instance: The instance object.
        :param node: The node object.
        :param network_info: Instance network information.
        :param extra_md: Optional, extra metadata to be added to the
                         configdrive.
        :param files: Optional, a list of paths to files to be added to
                      the configdrive.

        """
        if not extra_md:
            extra_md = {}

        network_metadata = netutils.get_network_metadata(network_info)

        pg_info = self._get_portgroup_info(node)

        bonds = False
        for pg in pg_info:
            if pg.bond_links:
                bonds = True
                for b_link in network_metadata['links']:
                    if b_link['ethernet_mac_address'] == pg.address:
                        b_link['type'] = 'bond'
                        b_link['bond_links'] = []
                        b_link['bond_mode'] = pg.extra.get('mode', 0)
                        m_link_num = 0
                        for member_link in pg.bond_links:
                            m_link = self._get_member_link(
                                member_link, b_link['id'], m_link_num)
                            network_metadata['links'].append(m_link)
                            b_link['bond_links'].append(m_link['id'])
                            m_link_num += 1
                        break

        if bonds:
            files.append(('ironicbonds', 'yes'.encode()))

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