from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import cloudpickle
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from storyt.db import Concept, Resource
    from storyt.story import Recorder


class CloudPickleType(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Python -> DB: serialize on write"""
        if value is not None:
            return cloudpickle.dumps(value)

    def process_result_value(self, value, dialect):
        """DB -> Python: deserialize on read"""
        if value is not None:
            return cloudpickle.loads(value)


class ResourceKind(Enum):
    RE = "re"
    GLOB = "glob"
    PATH = "path"
    # FUNCTION = "function"


@dataclass
class ConceptResource:
    "TODO"

    concept: "Concept"
    resource: "Resource"
    recorder: "Recorder"

    def glob(self, pattern: str, *, name: str | None = None):
        name = name or pattern
        return self.recorder.Resource(
            name=name,
            source_code=pattern,
            kind=ResourceKind.GLOB,
            concept=self.concept,
            parent=self.resource,
        )

    def re(self, pattern: str, *, name: str | None = None):
        name = name or pattern
        return self.recorder.Resource(
            name=name,
            source_code=pattern,
            kind=ResourceKind.RE,
            concept=self.concept,
            parent=self.resource,
        )
