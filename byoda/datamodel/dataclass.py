'''
Class for data classes defined in the JSON Schema used
for generating the GraphQL Strawberry code based on Jinja2
templates


:maintainer : Steven Hessing <steven@byoda.org>
:copyright  : Copyright 2021, 2022, 2023
:license    : GPLv3
'''

import orjson
import logging
from enum import Enum
from copy import copy
from uuid import UUID
from urllib.parse import urlparse, ParseResult
from datetime import datetime, timezone
from typing import TypeVar


from byoda.datatypes import RightsEntityType
from byoda.datatypes import DataOperationType
from byoda.datatypes import DataType
from byoda.datatypes import MARKER_ACCESS_CONTROL

from byoda.storage.pubsub import PubSub

from byoda import config

from .dataaccessright import DataAccessRight


_LOGGER = logging.getLogger(__name__)

RequestAuth = TypeVar('RequestAuth')
Member = TypeVar('Member')
Schema = TypeVar('Schema')

# We create a number of standard APIs for each class to manipulate data.
class GraphQlAPI(Enum):
    # flake8: noqa=E221
    MUTATE    = 'mutate'
    APPEND    = 'append'
    SEARCH    = 'search'
    DELETE    = 'delete'

class Property(Enum):
    # flask8: noqa=E221
    COUNTER     = 'counter'

# Translation from jsondata data type to Python data type in the Jinja template
PYTHON_SCALAR_TYPE_MAP = {
    DataType.STRING: 'str',
    DataType.INTEGER: 'int',
    DataType.NUMBER: 'float',
    DataType.BOOLEAN: 'bool',
    DataType.DATETIME: 'datetime',
    DataType.UUID: 'UUID',
}

GRAPHQL_SCALAR_TYPE_MAP = {
    DataType.STRING: 'String',
    DataType.INTEGER: 'Int',
    DataType.NUMBER: 'Float',
    DataType.BOOLEAN: 'Boolean',
    DataType.DATETIME: 'DateTime',
    DataType.UUID: 'UUID',
}

MARKER_PROPERTIES = '#properties'

