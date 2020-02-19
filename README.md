ad\_dns
-------

Simple ansible module to create or delete DNS record in Active Directory DNS.

It requires samba-tool to be installed on the system (can be found in smbclient or samba-common-bin, depends on distribution and version):

	apt install samba-common-bin

Place the module into the library/ folder:

	cp ad_dns.py <path_to_project>/library/
	cd <path_to_project>
	ansible-doc ad_dns
