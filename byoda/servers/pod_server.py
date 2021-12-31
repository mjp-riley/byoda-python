'''
Class ServiceServer derived from Server class for modelling
a server that hosts a BYODA Service

:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2021
:license    : GPLv3
'''

import logging
from typing import TypeVar, Dict

from byoda.util.api_client import RestApiClient
from byoda.util import Paths

from byoda import config

from .server import Server
from .server import ServerType


_LOGGER = logging.getLogger(__name__)


Network = TypeVar('Network')
RegistrationStatus = TypeVar('RegistrationStatus')


class PodServer(Server):
    HTTP_PORT = 8000

    def __init__(self):
        super().__init__()

        self.server_type = ServerType.Pod
        self.service_summaries: Dict[int:Dict] = None
        self.account_unencrypted_private_key_file: str = None

    def load_secrets(self, password: str = None):
        '''
        Loads the secrets used by the podserver
        '''
        self.account.load_secrets()

        # We use the account secret as client TLS cert for outbound
        # requests and as private key for the TLS server
        filepath = self.account.tls_secret.save_tmp_private_key()

        config.requests.cert = (
            self.account.tls_secret.cert_file, filepath
        )

    def get_registered_services(self):
        '''
        Downloads a list of service summaries
        '''

        network = self.network

        url = network.paths.get(Paths.NETWORKSERVICES_API)
        response = RestApiClient.call(url)

        if response.status_code == 200:
            summaries = response.json()
            self.network.service_summaries = dict()
            for summary in summaries.get('service_summaries', []):
                self.network.service_summaries[summary['service_id']] = summary
            _LOGGER.debug(
                f'Read summaries for {len(self.network.service_summaries)} '
                'services'
            )
        else:
            _LOGGER.debug(
                'Failed to retrieve list of services from the network: '
                f'HTTP {response.status_code}'
            )