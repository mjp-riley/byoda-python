#!/usr/bin/env python3

'''
Test case for YouTube import of assets and their metadata

For importing video and audio tracks, BenTo4 needs to be installed
under /podserver/bento4: https://www.bento4.com/downloads/

'''
import os
import sys
import shutil
import unittest

from datetime import datetime, timezone

from byoda.datamodel.network import Network
from byoda.datamodel.account import Account

from byoda.datatypes import IngestStatus
from byoda.datatypes import IdType

from byoda.requestauth.jwt import JWT

from byoda.datastore.data_store import DataStore

from byoda.data_import.youtube import YouTube

from byoda.storage.filestorage import FileStorage

from byoda.util.api_client.api_client import ApiClient

from byoda.servers.pod_server import PodServer

from byoda.util.logger import Logger

from byoda import config

from tests.lib.setup import setup_network
from tests.lib.setup import setup_account
from tests.lib.setup import mock_environment_vars

from tests.lib.defines import ADDRESSBOOK_SERVICE_ID
from tests.lib.defines import MODTEST_FQDN, MODTEST_APP_ID

_LOGGER = None

TEST_DIR = '/tmp/byoda-tests/yt-import'

TEST_YOUTUBE_VIDEO_ID: str = '5Y9L5NBINV4'

API_KEY_FILE: str = 'tests/collateral/local/youtube-data-api.key'


class TestFileStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            shutil.rmtree(TEST_DIR)
        except FileNotFoundError:
            pass

        os.makedirs(TEST_DIR)

        asset_dir: str = f'{TEST_DIR}/tmp/{TEST_YOUTUBE_VIDEO_ID}'
        try:
            shutil.rmtree(asset_dir)
        except FileNotFoundError:
            pass

        shutil.copytree('tests/collateral/local/video_asset', asset_dir)

        mock_environment_vars(TEST_DIR)
        network_data = await setup_network(delete_tmp_dir=False)

        config.test_case = 'TEST_CLIENT'

        config.server.account: Account = await setup_account(network_data)

        os.environ[YouTube.ENVIRON_CHANNEL] = ''
        os.environ[YouTube.ENVIRON_API_KEY] = ''

    @classmethod
    async def asyncTearDown(self):
        await ApiClient.close_all()

    async def test_scrape_videos(self):
        account: Account = config.server.account
        await account.load_memberships()
        member = account.memberships.get(ADDRESSBOOK_SERVICE_ID)

        server: PodServer = config.server
        data_store: DataStore = server.data_store
        storage_driver: FileStorage = server.storage_driver
        network: Network = server.network

        channel: str = 'Dathes'
        # channel: str = 'History Matters'
        os.environ[YouTube.ENVIRON_CHANNEL] = f'{channel}:ALL'

        yt = YouTube()
        ingested_videos = await YouTube.load_ingested_videos(
            member.member_id, data_store
        )
        self.assertEqual(len(ingested_videos), 0)

        ingested_videos = {
            '2BqKA3DOilk': {
                'ingest_status': IngestStatus.PUBLISHED
            },
            'OD08BC26QaM': {
                'ingest_status': IngestStatus.EXTERNAL
            },
        }
        await yt.get_videos(ingested_videos)
        self.assertGreaterEqual(len(yt.channels[channel].videos), 1)

        jwt = JWT.create(
            member.member_id, IdType.MEMBER, member.data_secret, network.name,
            ADDRESSBOOK_SERVICE_ID, IdType.APP, MODTEST_APP_ID,
            expiration_days=3
        )
        mod_url = f'https://{MODTEST_FQDN}'
        mod_api_url: str = mod_url + YouTube.MODERATION_REQUEST_API
        mod_claim_url: str = mod_url + YouTube.MODERATION_CLAIM_URL
        await yt.persist_videos(
            member, data_store, storage_driver, ingested_videos,
            moderate_request_url=mod_api_url,
            moderate_jwt_header=jwt.encoded,
            moderate_claim_url=mod_claim_url
        )

        ingested_videos = await YouTube.load_ingested_videos(
            member.member_id, data_store
        )
        self.assertGreaterEqual(len(ingested_videos), 2)

        # Start with clean slate
        yt = YouTube()

        await yt.get_videos(ingested_videos)

        await yt.persist_videos(
            member, data_store, storage_driver, ingested_videos
        )

    async def test_import_videos(self):
        _LOGGER.info('Disabled API import tests')
        return

        account: Account = config.server.account
        await account.load_memberships()
        member = account.memberships.get(ADDRESSBOOK_SERVICE_ID)

        server: PodServer = config.server
        data_store: DataStore = server.data_store
        storage_driver: FileStorage = server.storage_driver

        with open(API_KEY_FILE, 'r') as file_desc:
            api_key = file_desc.read().strip()

        os.environ[YouTube.ENVIRON_API_KEY] = api_key
        os.environ[YouTube.ENVIRON_CHANNEL] = 'Dathes'
        yt = YouTube()

        already_ingested_videos = await YouTube.load_ingested_videos(
            member.member_id, data_store
        )
        self.assertEqual(len(already_ingested_videos), 0)

        already_ingested_videos = {
            '2BqKA3DOilk': {
                'ingest_status': IngestStatus.PUBLISHED,
                'published_timestamp': datetime.now(timezone.utc)
            },
            'OD08BC26QaM': {
                'ingest_status': IngestStatus.EXTERNAL,
                'published_timestamp': datetime.now(timezone.utc)
            },
        }

        await yt.get_videos(already_ingested_videos)

        await yt.persist_videos(
            member, data_store, storage_driver, already_ingested_videos
        )

        ingested_videos = await YouTube.load_ingested_videos(
            member.member_id, data_store
        )

        # We are not ingesting A/V tracks in this test so only
        # expect 1 ingested video
        self.assertEqual(len(ingested_videos), 1)

        # Start with clean slate
        yt = YouTube()

        await yt.get_videos(ingested_videos)

        await yt.persist_videos(
            member, data_store, storage_driver, already_ingested_videos
        )


if __name__ == '__main__':
    _LOGGER = Logger.getLogger(sys.argv[0], debug=True, json_out=False)
    unittest.main()