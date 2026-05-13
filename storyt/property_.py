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
    ):
        self.name = name
        self.fn = fn
        self.serializer = serializer
        self.requires: list[str] = requires or []

    def source_hash(self) -> str:
        try:
            src = inspect.getsource(self.fn)
        except (OSError, TypeError):
            try:
                code = self.fn.__code__
                src = str(code.co_consts) + str(code.co_varnames) + str(code.co_code)
            except AttributeError:
                src = str(id(self.fn))
        return hashlib.sha256(src.encode()).hexdigest()

    def compute(self, instance: Any, dep_values: tuple = ()) -> Any:
        """Call fn(instance[, dep1, dep2, ...]) with resolved dependency values."""
        if dep_values:
            return self.fn(instance, *dep_values)
        return self.fn(instance)
