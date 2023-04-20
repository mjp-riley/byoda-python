'''
The generic PubSub classes from which tech-specific classes should
derive

:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2021, 2022, 2023
:license    : GPLv3
'''

import logging

from typing import TypeVar

import pynng

from byoda.datamodel.pubsub_message import PubSubMessage

from byoda.datatypes import PubSubTech

_LOGGER = logging.getLogger(__name__)

SchemaDataItem = TypeVar('SchemaDataItem')
Schema = TypeVar('Schema')


class PubSub:
    def __init__(self, connection_string: str, data_class: SchemaDataItem,
                 schema: Schema, is_sender: bool):
        self.connection_string: str = connection_string
        self.schema: Schema = schema
        self.service_id: int = schema.service_id
        self.is_sender: bool = is_sender

        self.data_class: SchemaDataItem = data_class
        self.pub: pynng.Pub0 | None = None
        self.subs: list[pynng.Sub0] = []

    @staticmethod
    def setup(connection_string: str, data_class: SchemaDataItem,
              service_id: int, is_counter: bool = False,
              is_sender: bool = False,
              pubsub_tech: PubSubTech = PubSubTech.NNG):
        '''
        Factory for PubSub
        '''

        if pubsub_tech == PubSubTech.NNG:
            from .pubsub_nng import PubSubNng

            return PubSubNng(
                data_class, service_id, is_counter,
                is_sender
            )
        else:
            raise ValueError(
                f'Unknown PubSub tech {pubsub_tech}: {connection_string}'
            )

    @staticmethod
    def get_connection_string() -> str:
        '''
        Returns the connection string
        '''

        raise NotImplementedError

    @staticmethod
    def cleanup():
        '''
        Cleans up any resources
        '''

        raise NotImplementedError