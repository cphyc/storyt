import hashlib
import inspect
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from pathlib import Path
from textwrap import indent
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
    validates,
)
from sqlalchemy.sql import func
from sqlalchemy.types import PickleType

from storyt.types import CloudPickleType, ConceptResource, ResourceKind

if TYPE_CHECKING:
    from storyt.story import Recorder


class Base(MappedAsDataclass, DeclarativeBase):
    pass


class Concept(Base):
    """Represents a concept in the knowledge graph.
    Concepts can have a parent-child relationship."""

    __tablename__ = "concept"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)

    recorder: "Recorder"

    timestamp: Mapped[datetime] = mapped_column(insert_default=func.now(), default=None)

    # Relations
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("concept.id"), default=None
    )
    parent: Mapped[Optional["Concept"]] = relationship(
        "Concept", remote_side="Concept.id", back_populates="children", default=None
    )
    children: Mapped[list["Concept"]] = relationship(
        "Concept", back_populates="parent", default_factory=list
    )
    instances: Mapped[list["ConceptInstance"]] = relationship(
        "ConceptInstance", back_populates="concept", default_factory=list
    )
    resources: Mapped[list["Resource"]] = relationship(
        "Resource", back_populates="concept", default_factory=list
    )

    def __repr__(self) -> str:
        if self.parent:
            return f"<Concept '{self.name}', parent={self.parent.name}>"
        else:
            return f"<Concept '{self.name}' ROOT>"

    def add_child(self, name: str):
        child = self.recorder.Concept(name=name, parent=self)
        return child

    def add_resource(self, name: str, path: Path):
        """Add a resource associated to this concept.

        Note
        ----
        The number of items will determine the number of
        instances of this resource.
        """

        resource = self.recorder.Resource(
            name=name, concept=self, source_code=str(path), kind=ResourceKind.PATH
        )
        return resource


class ConceptInstance(Base):
    """Represents an instance of a concept."""

    __tablename__ = "concept_instance"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concept.id"))
    name: Mapped[str] = mapped_column(unique=True)

    concept: Mapped["Concept"] = relationship("Concept", back_populates="instances")

    recorder: "Recorder"

    # Relations
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("concept_instance.id"), default=None
    )
    parent: Mapped[Optional["ConceptInstance"]] = relationship(
        "ConceptInstance",
        remote_side="ConceptInstance.id",
        back_populates="children",
        default=None,
    )
    children: Mapped[list["ConceptInstance"]] = relationship(
        "ConceptInstance", back_populates="parent", default_factory=list
    )
    resource_instances: Mapped[list["ResourceInstance"]] = relationship(
        "ResourceInstance", back_populates="concept_instance", default_factory=list
    )

    def __repr__(self) -> str:
        return f"<ConceptInstance '{self.name}' concept={self.concept.name}>"


class Resource(Base):
    """Represents a resource associated to a concept."""

    __tablename__ = "resource"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str]
    source_code: Mapped[str]
    hash: Mapped[str] = mapped_column(init=False)
    kind: Mapped[ResourceKind]

    recorder: "Recorder"

    # Relations
    concept: Mapped["Concept"] = relationship("Concept", back_populates="resources")
    concept_id: Mapped[int] = mapped_column(ForeignKey("concept.id"), default=None)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource.id"), default=None
    )
    parent: Mapped[Optional["Resource"]] = relationship(
        "Resource", remote_side="Resource.id", back_populates="children", default=None
    )
    children: Mapped[list["Resource"]] = relationship(
        "Resource", back_populates="parent", default_factory=list
    )
    instances: Mapped[list["ResourceInstance"]] = relationship(
        "ResourceInstance", back_populates="resource", default_factory=list
    )
    products: Mapped[list["Product"]] = relationship(
        "Product", back_populates="resource", default_factory=list
    )

    # Constaints:
    # - resource name & concept are unique
    # - concept.parent == parent.concept (if parent is not None)
    __table_args__ = (UniqueConstraint("name", "concept_id"),)

    def __post_init__(self):
        self.hash = hashlib.md5(self.source_code.encode()).hexdigest()

    def __repr__(self) -> str:
        return f"<Resource '{self.name}' kind={self.kind.value} | concept={self.concept.name} | parent={self.parent.name} >"

    @validates("parent_id", "concept_id")
    def validate_concept_hierarchy(self, key, value):
        if self.parent and self.concept:
            parent_concept = self.parent.concept
            if parent_concept.id != self.concept.parent_id:
                raise ValueError(
                    f"resource.parent.concept ({parent_concept}) must equal "
                    f"resource.concept.parent ({self.concept.parent})"
                )
        return value

    def add_product(self, name: str | None = None):
        """Register a product of this resource."""

        def wrapper(func):
            if name is None:
                name_ = func.__name__
            else:
                name_ = name

            @wraps(func)
            def inner(*args, **kwargs):
                return func(*args, **kwargs)

            product = self.recorder.Product(name=name_, resource=self, function=inner)

            return product

        return wrapper

    def __gt__(self, concept: "Concept") -> "ConceptResource":
        """Syntactic sugar for creating a new resource that is the
         child of this one and associated to concept
        to a concept."""
        return ConceptResource(
            concept=concept,
            resource=self,
            recorder=self.recorder,
        )

    def discover(self):
        """Discover instances of oneself"""
        ...


