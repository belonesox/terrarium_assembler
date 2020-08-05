"""Top-level package for casket-assembler."""

__author__ = """Stas Fomin"""
__email__ = 'stas-fomin@yandex.ru'
__version__ = '0.1.0'

# from ..utils import *
# from ta import *
# from nuitka import *

# import pkgutil

# __all__ = []
# for loader, module_name, is_pkg in  pkgutil.walk_packages(__path__):
#     __all__.append(module_name)
#     _module = loader.find_module(module_name).load_module(module_name)
#     globals()[module_name] = _module


#import importlib
#import pkgutil

from .ca import *
from .nuitkaflags import *
from .utils import *