class SchemaDataItem:
    '''
    Class used to model the 'data classes' defined in the JSON Schema.
    The class is used in the Jinja2 templates to generate python3
    code leveraging the Strawberry GraphQL module.

    A data 'class' here can be eiter an object/dict, array/list or scalar
    '''

    def __init__(self, class_name: str, schema_data: dict[str:object], schema: Schema) -> None:
        '''
        Constructor

        :param class_name: name of the class
        :param schema: Schema instance
        :param schema_data: json-schema blurb for the class
        '''

        self.name: str | None = class_name
        self.schema_data: dict[str, object] = schema_data
        self.description: str | None = schema_data.get('description')
        self.item_id: str | None = schema_data.get('$id')
        self.schema_id: str = schema.schema_id
        self.service_id: int = schema.service_id
        self.schema_url: ParseResult = urlparse(schema.schema_id)
        self.enabled_apis: set = set()

        # Is this a class referenced by other classes
        self.defined_class: bool | None = None

        self.fields: list[SchemaDataItem] | None = None
        self.properties: set(Property) = set()

        # Properties for the class, currently only used by SchemaDataScalar
        for property in schema_data.get(MARKER_PROPERTIES, []):
            self.properties.add(Property(property))

        # Currently only used for SchemaDataScalar instances, to keep
        # counter per unique value of the item in an SchemaDataArray
        self.is_counter: bool = False

        self.type: DataType = DataType(schema_data['type'])

        # Used by SchemaDataArray to point to the class the entries
        # of the array have
        self.referenced_class: str = None

        self.python_type, self.graphql_type = self.get_types(
            class_name, self.schema_data
        )

        # The class for storing data for the service sets the values
        # for storage_name and storage_type for child data items
        # under the root data item
        self.storage_name: str = None
        self.storage_type: str = None

        # The Pub/Sub for communicating changes to data using this class
        # instance. Only used for SchemaDataArray instances
        self.pubsub_class: PubSub | None = None

        self.access_rights: list[DataAccessRight] = {}

        self.parse_access_controls()

    def get_types(self, data_name: str, schema_data: dict) -> tuple[str, str]:
        '''
        Returns translation of the jsonschema -> python typing string
        and of the jsonschema -> graphql typing string

        :param name: name of the data element
        :param subschema: json-schema blurb for the data element
        :returns: the Python typing value and the GraphQL typing value for
        the data element
        :raises: ValueError, KeyError
        '''

        js_type = schema_data.get('type')
        if not js_type:
            raise ValueError(f'Class {data_name} does not have a type defined')

        try:
            jsonschema_type = DataType(js_type)
        except KeyError:
            raise ValueError(
                f'Data class {data_name} is of unrecognized'
                f'data type: {jsonschema_type}'
            )

        if jsonschema_type not in (DataType.OBJECT, DataType.ARRAY):
            try:
                format = schema_data.get('format')
                if format and format.lower() in ('date-time', 'uuid'):
                    format_datatype = DataType(format)
                    python_type: str = PYTHON_SCALAR_TYPE_MAP[format_datatype]
                    graphql_type: str = GRAPHQL_SCALAR_TYPE_MAP[format_datatype]
                else:
                    python_type: str = PYTHON_SCALAR_TYPE_MAP[jsonschema_type]
                    graphql_type: str = GRAPHQL_SCALAR_TYPE_MAP[jsonschema_type]
            except KeyError:
                raise ValueError(
                    f'No GraphQL data type mapping for {jsonschema_type}'
                )

            return python_type, graphql_type
        elif jsonschema_type == DataType.ARRAY:
            items = schema_data.get('items')
            if not items:
                raise ValueError(
                    f'Array {data_name} does not have items defined'
                )

            if 'type' in items:
                python_type = f'List[{PYTHON_SCALAR_TYPE_MAP[DataType(items["type"])]}]'
                graphql_type = f'[{GRAPHQL_SCALAR_TYPE_MAP[DataType(items["type"])]}!]'
                return python_type, graphql_type
            elif '$ref' in items:
                if not items['$ref'].startswith('https') and items['$ref'].count('/') != 2:
                    raise ValueError(
                        f'Reference for {data_name} must follow format '
                        f' of "/schema/{data_name}"'
                    )
                class_reference = items['$ref'].split('/')[-1]
                python_type = f'List[{class_reference}]'
                graphql_type = f'[{class_reference}!]'
                return python_type, graphql_type
        elif jsonschema_type == DataType.OBJECT:
            return None, None

        raise ValueError(
            f'Unknown data type for {data_name}: {jsonschema_type}'
        )

    @staticmethod
    def create(class_name: str, schema_data: dict, schema: Schema, classes: dict = None):
        '''
        Factory for instances of classes derived from SchemaDataItem
        '''

        item_type = schema_data.get('type')
        if not item_type:
            raise ValueError(f'No type found in {class_name}')

        _LOGGER.debug(
            f'Creating data class instance for {class_name} '
            f'for type {item_type}'
        )

        if item_type == 'object':
            item = SchemaDataObject(
                class_name, schema_data, schema)
        elif item_type == 'array':
            item = SchemaDataArray(
                class_name, schema_data, schema, classes=classes
            )
        else:
            item = SchemaDataScalar(class_name, schema_data, schema)

        return item

    def normalize(self, value: str | int | float) -> str | int | float:
        '''
        Normalizes the value to the correct data type for the item
        '''

        return value

    def parse_access_controls(self) -> None:
        '''
        Parse the #accesscontrol key of the data item in the JSON Schema
        '''

        _LOGGER.debug(f'Parsing access controls for {self.name}')

        rights = self.schema_data.get(MARKER_ACCESS_CONTROL)
        if not rights:
            _LOGGER.debug(f'No access rights defined for {self.name}')
            return

        if not isinstance(rights, dict):
            raise ValueError(
                f'Access controls must be an object for class {self.name}'
            )

        self.access_rights: dict[RightsEntityType, list[DataAccessRight]] = {}

        for entity_type_data, access_rights_data in rights.items():
            entity_type, access_rights = DataAccessRight.get_access_rights(
                entity_type_data, access_rights_data
            )
            self.access_rights[entity_type] = access_rights

            permitted_actions = [
                 access_right.data_operation
                 for access_right in access_rights
            ]

            for data_operation in permitted_actions:
                if data_operation in (
                        DataOperationType.CREATE,
                        DataOperationType.UPDATE):
                    self.enabled_apis.add(GraphQlAPI.MUTATE)
                if data_operation == DataOperationType.APPEND:
                    self.enabled_apis.add(GraphQlAPI.APPEND)
                if data_operation == DataOperationType.DELETE:
                    self.enabled_apis.add(GraphQlAPI.DELETE)
                if data_operation == DataOperationType.SEARCH:
                    self.enabled_apis.add(GraphQlAPI.SEARCH)

    async def authorize_access(self, operation: DataOperationType,
                               auth: RequestAuth, service_id: int, depth: int
                               ) -> bool | None:
        '''
        Checks whether the entity performing the request has access for
        the requested operation to the data item

        :param operation: requested operation
        :param auth: the authenticated requesting entity
        :param service_id: service_id for membership that received the request
        :returns: None if no determination was made, otherwise True or False
        '''

        _LOGGER.debug(f'Checking authorization for operation {operation}')
        if service_id != auth.service_id:
            _LOGGER.debug(
                f'GraphQL API for service ID {service_id} called with credentials '
                f'for service: {auth.service_id}'
            )
            return False

        if not self.access_rights:
            # No access rights for the data element so can't decide
            # whether access is allowed or not
            _LOGGER.debug(
                f'No access controls defined for data item {self.name}'
            )
            return None

        for access_rights in self.access_rights.values():
            for access_right in access_rights:
                result = await access_right.authorize(
                    auth, service_id, operation, depth
                )
                if result:
                    return True

        _LOGGER.debug(f'No access controls matched for data item {self.name}')

        return None

