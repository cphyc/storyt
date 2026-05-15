from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property, wraps

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from storyt.db import Concept, Product, Resource


@dataclass
class Recorder:
    session: Session
    story: "Story"

    @wraps(Concept)
    def Concept(self, *args, **kwargs) -> Concept:
        c = Concept(*args, **kwargs, recorder=self)
        self.session.add(c)
        return c

    @wraps(Resource)
    def Resource(self, *args, **kwargs) -> Resource:
        r = Resource(*args, **kwargs, recorder=self)
        self.session.add(r)
        return r

    @wraps(Product)
    def Product(self, *args, **kwargs) -> Product:
        p = Product(*args, **kwargs, recorder=self)
        self.session.add(p)
        return p


@dataclass
class Story:
    """Recorder is responsible for tracking the current story being told.

    It provides a context manager to manage the session and ensure that all
    operations are properly recorded in the database.
    """

    engine: Engine

    @cached_property
    def session(self) -> Session:
        """Create a new session for the story."""
        return Session(self.engine)

    @contextmanager
    def record(self) -> Recorder:
        """Context manager for managing the session."""
        try:
            yield Recorder(session=self.session, story=self)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
