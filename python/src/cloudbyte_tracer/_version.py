"""The installed SDK version, read once from package metadata.

Kept in its own module so the transport can read it without importing the package's __init__ (which imports the transport).
"""
from importlib.metadata import PackageNotFoundError, version

try:
    SDK_VERSION = version("cloudbyte-tracer")
except PackageNotFoundError:  # imported from a source checkout that was never installed
    SDK_VERSION = "0+unknown"
