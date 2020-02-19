#!/usr/bin/env python

from __future__ import absolute_import, print_function, division

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
---
module: ad_dns
short_description: Manages AD DNS records via samba-tool
description:
    - Manages DNS records present in Microsoft AD.
    - Command line utility samba-tool is required by this module.
version_added: 2.9
author:
    - Dusan Matejka (@D3DeFi)
requirements:
    - python >= 2.7
options:
    server:
        description:
            - AD DC where to create DNS record.
        required: True
        type: str
    username:
        description:
            - AD domain account to manage DNS record as.
        required: True
        type: str
    password:
        description:
            - Password for the AD domain account.
        required: True
        type: str
    zone:
        description:
            - A DNS zone where to manage the DNS record.
        required: True
        type: str
    record:
        description:
            - Managed DNS record in a short form (not a FQDN).
        required: True
        type: str
    value:
        description:
            - Value for a managed DNS record.
            - Usually an IP address if I(type=A) or an existing forward record if I(type=CNAME).
            - 'For each type data contents are as follows:'
            - ' - I(type=A)      ipv4_address_string'
            - ' - I(type=AAAA)   ipv6_address_string'
            - ' - I(type=PTR)    fqdn_string'
            - ' - I(type=CNAME)  fqdn_string'
            - ' - I(type=NS)     fqdn_string'
            - ' - I(type=MX)     fqdn_string preference'
            - ' - I(type=SRV)    fqdn_string port priority weight'
            - ' - I(type=TXT)    string1 string2 ...'
        required: True
        type: str
    type:
        description:
            - Type of a DNS record to manage.
        choices: [A, AAAA, PTR, CNAME, NS, MX, SRV, TXT]
        default: A
        type: str
    state:
        description:
            - When I(state=present) a DNS record will be created if not present.
            - When I(state=absent) a DNS record will be deleted if present.
            - If the same record already exists, but with the different value, this module will
              add new value to the same record keeping the old one intact!
        choices: [present, absent]
        default: present
        type: str
'''

EXAMPLES = '''
- name: Create a new forward A DNS record
  ad_dns:
    server: dc01.example.com
    username: admin.user
    password: admin.password
    zone: example.com
    record: www
    value: 10.1.1.123
    type: A
    state: present

- name: Create a new forward CNAME DNS record
  ad_dns:
    server: dc01.example.com
    username: admin.user
    password: admin.password
    zone: example.com
    record: web
    value: www.example.com
    type: CNAME
    state: present

- name: Create a new reverse PTR DNS record
  ad_dns:
    server: dc01.example.com
    username: admin.user
    password: admin.password
    zone: 1.1.10.in-addr.arpa
    record: 123
    value: www.example.com
    type: PTR
    state: present
