import os
from setuptools import find_packages
from setuptools import setup

#NOTE: Using setup.py is the legacy format. It's better to use setup.ini
# since that avoids the circular dependency issue for wheel building

PACKAGE_NAME = "funkpkg"
LICENSE_NAME = "MIT"

def version():
    if "PKG_VERSION" in os.environ and os.environ["PKG_VERSION"]:
        return os.environ["PKG_VERSION"]
    return "0.0.1-0"

setup(
    name=PACKAGE_NAME,
    version=version(),
    description="Function Mapping Example",
    long_description="""Fake Long Description.""",
    url="https://example.com",
    author="Muzammil Abdul Rehman",
    author_email="foo@bar.com",
    license=LICENSE_NAME,
    classifiers=[
        "Development Status :: 1",
        "Programming Language :: Python :: 3",
    ],
    install_requires=[
        "requests>2.18,<3.0"
    ],
    package_dir={"funcmapper": "funcmapper"},
    packages=find_packages(),
    include_package_data=True,
    data_files=[],
    entry_points={
        "console_scripts": [
            "funk=funcmapper.funk:main",
        ]
    },
)
