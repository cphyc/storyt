import hashlib
import inspect
from collections.abc import Callable
from typing import Any


class Property:
    def __init__(
        self,
        name: str,
        fn: Callable,
        serializer: str = "pickle",
        requires: list[str] | None = None,
        reader: str | None = None,
    ):
        self.name = name
        self.fn = fn
        self.serializer = serializer
        self.requires: list[str] = requires or []
        self.reader = reader

    def source_hash(self) -> str:
        try:
            src = inspect.getsource(self.fn)
        except (OSError, TypeError):
            try:
                code = self.fn.__code__
                src = str(code.co_consts) + str(code.co_varnames) + str(code.co_code)
            except AttributeError:
                src = str(id(self.fn))
        src = f"{src}|reader={self.reader}|requires={','.join(self.requires)}"
        return hashlib.sha256(src.encode()).hexdigest()

    def compute(self, instance: Any, dep_values: tuple = ()) -> Any:
        """Call fn with instance or configured reader value plus dependencies."""
        first_arg = instance if self.reader is None else instance.reader[self.reader]
        if dep_values:
            return self.fn(first_arg, *dep_values)
        return self.fn(first_arg)
