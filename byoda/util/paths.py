'''
Python module for directory and file management a.o. for secrets

:maintainer : Steven Hessing (stevenhessing@live.com)
:copyright  : Copyright 2020, 2021
:license    : GPLv3
'''

import os
import logging


_LOGGER = logging.getLogger(__name__)


class Paths:
    '''
    Filesystem path management. Provides a uniform interface
    to the location of various files
    '''
    # Directory prepended to paths
    _ROOT_DIR = os.environ['HOME'] + '/.byoda/'

    # Templates for location of directories and files
    # all paths not starting with '/' will have the root directory prepended
    CONFIG_FILE          = 'config.yml'              # noqa
    SECRETS_DIR          = 'private/'                # noqa
    NETWORK_DIR          = 'network-{network}'       # noqa
    NETWORK_FILE         = 'network-{network}.json'  # noqa
    NETWORK_ROOT_CA_CERT_FILE     = 'network-{network}/network-{network}-root-ca-cert.pem'                     # noqa
    NETWORK_ROOT_CA_KEY_FILE      = 'private/network-{network}-root-ca.key'                                    # noqa
    NETWORK_ACCOUNTS_CA_CERT_FILE = 'network-{network}/network-{network}-accounts-ca-cert.pem'                 # noqa
    NETWORK_ACCOUNTS_CA_KEY_FILE  = 'private/network-{network}-accounts-ca.key'                                # noqa
    NETWORK_SERVICES_CA_CERT_FILE = 'network-{network}/network-{network}-services-ca-cert.pem'                 # noqa
    NETWORK_SERVICES_CA_KEY_FILE  = 'private/network-{network}-services-ca.key'                                # noqa

    ACCOUNT_DIR          = 'network-{network}/account-{account}/'                                              # noqa
    ACCOUNT_FILE         = 'network-{network}/account-{account}/account-{account}.json'                        # noqa
    ACCOUNT_CERT_FILE    = 'network-{network}/account-{account}/{account}-cert.pem'                            # noqa
    ACCOUNT_KEY_FILE     = 'private/network-{network}-account-{account}.key'                                   # noqa

    SERVICE_DIR          = 'network-{network}/services/service-{service}/'                                     # noqa
    SERVICE_FILE         = 'network-{network}/services/service-{service}/service-{service}.json'               # noqa
    SERVICE_CA_CERT_FILE = 'network-{network}/network-{network}-service-{service}-ca-cert.pem'                 # noqa
    SERVICE_CA_KEY_FILE  = 'private/network-{network}-service-{service}-ca.key'                                # noqa
    SERVICE_MEMBERS_CA_CERT_FILE = 'network-{network}/network-{network}-service-{service}-member-ca-cert.pem'  # noqa
    SERVICE_MEMBERS_CA_KEY_FILE  = 'private/network-{network}-service-{service}-member-ca.key'                 # noqa
    SERVICE_CERT_FILE    = 'network-{network}/services/service-{service}/service-{service}-cert.pem'           # noqa
    SERVICE_KEY_FILE     = 'private/network-{network}-service-{service}.key'                                   # noqa

    MEMBER_DIR            = 'network-{network}/account-{account}/service-{service}/'                                  # noqa
    MEMBER_CERT_FILE      = 'network-{network}/account-{account}/service-{service}/service-{service}-cert.pem'        # noqa
    MEMBER_KEY_FILE       = 'network-{network}/account-{account}/service-{service}/service-{service}.key'             # noqa
    MEMBER_DATA_CERT_FILE = 'network-{network}/account-{account}/service-{service}/service-{service}-data-cert.pem'   # noqa
    MEMBER_DATA_KEY_FILE  = 'network-{network}/account-{account}/service-{service}/service-{service}-data.key'        # noqa

    def __init__(self, root_directory=_ROOT_DIR, account_alias=None,
                 network_name=None):
        '''
        Initiate instance with root_dir and account_alias members
        set

        :param str root_directory : optional, the root directory under which
                                    all other files and directories are
                                    stored
        :param str network_name   : optional, name of the network
        :param str account_alias  : optional, alias for the account. If no
                                    alias is specified then an UUID is
                                    generated and used as alias
        :returns: (none)
        :raises: (none)
        '''

        self._root_directory = root_directory

        self._account = account_alias
        self._network = network_name
        self.services = set()
        self.memberships = set()

    def get(self, path_template, service_alias=None):
        '''
        Gets the file/path for the specified path_type

        :param str path_template : string to be formatted
        :returns: string with the full path to the directory
        :raises: KeyError if path_type is for a service
                                   and the service parameter is not specified
        '''

        if '{network}' in path_template and not self._network:
            raise ValueError('No network specified')
        if '{service}' in path_template and not service_alias:
            raise ValueError('No service specified')
        if '{account}' in path_template and not self._account:
            raise ValueError('No account specified')

        path = path_template.format(
            network=self._network,
            account=self._account,
            service=service_alias,
        )
        if path[0] != '/':
            path = self._root_directory + '/' + path

        return path

    def _exists(self, path_template, service_alias=None, member_alias=None):
        return os.path.exists(
            self.get(
                path_template, service_alias=service_alias,
                member_alias=member_alias
            )
        )

    def _create_directory(self, path_template, service_alias=None):
        '''
        Ensures a directory exists. If it does not already exit
        then the directory will be created

        :param str path_template : string to be formatted
        :param str service       : string with the service to create the
                                   directory for
        :returns: string with the full path to the directory
        :raises: ValueError if PathType.SERVICES_FILE or PathType.CONFIG_FILE
                 is specified
        '''

        directory = self.get(
            path_template, service_alias=service_alias
        )

        os.makedirs(directory, exist_ok=True)

        return directory

    def root_directory(self):
        return self.get(self._root_directory)

    # Secrets directory
    def secrets_directory(self):
        return self.get(self.SECRETS_DIR)

    def secrets_directory_exists(self):
        return self._exists(self.SECRETS_DIR)

    def create_secrets_directory(self):
        return self._create_directory(self.SECRETS_DIR)

    # Network directory
    @property
    def network(self):
        return self._network

    @network.setter
    def network(self, value):
        self._network = value

    def network_directory(self):
        return self.get(self.NETWORK_DIR)

    def network_directory_exists(self):
        return self._exists(self.NETWORK_DIR)

    def create_network_directory(self):
        return self._create_directory(self.NETWORK_DIR)

    # Account directory
    @property
    def account(self):
        return self._account

    @account.setter
    def account(self, value):
        self._account = value

    def account_directory(self):
        return self.get(self.ACCOUNT_DIR)

    def account_directory_exists(self):
        return self._exists(self.ACCOUNT_DIR)

    def create_account_directory(self):
        return self._create_directory(self.ACCOUNT_DIR)

    # service directory
    def service(self, service_alias):
        return self.get(service_alias)

    def service_directory(self, service_alias):
        return self.get(self.SERVICE_DIR, service_alias=service_alias)

    def service_directory_exists(self, service_alias):
        return self._exists(self.SERVICE_DIR, service_alias=service_alias)

    def create_service_directory(self, service_alias):
        return self._create_directory(
            self.SERVICE_DIR, service_alias=service_alias
        )

    # Membership directory
    def member_directory(self, service_alias):
        return self.get(
            self.MEMBER_DIR, service_alias=service_alias
        )

    def member_directory_exists(self, service_alias):
        return self._exists(
            self.MEMBER_DIR, service_alias=service_alias
        )

    def create_member_directory(self, service_alias):
        return self._create_directory(
            self.MEMBER_DIR, service_alias=service_alias
        )

    # Config file
    def config_file(self):
        return self.get(self.CONFIG_FILE)

    def config_file_exists(self):
        return self._exists(self.CONFIG_FILE)

    # Services file with list of services
    def services_file(self):
        return self.get(self.SERVICES_FILE)

    def services_file_exists(self):
        return self._exists(self.SERVICES_FILE)

    # Network files
    def network_file(self):
        return self.get(self.NETWORK_FILE)

    def network_file_exists(self):
        return self._exists(self.NETWORK_FILE)

    # Account files
    def account_file(self):
        return self.get(self.ACCOUNT_FILE)

    def account_file_exists(self):
        return self._exists(self.ACCOUNT_FILE)

    # Service files
    def service_file(self, service_alias):
        return self.get(self.SERVICE_FILE, service_alias=service_alias)

    def service_file_exists(self, service_alias):
        return self._exists(self.SERVICE_FILE, service_alias=service_alias)