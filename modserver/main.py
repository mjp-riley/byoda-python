'''
Proof of Concept moderation server for Bring Your Own Data and Algorithms

:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2023
:license    : GPLv3
'''

import os
import sys
import yaml

from contextlib import asynccontextmanager

from fastapi import FastAPI

from byoda.datamodel.network import Network

from byoda.datastore.document_store import DocumentStoreType

from byoda.datatypes import CloudType
from byoda.datatypes import ClaimStatus

from byoda.servers.app_server import AppServer

from byoda.util.fastapi import setup_api

from byoda.util.logger import Logger

from byoda import config

from .routers import status as StatusRouter
from .routers import moderate as ModerateRouter

_LOGGER = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_file = os.environ.get('CONFIG_FILE', 'config.yml')
    with open(config_file) as file_desc:
        app_config = yaml.load(file_desc, Loader=yaml.SafeLoader)

    debug = app_config['application']['debug']
    verbose = not debug
    global _LOGGER
    _LOGGER = Logger.getLogger(
        sys.argv[0], debug=debug, verbose=verbose,
        logfile=app_config['appserver'].get('logfile')
    )

    os.makedirs(app_config['appserver']['whitelist_dir'], exist_ok=True)
    claim_dir = app_config['appserver']['claim_dir']
    for status in ClaimStatus:
        os.makedirs(f'{claim_dir}/{status.value}', exist_ok=True)

    network = Network(
        app_config['appserver'], app_config['application']
    )

    server = AppServer(app_config['appserver']['app_id'], network, app_config)

    await server.set_document_store(
        DocumentStoreType.OBJECT_STORE,
        cloud_type=CloudType.LOCAL,
        private_bucket='byoda',
        restricted_bucket='byoda',
        public_bucket='byoda',
        root_dir=app_config['appserver']['root_dir']
    )

    config.server = server

    await network.load_network_secrets()

    await server.load_secrets(
        password=app_config['appserver']['private_key_password']
    )

    if not os.environ.get('SERVER_NAME') and config.server.network.name:
        os.environ['SERVER_NAME'] = config.server.network.name

    _LOGGER.debug('Lifespan startup complete')

    yield

    _LOGGER.info('Shutting down server')

app = setup_api(
    'BYODA directory server', 'The directory server for a BYODA network',
    'v0.0.1', [StatusRouter, ModerateRouter],
    lifespan=lifespan
)

config.app = app