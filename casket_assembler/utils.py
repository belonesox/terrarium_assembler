"""
    Different (geeky) utils for 
    Casket Assembler
"""
import os
import magic
import subprocess
import shutil
import stat
import pathlib
from easydict import EasyDict as edict

def mkdir_p(path):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)
    pass


def folder_size(path, *, follow_symlinks=False):
    '''
    Counting size of a folder. 
    '''
    try:
        if not os.path.exists(path):
            # Nonexistant folder has zero size.
            return 0
        it = list(os.scandir(path))
        # with os.scandir(path) as it:
        return sum(folder_size(entry, follow_symlinks=follow_symlinks) for entry in it)
    except NotADirectoryError:
        return os.stat(path, follow_symlinks=follow_symlinks).st_size

# def mp_magic_create_filemagic(mime_detected, type_detected):
#     '''
#     Temp monkeypatching for debugging error cases.
#     '''
#     from magic import FileMagic
#     try:
#         # mime_encoding = 'utf-8'
#         # mime_detected.split('; ')
#         mime_type, mime_encoding = mime_detected.split('; ')
#     except ValueError:
#         raise ValueError(mime_detected)

#     return FileMagic(name=type_detected, mime_type=mime_type,
#                      encoding=mime_encoding.replace('charset=', ''))


# magic._create_filemagic = mp_magic_create_filemagic

def wtf(f):
    '''
    For debugging purposes.
    '''
    for wtf_ in ['PYTEST', '/tests']:
        if wtf_ in f:
            return True
        

def yaml_load(filename):
   '''
   Load yaml file into edict. Hide edict deps.
   ''' 
   import yaml

   fc = None
   with open(filename, 'r') as f:
     fc = edict(yaml.safe_load(f))
   return fc


def fix_binary(path, libpath):
    '''
    Make "portable" Elf-binary or SO-library.

    Calling patchelf to set RUNPATH to given libpath.
    '''

    from tempfile import mkstemp

    fd_, patched_elf = mkstemp()
    shutil.copy2(path, patched_elf)
    
    orig_perm = stat.S_IMODE(os.lstat(path).st_mode)
    os.chmod(patched_elf, orig_perm | stat.S_IWUSR)         

    try:
        subprocess.check_call(['patchelf',
                               '--set-rpath',
                               libpath,
                               patched_elf])
    except Exception as ex_:
        print("Cannot patch ", path)
        raise ex_
        pass

    os.close(fd_)
    os.chmod(patched_elf, orig_perm)         
    return patched_elf

def git2dir(git_url, git_branch, path_to_dir):
    pdir = os.path.split(path_to_dir)[0]
    os.chdir(pdir)
    scmd = 'git --git-dir=/dev/null clone --single-branch --branch %(git_branch)s  --depth=1 %(git_url)s %(path_to_dir)s ' % vars()
    os.system(scmd)
    pass

def make_setup_if_not_exists():
    '''
    If python package without setup.py
    (for example Poetry)
    '''
    if not os.path.exists('setup.py') and os.path.exists('setup.cfg'):
        from poetry.masonry.builders.sdist import SdistBuilder
        from poetry.factory import Factory
        factory = Factory()
        poetry = factory.create_poetry('.')                
        sdist_builder = SdistBuilder(poetry, None, None)
        setuppy_blob = sdist_builder.build_setup()
        with open('setup.py', 'wb') as unit:
            unit.write(setuppy_blob)
            unit.write(b'\n# This setup.py was autogenerated using poetry.\n')                
    pass


def giturl2folder(git_url):
    _, fld_ = os.path.split(git_url)
    fld_, _ = os.path.splitext(fld_)
    return fld_