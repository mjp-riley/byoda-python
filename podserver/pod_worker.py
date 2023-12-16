#!/usr/bin/env python3

'''
Manages recurring activities such as checking for new service contracts and
data secrets

Suported environment variables:
CLOUD: 'AWS', 'AZURE', 'GCP', 'LOCAL'
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

from uuid import UUID

from anyio import run
from anyio import sleep
from anyio import create_task_group
from anyio.abc import TaskGroup

from aioschedule import every, run_pending

from byoda.datamodel.network import Network
from byoda.datamodel.account import Account

from byoda.datatypes import CloudType

from byoda.datastore.data_store import DataStore
from byoda.datastore.data_store import DataStoreType
from byoda.datastore.cache_store import CacheStore
from byoda.datastore.cache_store import CacheStoreType

from byoda.datastore.document_store import DocumentStoreType

from byoda.data_import.youtube import YouTube
from byoda.data_import.twitter import Twitter

from byoda.util.updates_listener import UpdateListenerMember

from byoda.servers.pod_server import PodServer

from byoda.util.logger import Logger

from byoda import config

from podserver.util import get_environment_vars

from podserver.podworker.discovery import get_current_network_links
from podworker.discovery import listen_local_network_links_tables

from podworker.datastore_maintenance import backup_datastore
from podworker.datastore_maintenance import database_maintenance
from podworker.datastore_maintenance import refresh_cached_data
from podworker.datastore_maintenance import expire_cached_data

from podworker.twitter import run_twitter_startup_tasks
from podworker.twitter import twitter_update_task
from podworker.youtube import run_youtube_startup_tasks
from podworker.youtube import youtube_update_task

_LOGGER: Logger | None = None

LOGFILE: str = os.environ.get('LOGDIR', '/var/log/byoda') + '/worker.log'
ADDRESSBOOK_ID: int = 4294929430
YOUTUBE_IMPORT_SERVICE_ID: int = ADDRESSBOOK_ID
TWITTER_IMPORT_SERVICE_ID: int = ADDRESSBOOK_ID


async def main(argv):
    server = await setup_worker(argv)
    account: Account = server.account

    youtube_import_service_id: int = int(
        os.environ.get(
            'YOUTUBE_IMPORT_SERVICE_ID', YOUTUBE_IMPORT_SERVICE_ID
        )
    )
    twitter_import_service_id: int = TWITTER_IMPORT_SERVICE_ID

    await setup_recurring_tasks(server, youtube_import_service_id)

    listeners: dict[UUID, UpdateListenerMember] = await run_startup_tasks(
        server, server.data_store, youtube_import_service_id,
        twitter_import_service_id
    )

    task_group: TaskGroup
    async with create_task_group() as task_group:
        # listens to the network_links table from all
        # services that have 'listen relations' defined
        await listen_local_network_links_tables(
            account, listeners, task_group
        )

        listener: UpdateListenerMember
        for listener in listeners.values():
            task_group.start_soon(listener.get_updates)

        while True:
            try:
                await run_pending()
                await sleep(1)
            except Exception:
                _LOGGER.exception('Exception during run_pending')


async def run_startup_tasks(server: PodServer, data_store: DataStore,
                            youtube_import_service_id: int,
                            twitter_import_service_id: int
                            ) -> list[UpdateListenerMember]:
    '''
    Sets up data structures for the pod_worker

    :param server:
    :param data_store:
    :param youtube_import_service_id: The service to run the Youtube import on
    :param twitter_import_service-id: The service to run the Twitter import on
    '''

    _LOGGER.debug('Running pod_worker startup tasks')

    account: Account = server.account
    server.twitter_client = None

    updates_listeners: dict[UUID, UpdateListenerMember] = \
        await get_current_network_links(account, data_store)

    await run_youtube_startup_tasks(
        account, data_store, youtube_import_service_id
    )

    await run_twitter_startup_tasks(server, account, twitter_import_service_id)

    return updates_listeners


async def setup_recurring_tasks(server: PodServer,
                                youtube_import_service_id: int) -> None:
    account: Account = server.account
    data_store: DataStore = server.data_store
    cache_store: CacheStore = server.cache_store

    _LOGGER.debug('Scheduling task to update in-memory memberships')
    every(1).minutes.do(
        account.update_memberships, data_store, cache_store, False
    )

    _LOGGER.debug('Scheduling Database maintenance tasks')
    every(10).minutes.do(database_maintenance, server)

    _LOGGER.debug('Scheduling cache refresh task')
    every(30).minutes.do(refresh_cached_data, account, server)

    _LOGGER.debug('Scheduling cache expiration task')
    every(1).hour.do(expire_cached_data, server, cache_store)

    if server.cloud != CloudType.LOCAL:
        _LOGGER.debug('Scheduling backups of the datastore')
        interval: int = int(os.environ.get("BACKUP_INTERVAL", 240) or 240)
        every(interval).minutes.do(backup_datastore, server)

    if Twitter.twitter_integration_enabled():
        _LOGGER.debug('Scheduling twitter update task')
        every(180).seconds.do(twitter_update_task, server)

    if YouTube.youtube_integration_enabled():
        interval: int = int(
            os.environ.get('YOUTUBE_IMPORT_INTERVAL', 240) or 240
        )
        _LOGGER.debug(
            f'Scheduling youtube update task to run every {interval} minutes'
        )
        every(int(interval)).minutes.do(
            youtube_update_task, server, youtube_import_service_id
        )


async def setup_worker(argv: list[str]) -> PodServer:
    '''
    Initializes all the data bases and data structures

    :param argv: the command line arguments via 'sys.argv'
    :returns: the server object
    :raises: (none)
    '''

    data: dict[str, str] = get_environment_vars()

    debug = data.get('debug', False)
    if debug and str(debug).lower() in ('true', 'debug', '1'):
        config.debug = True
        # Make our files readable by everyone, so we can
        # use tools like call_data_api.py to debug the server
        os.umask(0o0000)
    else:
        os.umask(0x0077)

    global _LOGGER
    _LOGGER = Logger.getLogger(
        argv[0], json_out=True, debug=config.debug,
        loglevel=data.get('worker_loglevel', 'WARNING'), logfile=LOGFILE
    )
    _LOGGER.debug(f'Starting pod_worker {data["bootstrap"]}')

    try:
        server: PodServer = PodServer(
            cloud_type=CloudType(data['cloud']),
            bootstrapping=bool(data.get('bootstrap'))
        )
        config.server = server

        await server.set_document_store(
            DocumentStoreType.OBJECT_STORE, server.cloud,
            private_bucket=data['private_bucket'],
            restricted_bucket=data['restricted_bucket'],
            public_bucket=data['public_bucket'],
            root_dir=data['root_dir']
        )

        network: Network = Network(data, data)
        await network.load_network_secrets()

        server.network = network
        server.paths = network.paths

        account = Account(data['account_id'], network)
        await account.paths.create_account_directory()
        await account.load_secrets()

        server.account = account

        await server.set_data_store(
            DataStoreType.SQLITE, account.data_secret
        )

        await server.set_cache_store(CacheStoreType.SQLITE)

        await account.update_memberships(
            server.data_store, server.cache_store, False
        )
    except Exception:
        _LOGGER.exception('Exception during startup')
        raise

    return server


if __name__ == '__main__':
    run(main, sys.argv)
