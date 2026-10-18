"""Hardware vendors for the `specs` module.

Each file in this package defines a Vendor subclass. To add a vendor, drop a
new file here with a class that sets `keyword` and implements `lookup`; the
specs module finds it automatically and adds `/bot <keyword> <serial>`.
"""
