"""A list of functions to be mapped.

Every function takes plain string arguments (as they would come from a config
file or the command line) and returns a human readable description string.
"""


def rails(name: str, length: str) -> str:
    """Describe a rail of a given length."""
    return f"rails: '{name}' with length {length}"


def cylinders(name: str, length: str, diameter: str = None) -> str:
    """Describe a cylinder. The diameter is optional."""
    description = f"cylinders: '{name}' with length {length}"
    if diameter:
        description += f" and diameter {diameter}"
    return description


def oranges(**kwargs) -> str:
    """Describe a bag of oranges using arbitrary keyword arguments."""
    if not kwargs:
        return "oranges: none"
    details = ", ".join(f"{key}={value}" for key, value in sorted(kwargs.items()))
    return f"oranges: {details}"
