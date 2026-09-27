"""Map function names (strings) to the actual callables."""

from funcmapper import functions as funks

maps = {
    "rails": funks.rails,
    "cylinders": funks.cylinders,
    "oranges": funks.oranges,
}


def call(name: str, *args, **kwargs) -> str:
    """Look up a function by name and call it with the given arguments.

    :raises KeyError: if no function is registered under ``name``
    """
    try:
        func = maps[name]
    except KeyError:
        raise KeyError(f"unknown function '{name}', expected one of {sorted(maps)}") from None
    return func(*args, **kwargs)
