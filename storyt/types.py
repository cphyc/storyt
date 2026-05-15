import cloudpickle
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator


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
