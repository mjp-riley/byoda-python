'''
Class for modeling an account on a network

:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2021
:license    : GPLv3
'''

import logging

from uuid import uuid4, UUID
from copy import copy
from typing import Dict, TypeVar, Callable

from fastapi import FastAPI

from strawberry.types import Info
from strawberry.fastapi import GraphQLRouter

from byoda.servers import PodServer

from byoda.datatypes import CsrSource
from byoda.datatypes import IdType
from byoda.datatypes import StorageType

from byoda.datamodel.service import Service
from byoda.datamodel.memberdata import MemberData
from byoda.datamodel.schema import Schema, SignatureType

from byoda.datastore.document_store import DocumentStore

from byoda.storage import FileStorage

from byoda.secrets import ServiceDataSecret
from byoda.secrets import MemberSecret, MemberDataSecret
from byoda.secrets import Secret, MembersCaSecret

from byoda.util import Paths

from byoda.util import NginxConfig
from byoda.util import NGINX_SITE_CONFIG_DIR

from byoda import config
from byoda.util.api_client import RestApiClient
from byoda.util.api_client.restapi_client import HttpMethod


_LOGGER = logging.getLogger(__name__)

Account = TypeVar('Account')
Network = TypeVar('Network')


class Member:
    '''
    Class for modelling an Membership.

    This class is expected to only be used in the podserver
    '''

    def __init__(self, service_id: int, account: Account) -> None:
        '''
        Constructor
        '''

        self.member_id: UUID = None
        self.service_id: int = int(service_id)

        self.account: Account = account
        self.network: Network = self.account.network

        self.data: MemberData = None

        self.paths: Paths = copy(self.network.paths)
        self.paths.account_id = account.account_id
        self.paths.account = account.account
        self.paths.service_id = self.service_id

        self.document_store: DocumentStore = self.account.document_store
        self.storage_driver: FileStorage = self.document_store.backend

        self.private_key_password = account.private_key_password

        # The FastAPI app. We store this value to support upgrades of schema
        self.app: FastAPI = None

        if self.service_id not in self.network.services:
            # Here we read the service contract as currently published
            # by the service, which may differ from the one we have
            # previously accepted
            filepath = self.paths.service_file(self.service_id)
            try:
                self.service = Service.get_service(
                    self.network, filepath=filepath
                )
                self.service.verify_schema_signatures()
            except FileNotFoundError:
                # if the service contract is not yet available for
                # this membership then it should be downloaded at
                # a later point
                _LOGGER.info(
                    f'Service contract file {filepath} does not exist'
                )
                self.service = Service(
                    self.network, service_id=self.service_id,
                )

                self.service.download_data_secret(save=True, failhard=False)

            self.network.services[self.service_id] = self.service

        # This is the schema a.k.a data contract that we have previously
        # accepted, which may differ from the latest schema version offered
        # by the service
        try:
            self.schema: Schema = self.load_schema()
        except FileNotFoundError:
            # We do not have the schema file for a service that the pod did
            # not join yet
            pass

        self.service = self.network.services[service_id]

        # We need the service data secret to verify the signature of the
        # data contract we have previously accepted
        self.service_data_secret = ServiceDataSecret(
            None, service_id, self.network
        )
        if self.service_data_secret.cert_file_exists():
            self.service_data_secret.load(with_private_key=False)
        else:
            self.download_data_secret(save=True)
            self.service_data_secret.load(with_private_key=False)

        self.tls_secret = None
        self.data_secret = None

    def as_dict(self) -> Dict:
        '''
        Returns the metdata for the membership, complying with the
        MemberResponseModel
        '''

        if not self.schema:
            raise ValueError('Schema not available')

        data = {
            'account_id': self.account.account_id,
            'network': self.network.name,
            'member_id': self.member_id,
            'service_id': self.service_id,
            'version': self.schema.version,
            'name': self.schema.name,
            'owner': self.schema.owner,
            'website': self.schema.website,
            'supportemail': self.schema.supportemail,
            'description': self.schema.description,
            'certificate': self.tls_secret.cert_as_pem(),
            'private_key': self.tls_secret.private_key_as_pem(),
        }

        return data

    def create_nginx_config(self):
        '''
        Generates the Nginx virtual server configuration for
        the membership
        '''

        if not self.member_id:
            self.load_secrets()

        self.tls_secret.save_tmp_private_key()

        nginx_config = NginxConfig(
            directory=NGINX_SITE_CONFIG_DIR,
            filename='virtualserver.conf',
            identifier=self.member_id,
            subdomain=f'{IdType.MEMBER.value}{self.service_id}',
            cert_filepath=(
                self.paths.root_directory() + '/' + self.tls_secret.cert_file
            ),
            key_filepath=self.tls_secret.unencrypted_private_key_file,
            alias=self.network.paths.account,
            network=self.network.name,
            public_cloud_endpoint=self.paths.storage_driver.get_url(
                StorageType.PUBLIC
            ),
            port=PodServer.HTTP_PORT,
            root_dir=config.server.network.paths.root_directory()
        )

        nginx_config.create()
        nginx_config.reload()

    @staticmethod
    def create(service: Service, schema_version: int,
               account: Account, members_ca: MembersCaSecret = None):
        '''
        Factory for a new membership
        '''

        member = Member(service.service_id, account)
        member.member_id = uuid4()

        member.create_secrets(members_ca=members_ca)

        if not member.paths._exists(member.paths.SERVICE_FILE):
            filepath = member.paths.get(member.paths.SERVICE_FILE
                                        )
        # TODO: make this more user-friendly by attempting to download
        # the specific version of a schema
        if not members_ca:
            # If members_ca has a value then we were called by a test
            # case and should not attempt to download the schema
            member.service.download_schema(
                save=True, filepath=member.paths.get(Paths.MEMBER_SERVICE_FILE)
            )

        member.schema = member.load_schema()

        if (member.schema.version != schema_version):
            raise ValueError(
                f'Downloaded schema for service_id {service.service_id} '
                f'has version {member.schema.version} instead of version '
                f'{schema_version} as requested'
            )

        member.tls_secret = MemberSecret(
            member.member_id, member.service_id, member.account
        )
        member.data_secret = MemberDataSecret(
            member.member_id, member.service_id, member.account
        )

        member.create_secrets(members_ca=members_ca)

        member.data_secret.create_shared_key()

        member.data = MemberData(
            member, member.paths, member.document_store
        )

        member.data.save_protected_shared_key()

        filepath = member.paths.get(member.paths.MEMBER_SERVICE_FILE)
        member.schema.save(filepath, member.paths.storage_driver)

        return member

    def update_schema(self, version: int):
        '''
        Download the latest version of the schema, disables the old version
        of the schema and enables the new version

        :raises: HTTPException
        '''

        if not self.service:
            raise ValueError(
                'Member instance does not have a service associated'
            )

    def create_secrets(self, members_ca: MembersCaSecret = None) -> None:
        '''
        Creates the secrets for a membership
        '''

        if self.tls_secret and self.tls_secret.cert_file_exists():
            self.tls_secret = MemberSecret(
                None, self.service_id, self.account
            )
            self.tls_secret.load(
                with_private_key=True, password=self.private_key_password
            )
            self.member_id = self.tls_secret.member_id
        else:
            self.tls_secret = self._create_secret(MemberSecret, members_ca)

        if self.data_secret and self.data_secret.cert_file_exists():
            self.data_secret = MemberDataSecret(
                self.member_id, self.service_id, self.account
            )
            self.data_secret.load(
                with_private_key=True, password=self.private_key_password

            )
        else:
            self.data_secret = self._create_secret(
                MemberDataSecret, members_ca
            )

    def _create_secret(self, secret_cls: Callable, issuing_ca: Secret
                       ) -> Secret:
        '''
        Abstraction for creating secrets for the Member class to avoid
        repetition of code for creating the various member secrets of the
        Service class

        :param secret_cls: callable for one of the classes derived from
        byoda.util.secrets.Secret
        :param issuing_ca: ca to sign the cert locally, instead of requiring
        the service to sign the cert request
        :raises: ValueError, NotImplementedError
        '''

        if not self.member_id:
            raise ValueError(
                'Member_id for the account has not been defined'
            )

        secret = secret_cls(
            self.member_id, self.service_id, account=self.account
        )

        if secret.cert_file_exists():
            raise ValueError(
                f'Cert for {type(secret)} for service {self.service_id} and '
                f'member {self.member_id} already exists'
            )

        if secret.private_key_file_exists():
            raise ValueError(
                f'Private key for {type(secret)} for service {self.service_id}'
                f' and member {self.member_id} already exists'
            )

        if not issuing_ca:
            if secret_cls != MemberSecret and secret_cls != MemberDataSecret:
                raise ValueError(
                    f'No issuing_ca was provided for creating a '
                    f'{type(secret_cls)}'
                )
            else:
                csr = secret.create_csr()
                payload = {'csr': secret.csr_as_pem(csr).decode('utf-8')}

                resp = RestApiClient.call(
                    Paths.SERVICEMEMBER_API, HttpMethod.POST,
                    data=payload, service_id=self.service_id
                )
                if resp.status_code != 201:
                    raise RuntimeError('Certificate signing request failed')

                cert_data = resp.json()
                secret.from_string(
                    cert_data['signed_cert'], certchain=cert_data['cert_chain']
                )
        else:
            csr = secret.create_csr()
            issuing_ca.review_csr(csr, source=CsrSource.LOCAL)
            certchain = issuing_ca.sign_csr(csr)
            secret.from_signed_cert(certchain)

        secret.save(password=self.private_key_password)

        return secret

    def load_secrets(self):
        '''
        Loads the membership secrets
        '''

        self.tls_secret = MemberSecret(
            None, self.service_id, self.account
        )
        self.tls_secret.load(
            with_private_key=True, password=self.private_key_password
        )
        self.member_id = self.tls_secret.member_id

        self.data_secret = MemberDataSecret(
            self.member_id, self.service_id, self.account
        )
        self.data_secret.load(
            with_private_key=True, password=self.private_key_password
        )

    def register(self):
        '''
        Registers the membership and its schema version with both the network
        and the service
        '''

        # Call the member API of the service
        RestApiClient.call(
            Paths.SERVICEMEMBER_API +
            f'/service_id/{self.service_id}/version/{self.schema.version}',
            secret=self.tls_secret,
            service_id=self.service_id
        )
        _LOGGER.debug(
            f'Member {self.member_id} registered with service '
            f'{self.service_id}'
        )

        RestApiClient.call(
            self.paths.get(Paths.NETWORKMEMBER_API), secret=self.tls_secret,
            service_id=self.service_id
        )

        _LOGGER.debug(
            f'Member {self.member_id} registered service {self.service_id} '
            f' with network {self.network.name}'
        )

    def load_schema(self) -> Schema:
        '''
        Loads the schema for the service that we're loading the membership for
        '''
        filepath = self.paths.get(self.paths.MEMBER_SERVICE_FILE)

        if self.storage_driver.exists(filepath):
            schema = Schema.get_schema(
                filepath, self.storage_driver,
                service_data_secret=self.service.data_secret,
                network_data_secret=self.network.data_secret,
            )
        else:
            _LOGGER.exception(
                f'Service contract file {filepath} does not exist for the '
                'member'
            )
            raise FileNotFoundError(filepath)

        self.verify_schema_signatures(schema)
        schema.generate_graphql_schema()

        return schema

    def enable_graphql_api(self, app: FastAPI):
        '''
        Loads the GraphQL API in the FastApi app.
        '''

        self.app = app

        path = f'/api/v1/data/service-{self.service_id}'
        graphql_app = GraphQLRouter(self.schema.gql_schema)
        app.include_router(graphql_app, prefix=path)

    def upgrade_graphql_api(self, app: FastAPI):
        '''
        Updates the GraphQL interface for a membership

        This is not yet implemented as FastAPI has no method for
        replacing an existing route in its list of FastAPI.routes
        '''

        # TODO: implement upgrades of schemas
        # path = f'/api/v1/data/service-{self.service_id}'
        # graphql_app = GraphQLRouter(self.schema.gql_schema)

        # This uses internal data structures of FastAPI as there
        # does not seem to be a supported method to replace an
        # route
        # for route in [r for r in self.app.routes if r.path == path]:
        #    route.endpoint = graphql_app.__init__().get_graphiql

        raise NotImplementedError(
            'Updating a schema is not supported, best short term solution '
            'seems to restart the FastAPI app'
        )

    def verify_schema_signatures(self, schema: Schema):
        '''
        Verify the signatures for the schema, a.k.a. data contract

        :raises: ValueError
        '''

        if not schema.signatures[SignatureType.SERVICE.value]:
            raise ValueError('Schema does not contain a service signature')

        if not schema.signatures[SignatureType.NETWORK.value]:
            raise ValueError('Schema does not contain a network signature')

        if not self.service.data_secret or not self.service.data_secret.cert:
            service = Service(
                self.network, service_id=self.service_id,
                storage_driver=self.storage_driver
            )
            service.download_data_secret(save=True)

        schema.verify_signature(
            self.service.data_secret, SignatureType.SERVICE
        )

        _LOGGER.debug(
            f'Verified service signature for service {self.service_id}'
        )

        schema.verify_signature(
            self.network.data_secret, SignatureType.NETWORK
        )

        _LOGGER.debug(
            f'Verified network signature for service {self.service_id}'
        )

    def load_data(self):
        '''
        Loads the data stored for the membership
        '''

        self.data.load()

    def save_data(self, data):
        '''
        Saves the data for the membership
        '''

        self.data.save()

    @staticmethod
    def get_data(service_id, info: Info) -> Dict:
        '''
        Extracts the requested data field.

        This function is called from the Strawberry code generated by the
        jsonschema-to-graphql converter
        '''

        if not info.path:
            raise ValueError('Did not get value for path parameter')

        if info.path.typename != 'Query':
            raise ValueError(
                f'Got graphql invocation for "{info.path.typename}" '
                f'instead of "Query"'
            )

        _LOGGER.debug(
            f'Got graphql invocation for {info.path.typename} '
            f'for object {info.path.key}'
        )

        server = config.server
        member = server.account.memberships[service_id]
        member.load_data()

        return member.data.get(info.path.key)

    @staticmethod
    def set_data(service_id, info: Info) -> None:
        '''
        Sets the provided data

        :param service_id: Service ID for which the GraphQL API was called
        :param path: the GraphQL path variable that shows the path taken
        through the GraphQL data model
        :param mutation: the instance of the Mutation<Object> class
        '''

        if not info.path:
            raise ValueError('Did not get value for path parameter')

        if info.path.typename != 'Mutation':
            raise ValueError(
                f'Got graphql invocation for "{info.path.typename}"" '
                f'instead of "Query"'
            )

        _LOGGER.debug(
            f'Got graphql invocation for {info.path.typename} '
            f'for object {info.path.key}'
        )

        server = config.server
        member = server.account.memberships[service_id]

        # Any data we may have in memory may be stale when we run
        # multiple processes so we always need to load the data
        member.load_data()

        # We do not modify existing data as it will need to be validated
        # by JSON Schema before it can be accepted.
        data = copy(member.data)

        # By convention implemented in the Jinja template, the called mutate
        # 'function' starts with the string 'mutate' so we to find out
        # what mutation was invoked, we want what comes after it.
        class_object = info.path.key[len('mutate'):].lower()

        # Gets the data included in the mutation
        mutate_data: Dict = info.selected_fields[0].arguments

        # Get the properties of the JSON Schema, we don't support
        # nested objects just yet
        schema = member.schema
        schema_properties = schema.json_schema['jsonschema']['properties']

        # The query may be for an object for which we do not yet have
        # any data
        if class_object not in member.data:
            member.data[class_object] = dict()

        properties = schema_properties[class_object].get('properties', {})

        for key in properties.keys():
            if properties[key]['type'] == 'object':
                raise ValueError(
                    'We do not support nested objects yet: %s', key
                )
            if properties[key]['type'] == 'array':
                raise ValueError(
                    'We do not support arrays yet'
                )
            if key.startswith('#'):
                _LOGGER.debug(
                    'Skipping meta-property %s in schema for service %s',
                    key, member.service_id
                )
                continue

            member.data[class_object][key] = mutate_data[key]

        member.save_data(data)

        return member.data