class SchemaDataScalar(SchemaDataItem):
    def __init__(self, class_name: str, schema_data: dict, schema: Schema) -> None:
        super().__init__(class_name, schema_data, schema)

        self.defined_class: bool = False
        self.format: str = None

        if self.type == DataType.STRING:
            self.format: str = self.schema_data.get('format')
            if self.format == 'date-time':
                self.type = DataType.DATETIME
                self.python_type = 'datetime'
            elif (self.format == 'uuid' or self.schema_data.get('regex') ==
                    (
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}'
                        '-[0-9a-f]{12}$'
                    )):
                self.type = DataType.UUID
                self.python_type = 'UUID'

        self.is_counter = Property.COUNTER in self.properties

        if (self.is_counter and not (self.type == DataType.UUID or
                self.type == DataType.STRING)):
            _LOGGER.exception(
                f'Only UUIDs and strings can be counters: {self.name}, '
                f'{self.type}'
            )
            raise ValueError('Only UUIDs and strings can be counters')

        _LOGGER.debug(
            f'Created scalar class {self.name} of type {self.type} with '
            f'format {self.format} and python type {self.python_type}'
        )

    def normalize(self, value: str | int | float) -> str | int | float:
        '''
        Normalizes the value to the correct data type for the item
        '''

        if (self.type == DataType.UUID
                and value and not isinstance(value, UUID)):
            result = UUID(value)
        elif (self.type == DataType.DATETIME
                and value and not isinstance(value, datetime)):
            if isinstance(value, str):
                result = datetime.fromisoformat(value)
            else:
                result = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            result = value

        return result

class SchemaDataObject(SchemaDataItem):
    def __init__(self, class_name: str, schema_data: dict, schema: Schema) -> None:
        super().__init__(class_name, schema_data, schema)

        # 'Defined' classes are objects under the '$defs' object
        # of the JSON Schema. We don't create GraphQL mutations for
        # named classes. We require all these 'defined' classes to
        # be defined locally in the schema and their id
        # thus starts with '/schemas/' instead of 'https://'. Furthermore,
        # we require that there no further '/'s in the id

        self.fields: dict[str, SchemaDataItem] = {}
        self.required_fields: list[str] = schema_data.get('required', [])
        self.defined_class: bool = False

        if self.item_id:
            self.defined_class = True

        for field, field_properties in schema_data['properties'].items():
            if field_properties['type'] == 'object':
                raise ValueError(
                    f'Nested objects or arrays under object {class_name} are '
                    'not yet supported'
                )
            elif field_properties['type'] == 'array':
                items = field_properties.get('items')
                if not items:
                    raise ValueError(
                        f'Array for {class_name} does not specify items'
                    )
                if not isinstance(items, dict):
                    raise ValueError(
                        f'Items property of array {class_name} must be an '
                        'object'
                    )

            item = SchemaDataItem.create(field, field_properties, schema)

            self.fields[field] = item

        _LOGGER.debug(f'Created object class {class_name}'
                      )
    def normalize(self, value: dict) -> dict:
        '''
        Normalizes the values in a dict
        '''

        data = copy(value)
        for field in data:
            if field == 'remote_member_id':
                if isinstance(data[field], str):
                    # special handling for 'remote_member_id', which is a
                    # parameter used for remote appends
                    data[field] = UUID(data[field])
            elif field != 'depth':
                data_class = self.fields[field]
                data[field] = data_class.normalize(value[field])

        return data

    async def authorize_access(self, operation: DataOperationType,
                               auth: RequestAuth, service_id: int, depth: int
                               ) -> bool | None:
        '''
        Checks whether the entity performing the request has access for the
        requested operation to the data item

        :param operation: requested operation
        :param auth: the authenticated requesting entity
        :returns: None if no determination was made, otherwise True or False
        '''

        access_allowed: bool | None = await super().authorize_access(
            operation, auth, service_id, depth
        )

        if access_allowed is False:
            return False

        for data_class in self.fields.values():
            child_access_allowed = await data_class.authorize_access(
                operation, auth, service_id, depth
            )
            _LOGGER.debug(
                f'Object child data access authorized: {child_access_allowed}'
            )

            if child_access_allowed is False:
                return False

        _LOGGER.debug(
            f'Object data access authorized: {access_allowed} for data '
            f'item {self.name}'
        )
        return access_allowed


