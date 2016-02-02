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

from ironic.common import exception
from ironic.common import network as common_net
from ironic.dhcp import neutron
from sam_ironic_contrib import network_provider


def _get_vifs(task):
    all_vifs = []
    for port in task.ports:
        vifs = port.extra.get('vif_port_ids', [])
        for vif in vifs:
            all_vifs.append(vif)
        prov_vif = port.extra.get('vif_port_id')
        if prov_vif:
            all_vifs.append(prov_vif)

    for portgroup in task.portgroups:
        vifs = portgroup.extra.get('vif_port_ids', [])
        for vif in vifs:
            all_vifs.append(vif)
        prov_vif = portgroup.extra.get('vif_port_id')
        if prov_vif:
            all_vifs.append(prov_vif)
    return all_vifs


class NeutronDHCPApi(neutron.NeutronDHCPApi):
    """API for communicating to neutron 2.x API."""

    def update_dhcp_opts(self, task, options, vifs=None):
        if vifs is None:
            vifs = _get_vifs(task)
        if not vifs:
            raise exception.FailedToUpdateDHCPOptOnPort(
                _("No VIFs found for node %(node)s when attempting "
                  "to update DHCP BOOT options.") %
                {'node': task.node.uuid})
        for vif in vifs:
            try:
                self.update_port_dhcp_opts(vif, options,
                                           token=task.context.auth_token)
            except Exception:
                pass

    def get_ip_addresses(self, task):
        vifs = _get_vifs(task)
        client = common_net.get_neutron_client()
        ips = []
        for vif in vifs:
            n_port = client.show_port(vif).get('port')
            fixed_ips = n_port.get('fixed_ips')
            if fixed_ips:
                ips.append(fixed_ips[0].get('ip_address', None))
        return ips

    def create_cleaning_ports(self, task):
        net_provider = network_provider.NetworkProvider()
        return net_provider.add_cleaning_network(task)

    def delete_cleaning_ports(self, task):
        net_provider = network_provider.NetworkProvider()
        return net_provider.remove_cleaning_network(task)