class ResourceInstance(Base):
    """Represents an instance of a resource."""

    __tablename__ = "resource_instance"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    concept_instance_id: Mapped[int] = mapped_column(ForeignKey("concept_instance.id"))
    path: Mapped[str]

    recorder: "Recorder"

    # Relations
    resource: Mapped["Resource"] = relationship("Resource", back_populates="instances")
    concept_instance: Mapped["ConceptInstance"] = relationship(
        "ConceptInstance", back_populates="resource_instances"
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource_instance.id"), default=None
    )
    parent: Mapped[Optional["ResourceInstance"]] = relationship(
        "ResourceInstance",
        remote_side="ResourceInstance.id",
        back_populates="children",
        default=None,
    )
    children: Mapped[list["ResourceInstance"]] = relationship(
        "ResourceInstance", back_populates="parent", default_factory=list
    )
    product_instances: Mapped[list["ProductInstance"]] = relationship(
        "ProductInstance", back_populates="resource_instance", default_factory=list
    )

    timestamp: Mapped[datetime] = mapped_column(insert_default=func.now(), default=None)

    def __repr__(self) -> str:
        return f"<ResourceInstance '{self.name}' resource={self.resource.name} parent={self.parent.name} concept_instance={self.concept_instance_id}>"


class Product(Base):
    """Represents a product of a resource instance."""

    __tablename__ = "product"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    function: Mapped[Callable] = mapped_column(CloudPickleType)
    hash: Mapped[str] = mapped_column(init=False)
    resource: Mapped["Resource"] = relationship("Resource", back_populates="products")

    recorder: "Recorder"

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"), default=None)
    instances: Mapped[list["ProductInstance"]] = relationship(
        "ProductInstance", back_populates="product", default_factory=list
    )
    source_code: Mapped[str] = mapped_column(default=None)
    timestamp: Mapped[datetime] = mapped_column(insert_default=func.now(), default=None)

    def __post_init__(self):
        self.source_code = inspect.getsource(self.function)
        self.hash = hashlib.md5(self.source_code.encode()).hexdigest()

    def __repr__(self) -> str:
        ret = f"<Product '{self.name}' resource='{self.resource.name}' source_code=\n"
        ret += indent(self.source_code, " | ")
        ret += ">"
        return ret

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)


class ProductInstance(Base):
    """Represents an instance of a product."""

    __tablename__ = "product_instance"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    resource_instance_id: Mapped[int] = mapped_column(
        ForeignKey("resource_instance.id")
    )
    content: Mapped[Any] = mapped_column(PickleType)

    recorder: "Recorder"

    product: Mapped["Product"] = relationship("Product", back_populates="instances")
    resource_instance: Mapped["ResourceInstance"] = relationship(
        "ResourceInstance", back_populates="product_instances"
    )

    timestamp: Mapped[datetime] = mapped_column(insert_default=func.now(), default=None)

    def __repr__(self) -> str:
        return f"<ProductInstance '{self.name}' product={self.product}, resource_instance={self.resource_instance}>"
