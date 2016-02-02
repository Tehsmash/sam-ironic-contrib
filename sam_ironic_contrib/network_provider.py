# Copyright 2016, Cisco Systems.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg
from oslo_log import log as logging

from ironic.common import network as common_net
from ironic.networks import base
from ironic import objects

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class NetworkProvider(base.NetworkProvider):

    def add_provisioning_network(self, task):
        node = task.node
        network_uuid = CONF.provisioning_network_uuid
        client = common_net.get_neutron_client()

        for port in task.ports:
            body = {
                'port': {
                    'network_id': network_uuid,
                    'mac_address': port.address,
                    'device_owner': 'baremetal:none',
                    'device_id': node.uuid,
                    'admin_state_up': True,
                    'binding:vnic_type': 'baremetal',
                    'binding:host_id': node.uuid,
                    'binding:profile': {
                        'local_link_information': [
                            port.local_link_connection
                        ],
                    },
                }
            }
            por = client.create_port(body)
            extra = dict(port.extra)
            extra['vif_port_id'] = por['port']['id']
            port.extra = extra
            port.save()
        task.ports = objects.Port.list_by_node_id(task.context, node.id)
        return self._port_map(task)

    def remove_provisioning_network(self, task):
        client = common_net.get_neutron_client()
        for port in task.ports:
            try:
                client.delete_port(port.extra['vif_port_id'])
            except Exception:
                pass
            extra = dict(port.extra)
            try:
                del extra['vif_port_id']
            except Exception:
                pass
            port.extra = extra
            port.save()
        task.ports = objects.Port.list_by_node_id(task.context, task.node.id)
        return self._port_map(task)

    def configure_tenant_networks(self, task):
        node = task.node
        client = common_net.get_neutron_client()
        for portgroup in task.portgroups:
            for vif in portgroup.extra.get('vif_port_ids', []):
                lli = []
                ports = objects.Port.list_by_portgroup_id(portgroup.id)
                for port in ports:
                    lli.append(port.local_link_connection)

                body = {
                    'port': {
                        'device_owner': 'baremetal:none',
                        'device_id': node.instance_uuid,
                        'admin_state_up': True,
                        'binding:vnic_type': 'baremetal',
                        'binding:host_id': node.uuid,
                        'binding:profile': {
                            'local_link_information': lli,
                        },
                    }
                }
                client.update_port(vif, body)
        for port in task.ports:
            if port.portgroup_id is None:
                for vif in port.extra.get('vif_port_ids', []):
                    body = {
                        'port': {
                            'device_owner': 'baremetal:none',
                            'device_id': node.instance_uuid,
                            'admin_state_up': True,
                            'binding:vnic_type': 'baremetal',
                            'binding:host_id': node.uuid,
                            'binding:profile': {
                                'local_link_information': [
                                    port.local_link_connection
                                ],
                            },
                        }
                    }
                    client.update_port(vif, body)

    def unconfigure_tenant_networks(self, task):
        pass

    def add_cleaning_network(self, task):
        node = task.node
        network_uuid = CONF.neutron.cleaning_network_uuid
        client = common_net.get_neutron_client()

        for port in task.ports:
            body = {
                'port': {
                    'network_id': network_uuid,
                    'mac_address': port.address,
                    'device_owner': 'baremetal:none',
                    'device_id': node.uuid,
                    'admin_state_up': True,
                    'binding:vnic_type': 'baremetal',
                    'binding:host_id': node.uuid,
                    'binding:profile': {
                        'local_link_information': [
                            port.local_link_connection
                        ],
                    },
                }
            }
            por = client.create_port(body)
            extra = dict(port.extra)
            extra['vif_port_id'] = por['port']['id']
            port.extra = extra
            port.save()
        task.ports = objects.Port.list_by_node_id(task.context, node.id)
        return self._port_map(task)

    def remove_cleaning_network(self, task):
        client = common_net.get_neutron_client()
        for port in task.ports:
            try:
                client.delete_port(port.extra['vif_port_id'])
            except Exception:
                pass
            extra = dict(port.extra)
            try:
                del extra['vif_port_id']
            except Exception:
                pass
            port.extra = extra
            port.save()
        task.ports = objects.Port.list_by_node_id(task.context, task.node.id)
        return self._port_map(task)

    def _port_map(self, task):
        ma = {}
        for port in task.ports:
            ma[port.uuid] = port.extra.get('vif_port_id')
        return ma
