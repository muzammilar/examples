# tests/test_math_util.py
import pytest
from app.math_util import add, multiply

# Test the add function
def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

# Test the multiply function
def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(-1, 1) == -1
    assert multiply(0, 1) == 0
