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

@dc.dataclass
class PythonRebuildProfile:
    '''
    Specs how to rebuild python packages
    '''
    inherited: object = None # PythonRebuildProfile profile to inherit all the staff.
    env: dict = None # ENVIRONMENT VARIABLES to CUSTOMIZE BUILD
    files: dict = None # List of configuration files to create
    inherit: str = ''
    packages: list = None # utilities to build 
    command: str = "python setup.py bdist_wheel --build-number=99zzz "
    libs: list = None
    pip: list = None


    def get_merged_env(self):
        '''
        Get merged environment, using inheritance.
        '''
        env = {}
        if self.inherited:
            env = self.inherited.get_merged_env()
        if self.env:    
            for k, v in self.env.items():
                base_ = ''
                if k in env:
                    base_ = env[k]
                if base_:
                    base_ += ' '    
                env[k] = base_ + str(v)
        return env


    def get_build_command(self):
        '''
        Get build command for python package, using inheritance.
        '''
        env = self.get_merged_env()
        env_str = " ".join([f"{k}='{v}'" for k, v in env.items()])
        scmd  = f'''{env_str} {self.command}'''
        return scmd

    def get_list_of_libs_dir(self):
        '''
        Get list of symlinked dirs to system libs in /lib64 directory
        '''
        res = []
        for p_ in self.packages:
            for l_ in self.libs or []:
                if l_ == '$package':
                    l_ = p_
                l_ = l_.replace('-', '_')    
                res.append(l_)
        return res        

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



@dc.dataclass
class PythonRebuildProfiles:
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

            np_ = PythonRebuildProfile(inherited=inherit_profile, **spec)
            self.profiles[name] = np_
        pass            

    def get_list_of_pip_packages_to_rebuild(self):    
        plist_ = []
        for _, profile in self.profiles.items():
            plist_.extend(profile.packages)
        return " ".join(plist_)

    def get_list_of_pip_packages_to_install(self):    
        plist_ = []
        for _, profile in self.profiles.items():
            plist_.extend(profile.pip or [])
        return " ".join(plist_)


    def get_commands_to_build_packages(self):    
        plist_ = []
        for _, profile in self.profiles.items():
            command_ = profile.get_build_command()
            for pp in profile.packages:
                yield pp, command_, profile.files