'''


import re

from ansible.module_utils.basic import AnsibleModule


class SambaToolDNS(object):
    """Wrapper interacting with samba-tool to manage DNS records in AD DC.

    Attributes:
        module: AnsibleModule object
        smbtool: full absolute path to samba-tool executable. Fails module if not found in $PATH
        server: AD DC server where to manage DNS records
        zone: DNS zone where to manage DNS records
        cmdb: base of the command to execute - `/path/samba-tool dns`
        cmdc: credentials to append at the end of the command
    """

    def __init__(self, module):
        """Inits SambaToolDNS class with AnsibleModule."""
        self.module = module
        self.smbtool = str(self.module.get_bin_path('samba-tool', required=True))
        self.server = self.module.params['server']
        self.zone = self.module.params['zone']
        self.cmdb = [self.smbtool, 'dns']
        self.cmdc = [
            "--username=\'{}\'".format(self.module.params['username']),
            "--password=\'{}\'".format(self.module.params['password'])
        ]


    def get_exist_dns(self, record, type):
        """Searches for existing DNS records present on DC server

        function uses `samba-tool dns query` command to locate any existing
        DNS records present in the AD DC server.

        Args:
            record: DNS record to locate
            type: type of the DNS record to return

        Returns:
            A list of tuples with found records if any are present or None. Example:
            [
                (
                    '10.1.1.123',   # Recod value
                    'f0',           # Active flags
                    '0',            # Serial number
                    '1800'          # TTL
                )
            ]
        """
        cmd = self.cmdb + ['query', self.server, self.zone, record, type] + self.cmdc
        (rc, stdout, stderr) = self.module.run_command(' '.join(cmd), use_unsafe_shell=True)

        if 'WERR_DNS_ERROR_ZONE_DOES_NOT_EXIST' in stderr:
            self.module.fail_json(msg='Zone {} does not exists.'.format(self.zone))

        if rc != 0 or 'WERR_DNS_ERROR_NAME_DOES_NOT_EXIST' in stderr:
            return []

        if stdout or stdout is not None:
            # Parsing records returned in the response. Output looks like one of this:
            #  Name=, Records=1, Children=0
            #    A: 10.1.1.123 (flags=f0, serial=0, ttl=1800)
            #  Name=, Records=2, Children=0
            #    A: 10.1.1.123 (flags=f0, serial=0, ttl=1800)
            #    A: 10.1.1.124 (flags=f0, serial=0, ttl=1800)
            m = re.findall('{}: ([0-9.]+) \(flags=(.*), serial=(.*), ttl=(.*)\)'.format(type), stdout)
            return m
        else:
            return []

    def manage_dns(self, record, value, type, action):
        """Creates or deletes a DNS record with a required value and a type.

        This method will not overwrite any existing values present on the record
        and will simply add another under the requested record name. If no record
        with such name existed until now, it will be created from the scratch by
        samba-tool.

        When deleting, this method will only delete a specific value from the record
        if multiple values are associated with it. Record will not be deleted as a
        whole if there are other values present.

        Args:
            record: a record name to manage
            value: value for the record to add/delete
            type: type of the managed record
            action: add or delete
        """
        if action not in ['add', 'delete']:
            self.module.fail_json(msg='Wrong action for managing DNS record')

        cmd = self.cmdb + [action, self.server, self.zone, record, type, value] + self.cmdc
        (rc, stdout, stderr) = self.module.run_command(' '.join(cmd), use_unsafe_shell=True)

        # if for some reason this was not catched earlier and samba-tool got such response
        if action == 'add' and rc != 0 and 'WERR_DNS_ERROR_RECORD_ALREADY_EXISTS' in stderr:
            self.module.exit_json(changed=False, msg='Rercord already present')
        elif rc != 0:
            self.module.fail_json(
                msg='Action {} for record failed'.format(action),
                samba_tool_stderr=stderr,
                samba_tool_rc=rc
            )


def main():
    module = AnsibleModule(
        argument_spec=dict(
            server=dict(type='str', required=True),
            username=dict(type='str', required=True),
            password=dict(type='str', required=True, no_log=True),
            zone=dict(type='str', required=True),
            record=dict(type='str', required=True),
            value=dict(type='str', required=True),
            type=dict(type='str', choices=['A', 'AAAA', 'PTR', 'CNAME', 'NS', 'MX', 'SRV', 'TXT'], default='A'),
            state=dict(type='str', choices=['present', 'absent'], default='present'),
        ),
        supports_check_mode=True
    )

    record = module.params['record']
    value = module.params['value']
    type = module.params['type']
    state = module.params['state']

    # check if DNS record exists and zone is present
    st = SambaToolDNS(module)
    exist_records = st.get_exist_dns(record, type)

    if state == 'present':
        # exit if record with the same value is already present
        if exist_records:
            for rec in exist_records:
                if rec[0] == value:
                    module.exit_json(ok=True, msg='Record already present')

        if not module.check_mode:
            st.manage_dns(record, value, type, action='add')

        module.exit_json(changed=True, msg='Record has been created')

    elif state == 'absent':
        if exist_records:
            # check if record has value that should be deleted
            if value in [r[0] for r in exist_records]:
                if not module.check_mode:
                    st.manage_dns(record, value, type, action='delete')

                module.exit_json(changed=True, msg='Record has been deleted')
            else:
                # exit if DNS record exists, but with a different values
                module.exit_json(ok=True, msg='Record found with different value, but not with {}'.format(value))
        else:
            # exit if DNS record with such name doesn't exist
            module.exit_json(ok=True, msg='No such record found')



if __name__ == '__main__':
    main()