class SchemaDataArray(SchemaDataItem):
    def __init__(self, class_name: str, schema_data: dict, schema: Schema,
                 classes: dict[str, SchemaDataItem]) -> None:
        super().__init__(class_name, schema_data, schema)

        self.defined_class: bool = False

        items = schema_data.get('items')
        if not items:
            raise ValueError(
                'Schema properties for array {class_name} does not have items '
                'defined'
            )

        if 'type' in items:
            # This is an array of scalars
            self.items = DataType(items['type'])
            self.referenced_class = SchemaDataItem.create(
                None, schema_data['items'], schema
            )
        elif '$ref' in items:
            # This is an array of objects of the referenced class
            self.items = DataType.REFERENCE
            reference = items['$ref']
            url = urlparse(reference)
            if not url.path.startswith('/schemas/'):
                raise ValueError(
                    f'Data reference {reference} must start with "/schemas/"'
                )
            if url.path.count('/') > 2:
                raise ValueError(
                    f'Data reference {reference} must have path with no more '
                    'than 2 "/"s'
                )

            referenced_class = reference.split('/')[-1]
            if referenced_class not in classes:
                raise ValueError(
                    f'Unknown class {referenced_class} referenced by {class_name}'
                )

            self.referenced_class = classes[referenced_class]

            # The Pub/Sub for communicating changes to data using this class
            # instance. We only track changes for arrays that reference
            # another class
            if config.test_case != "TEST_CLIENT":
                self.pubsub_class = PubSub.setup(
                    self.name, self, schema, is_sender=True
                )
        else:
            raise ValueError(
                f'Array {class_name} must have "type" or "$ref" defined'
            )

        _LOGGER.debug(
            f'Created array class {class_name} with referenced class '
            f'{self.referenced_class}'
        )

    def normalize(self, value: list) -> list:
        '''
        Normalizes the data structure in the array to the types defined in
        the service contract
        '''

        data = copy(value)

        result = []
        if self.referenced_class and type(self.referenced_class) == SchemaDataObject:
            # We need to normalize an array of objects
            items = data
        else:
            # We need to normalize an array of scalars, which are represented
            # in storage as a string of JSON
            if type(value) in (str, bytes):
                items = orjson.loads(value or '[]')
            else:
                items = value

        for item in items or []:
            if self.referenced_class:
                normalized_item = self.referenced_class.normalize(item)
            result.append(normalized_item)
        return result

    async def authorize_access(self, operation: DataOperationType,
                               auth: RequestAuth, service_id: int, depth: int
                               ) -> bool | None:
        '''
        Checks whether the entity performing the request has access for the
        requested operation to the data item

        :param operation: requested operation
        :param auth: the authenticated requesting entity
        :param service_id: the service ID of the service specified in the
        request
        :param depth: the level of recurssion specified in the request
        :returns: None if no determination was made, otherwise True or False
        '''

        access_allowed: bool | None = await super().authorize_access(
            operation, auth, service_id, depth
        )

        if access_allowed is False:
            _LOGGER.debug(
                f'Access is not authorized for {operation} for service {service_id}'
            )
            return False

        child_access_allowed = None
        if self.referenced_class:
            child_access_allowed = await self.referenced_class.authorize_access(
                operation, auth, service_id, depth
            )
            _LOGGER.debug(
                f'Child of array data access authorized: '
                f'{child_access_allowed} for data item {self.name}'
            )
            if child_access_allowed is False:
                return False

        _LOGGER.debug(
            f'Array data access authorized: {access_allowed} for data '
            f'item {self.name}'
        )

        return access_allowed
