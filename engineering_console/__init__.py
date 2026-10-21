import logging

__version__ = "3.0.1"

# Library-style default: nothing is printed unless logsetup.setup() installs the log file handler.
logging.getLogger(__name__).addHandler(logging.NullHandler())
