"""
    All nuitka functions associated for TA
"""

import os
import sys
from setuptools import find_packages
from pkgutil import iter_modules

import dataclasses as dc
import importlib
import pathlib
import re

PACKAGES_DIRS = [
os.getcwd(), 
# '/opt/venvdm/lib64/python3.8/site-packages/', 
# '/opt/venvdm/src',
'/usr/lib/python3.8/site-packages/',
'/usr/lib64/python3.8/site-packages/',
]


def find_modules(path):
    if not path:
        return None
        
    modules = set()
    rootdir, base_package_name = os.path.split(path)

    def add_modules4pkg(pkg):
        modules.add(pkg)
        pkgpath = path + '/' + pkg.replace('.', '/')
        if sys.version_info.major == 2 or (sys.version_info.major == 3 and sys.version_info.minor < 6):
            for _, name, ispkg in iter_modules([pkgpath]):
                if not ispkg:
                    modules.add(pkg + '.' + name)
        else:
            for info in iter_modules([pkgpath]):
                if not info.ispkg:
                    modules.add(pkg + '.' + info.name)
        pass

    for info in iter_modules([path]):
        if not info.ispkg:
            if info.name not in ['__main__', 'setup']:
                modules.add(info.name)

    for pkg in find_packages(path):
        add_modules4pkg(pkg)
    return modules


def dir4module(modname):
    try:
        mod = importlib.__import__(modname)
    except:    
        return None
    finally:
        if modname in sys.modules:
            del sys.modules[modname]
        import gc
        gc.collect()    

    return str(pathlib.Path(mod.__file__).resolve().parent)


def dir4mnode(target_):
    module = target_.module
    module_dir = None
    if "folder" in target_:
        module_dir = target_.folder   
    else:    
        module_dir = dir4module(module)
    return module_dir


def flags4module(modname, module_dir, block_modules=None):
    # modnames_ = [modname]
    mods = sorted(find_modules(module_dir)) 
    disabled_re = None
    if block_modules:
        disabled_re_str = '('  + '|'.join([s.replace('.', '\.') for s in block_modules]) + ')'
        # print(disabled_re_str)
        disabled_re = re.compile(disabled_re_str)

    flags = []
    for mod in mods:
        beforename, lastname = os.path.splitext(modname  + '.' + mod)
        if not lastname[1:2].isdigit():
            firstname = mod.split('.')[0] 
            if 'migrations' in mod.split('.'):
                continue
            if firstname not in ['tests'] and lastname[1:] not in ['tests']:
                modname_ = mod
                if modname  != firstname:
                    modname_ = modname  + '.' + mod
                if disabled_re and disabled_re.match(modname_):
                    flags.append(' --nofollow-import-to ' + modname_  )
                else:
                    flags.append(' --include-module ' + modname_  )

    flags.append("--module  %s" % module_dir)
    return flags



@dc.dataclass
class NuitkaProfile:
    '''
    All about Nuitka Profile — what projects shoud be build with soem flags
    '''
    inherited: object = None # Nuitka profile to inherit all the staff.
    builds: list = None # utilities to build 
    force_packages: list = None  # force packages to include
    force_modules: list = None # force modules to include
    block_packages: list = None # disable packages
    flags: list = ('show-progress', 'show-scons')  # base flags
    inherit: str = ''

    def get_base_flags(self):
        '''
        Get list of flags, using inheritance.

        Failed by recursion, if deps loop exists.
        '''
        flags = []
        if self.inherited:
            flags = self.inherited.get_base_flags()

        if self.flags:
            for it_ in self.flags:
                flags.append(f'--{it_}')
        if self.force_packages:
            for it_ in self.force_packages:
                flags.append('--include-package=' + it_)
        if self.force_modules:
            for it_ in self.force_modules:
                flags.append('--include-module=' + it_)
        if self.block_packages:
            for it_ in self.block_packages:
                flags.append('--nofollow-import-to=' + it_)

        return flags


    def get_flags(self, out_dir, target_):
        '''
        Get flags for Nuitka compiler
        '''
        block_modules = None
        if block_modules in target_:
            block_modules = target_.block_modules

        flags_ = self.get_base_flags()

        flags = ("""
            %s --output-dir="%s"    
        """ % (" ".join([''] + flags_), out_dir)).strip().split("\n")        

        # if "module" in target_:
        #     '''
        #     Компиляция модулей пока не работает.
        #     '''    
        #     module_dir = dir4mnode(target_)
        #     if not module_dir:
        #         return ''
        #     flags += flags4module(target_.module, module_dir, block_modules)
        # else:
        if 1:
            flags.append('--standalone') 
            flags.append('--follow-imports') 
            if "modules" in target_:
                for it_ in target_.modules:
                    flags.append('--nofollow-import-to=' + it_)

            if 'force_modules' in target_:
                for it_ in target_.force_modules:
                    flags.append('--include-module=' + it_)

        return " ".join(flags)


if __name__ == '__main__':
    print(dir4module('ansible'))
#     flags4module

@dc.dataclass
class NuitkaProfiles:
    '''
    Dictionary of 
    '''
    profiles_spec: dict 

    def __post_init__(self):    
        '''
        Process profiles specs to profiles objects.
        '''
        self.profiles = {}
        for name, spec in self.profiles_spec.items():
            inherit_profile = None
            if 'inherit' in spec:
                inherit_profile = self.profiles[spec.inherit]

            np_ = NuitkaProfile(inherited=inherit_profile, **spec)
            self.profiles[name] = np_
        pass            

if __name__ == '__main__':
    print(dir4module('ansible'))
#     flags4module