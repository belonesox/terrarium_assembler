"""
    All nuitka associated for
    Casket Assembler
"""

import dataclasses as dc

@dc.dataclass
class NuitkaFlags:
    '''
    Just make observable flags for Nuitka compiler
    '''
    builds: list  # utilities to build 
    force_packages: list # force packages to include
    force_modules: list  # force modules to include
    block_packages: list # disable packages
    std_flags: list = ('show-progress', 'show-scons', 'standalone')  # base flags

    def get_flags(self, out_dir):
        '''
        Get flags for Nuitka compiler
        '''
        flags = ("""
            %s --output-dir="%s"    
        """ % (" --".join([''] + self.std_flags), out_dir)).strip().split("\n")        
        for it_ in self.force_packages:
            flags.append('--include-package=' + it_)
        for it_ in self.force_modules:
            flags.append('--include-module=' + it_)
        for it_ in self.block_packages:
            flags.append('--recurse-not-to=' + it_)

        return " ".join(flags)


