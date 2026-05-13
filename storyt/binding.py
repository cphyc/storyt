from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .asset import StaticAsset


def bind(*members: tuple[StaticAsset, str]):
    """
    Bind assets together on shared key names.

    Each argument is a (StaticAsset, key_name) tuple.  Assets that share the
    same key value via this binding can later be resolved with
    ``instance.bound(name)``.
    """
    binding = list(members)
    for asset, _key in members:
        # Store the same list object on every participating asset so that
        # _collect_and_register_bindings can deduplicate by object identity.
        if binding not in asset._bindings:
            asset._bindings.append(binding)
