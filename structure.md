# Database structure

## concept
- id
- name
- parent_id: concept.id | null
- timestamp

`concept` defines _what_ your data are made of (simulations, outputs, catalogues, ...)

## concept_instance
- id
- concept_id: concept.id
- parent_id: concept_instance.id

`concept_instance` defines their instances. If you have outputs, each output will have a corresponding `concept_instance` entry.

## resource
- id
- name: string
- parent_id: resource.id
- concept_id: concept.id
- kind: re | glob | function
- source_code: str
- hash: str

`resource` defines how one maps `concept` onto files. Each resource has the ability to generate a list of files, each of them being an instance of the resource (see below).
They are linked to a concept to know what these resource represent (a given concept can have multiple resources).
Their source code is stored together with a hash of it, so that, if the user changes the resource's source code, it can be registered and modified accordingly.


## resource_instance
- id
- resource_id: resource.id
- parent_id: resource_instance.id (nullable)
- concept_instance_id: concept_instance.id
- path: str
- timestamp

`resource_instance` are actual paths on drive. They are linked to a `resource` as well as an actual `concept_instance`. A given concept instance can have multiple resources instance.
Their timestamp allows storyt to know whether their children resource instances and product instances should be updated.

## product
- id
- name: string
- resource_id: resource.id
- source_code: str
- hash: str
- timestamp

A `product` represents how to transform a resource into some product (e.g., a plot). Calling their source code once per resource instance generates one product_instance (see below).

## product_instance
- id
- product_id: product.id
- resource_instance_id: resource_instance.id
- timestamp
- content: blob

`product_instance`s represent actual products. They are wired to a `product` (to describe how they were generated) and a `resource_instance` (to describe what resource they were generated from).
They are essentially the result of calling `product.source_code` once per `resource_instance`.

# On lazy loading

Entries in the database should essentially not be modified. However, if a change in concept, resource or product is detected, a new version is created and the old one is implictly invalidated (along with all its children) but it isn't removed so that you can roll back.
When resource instancing is triggered, storyt first looks up the file on hard drive. If it finds new instances or detects that one of them is newer than the timestamp, it calls its children recursively. Otherwise, it doesn't do anything and just returns, assuming that the cihldren are up-to-date.
