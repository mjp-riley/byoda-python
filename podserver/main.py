'''
POD server for Bring Your Own Data and Algorithms

The podserver relies on podserver/bootstrap.py to set up
the account, its secrets, restoring the database files
from the cloud storage, registering the pod and creating
the nginx configuration files for the account and for
existing memberships.

Suported environment variables:
CLOUD: 'AWS', 'LOCAL'
PRIVATE_BUCKET
RESTRICTED_BUCKET
PUBLIC_BUCKET
NETWORK
ACCOUNT_ID
ACCOUNT_SECRET
PRIVATE_KEY_SECRET: secret to protect the private key
LOGLEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL
ROOT_DIR: where files need to be cached (if object storage is used) or stored

:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2021, 2022, 2023
:license    : GPLv3
'''

import os
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI

from byoda.datamodel.network import Network
from byoda.datamodel.account import Account

from byoda.datatypes import CloudType

from byoda.datastore.document_store import DocumentStoreType
from byoda.datastore.data_store import DataStoreType
from byoda.datastore.cache_store import CacheStoreType

from byoda.storage.pubsub_nng import PubSubNng

from byoda.servers.pod_server import PodServer

from byoda.util.fastapi import setup_api, update_cors_origins

from podserver.util import get_environment_vars

from byoda.util.logger import Logger

from byoda import config

from .routers import account as AccountRouter
from .routers import member as MemberRouter
from .routers import authtoken as AuthTokenRouter
from .routers import status as StatusRouter
from .routers import accountdata as AccountDataRouter
from .routers import content_token as ContentTokenRouter

_LOGGER: Logger | None = None
LOG_FILE: str = os.environ.get('LOGDIR', '/var/log/byoda') + '/pod.log'

DIR_API_BASE_URL = 'https://dir.{network}/api'


@asynccontextmanager
async def lifespan(app: FastAPI):

    # HACK: Deletes files from tmp directory. Possible race condition
    # with other process so we do it right at the start
    PubSubNng.cleanup()

    network_data = get_environment_vars()

    server: PodServer = PodServer(
        bootstrapping=bool(network_data.get('bootstrap'))
    )

    config.server = server

    # Remaining environment variables used:
    server.custom_domain = network_data['custom_domain']
    server.shared_webserver = network_data['shared_webserver']

    debug: bool = network_data.get('debug', False)
    if debug and str(debug).lower() in ('true', 'debug', '1'):
        config.debug = True
        # Make our files readable by everyone, so we can
        # use tools like call_data_api.py to debug the server
        os.umask(0o0000)
    else:
        os.umask(0x0077)

    logfile: str = network_data.get('logfile')
    global _LOGGER
    _LOGGER = Logger.getLogger(
        sys.argv[0], json_out=config.debug, debug=config.debug,
        loglevel=network_data['loglevel'], logfile=logfile
    )

    _LOGGER.debug(
        f'Setting up logging: debug {config.debug}, '
        f'loglevel {network_data["loglevel"]}, logfile {logfile}'
    )

    await server.set_document_store(
        DocumentStoreType.OBJECT_STORE,
        cloud_type=CloudType(network_data['cloud']),
        private_bucket=network_data['private_bucket'],
        restricted_bucket=network_data['restricted_bucket'],
        public_bucket=network_data['public_bucket'],
        root_dir=network_data['root_dir']
    )

    network = Network(network_data, network_data)
    await network.load_network_secrets()

    server.network = network
    server.paths = network.paths

    account = Account(network_data['account_id'], network)
    account.password = network_data.get('account_secret')

    await account.load_secrets()

    server.account = account

    await server.set_data_store(
        DataStoreType.SQLITE, account.data_secret
    )

    await server.set_cache_store(CacheStoreType.SQLITE)

    await server.get_registered_services()

    cors_origins: set[str] = set(
        [
            f'https://proxy.{network.name}',
            f'https://{account.tls_secret.common_name}'
        ]
    )

    if server.custom_domain:
        cors_origins.add(f'https://{server.custom_domain}')

    await account.load_memberships()

    for member in account.memberships.values():
        await member.enable_data_apis(
            app, server.data_store, server.cache_store
        )

        await member.tls_secret.save(
            password=member.private_key_password,
            storage_driver=server.local_storage,
            overwrite=True
        )
        await member.data_secret.save(
            password=member.private_key_password,
            storage_driver=server.local_storage,
            overwrite=True
        )

        cors_origins.add(f'https://{member.tls_secret.common_name}')

    _LOGGER.debug(
        f'Tracing to {config.trace_server}'
    )

    _LOGGER.debug('Lifespan startup complete')
    update_cors_origins(cors_origins)

    yield

    _LOGGER.info('Shutting down pod server')


config.trace_server: str = os.environ.get('TRACE_SERVER', config.trace_server)

app = setup_api(
    'BYODA pod server', 'The pod server for a BYODA network',
    'v0.0.1', [
        AccountRouter, MemberRouter, AuthTokenRouter, StatusRouter,
        AccountDataRouter, ContentTokenRouter
    ],
    lifespan=lifespan, trace_server=config.trace_server,
)

config.app = app
