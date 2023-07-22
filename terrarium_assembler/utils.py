"""
    Different (geeky) utils for TA
"""
import os
import magic
import subprocess
import shutil
import pathlib
from easydict import EasyDict as edict

import hashlib
import re
import itertools

import inspect
import hashlib

def bashash4folder(var, folder):
#     scmd =f'''HASH_{var}=`tar cf - -C {folder} --mtime='1970-01-01' --format=pax --pax-option="exthdr.name=%d/PaxHeaders.0/%f,delete=atime,delete=ctime"  --numeric-owner --owner=0 --group=0 --mode='aou+rwx' --exclude=build  --exclude=.eggs --exclude=.git  --exclude='*.egg-info'  . | md5sum`
# '''
    scmd =f'''HASH_{var}=`md5deep -r -l {folder} | sort | md5sum`
'''
    return scmd

def read_old_hash(folder):
    scmd =f'''
mkdir -p {folder}    
OLD_HASH=$(cat {folder}/state.md5 || true)
'''
    return scmd

def save_state_hash(folder):
    scmd =f'''echo "$HASH_STATE" > {folder}/state.md5    
'''
    return scmd

def bashash4str(varn, msg):
    hash_ = hashlib.md5(msg.encode('utf-8')).hexdigest()
    scmd =f'''HASH_{varn}="{hash_}"
'''
    # scmd =f'''HASH_{varn}=`echo "{msg}" | md5sum`
    return scmd

def bashash_stop_if_not_changed(listvar, msg, cont=False):
    exit_mod = 'exit 0'
    if cont:
        exit_mod = 'continue'
    complex_hash_ = ' + '.join([f'$HASH_{v}' for v in listvar])
    scmd = f'''
HASH_STATE="{complex_hash_}"    
if [[ "$OLD_HASH" == "$HASH_STATE" ]] then
    echo "{msg}"
    {exit_mod}
fi
'''
    return scmd


def bashash_ok_folders_strings(targetdir, folders, strs, msg, cont=False):
    lines = []
    vars_ = []
    for i, fld in enumerate(folders):
        var_ = f'FLD{i:02}'
        vars_.append(var_)
        lines.append(f'''
{bashash4folder(var_, fld)}        
''')
    for i, str_ in enumerate(strs):
        var_ = f'REQ{i:02}'
        vars_.append(var_)
        lines.append(f'''
{bashash4str(var_, str_)}        
''')
    lines.append(f'''
{read_old_hash(targetdir)}  
{bashash_stop_if_not_changed(vars_, msg, cont)}
''')
    return "\n".join(lines)



def get_method_name():
    curframe = inspect.currentframe().f_back
    (filename, line_number, function_name, lines, index) = inspect.getframeinfo(curframe)        
    return function_name


def fname2stage(fname):
    return re.sub(r'''\d\d\_''', '', fname)

def fname2shname(fname, spy=False):
    ext = '.sh'
    if spy:
        ext = '.spy'
    return 'ta-' + fname.replace('stage_','').replace('_', '-') + ext

def fname2num(fname):
    for m in re.findall(r'''\d\d''', fname):
        return int(m)
    return None

def fname2option(fname):
    return re.sub(r'''_''', '-', fname)


def split_seq(iterable, size):
    it = iter(iterable)
    item = list(itertools.islice(it, size))
    while item:
        yield item
        item = list(itertools.islice(it, size))

def j2_hash_filter(value, hash_type="sha1"):
    """
    Example filter providing custom Jinja2 filter - hash

    Hash type defaults to 'sha1' if one is not specified

    :param value: value to be hashed
    :param hash_type: valid hash type
    :return: computed hash as a hexadecimal string
    """
    hash_func = getattr(hashlib, hash_type, None)

    if hash_func:
        computed_hash = hash_func(value.encode("utf-8")).hexdigest()
    else:
        raise AttributeError(
            "No hashing function named {hname}".format(hname=hash_type)
        )

    return computed_hash

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

def mp_magic_create_filemagic(mime_detected, type_detected):
    '''
    Temp monkeypatching for debugging error cases.
    '''
    from magic import FileMagic
    try:
        # mime_encoding = 'utf-8'
        # mime_detected.split('; ')
        mime_type, mime_encoding = mime_detected.split('; ')
    except ValueError:
        raise ValueError(mime_detected)

    return FileMagic(name=type_detected, mime_type=mime_type,
                     encoding=mime_encoding.replace('charset=', ''))


magic._create_filemagic = mp_magic_create_filemagic

def wtf(f):
    '''
    For debugging purposes.
    '''
    for wtf_ in ['PYTEST', '/tests']:
        if wtf_ in f:
            return True
        

def yaml_load(filename, vars__=None):
    '''
    Load yaml file into edict. Hide edict deps.
    ''' 
    import yaml
    from jinja2 import Environment, FileSystemLoader, Template, Undefined, DebugUndefined

    vars_ = {}
    if vars__:
        vars_ = vars__

    fc = None
    # with open(filename, 'r') as f:
    dir_, filename_ = os.path.split(os.path.abspath(filename))
    file_loader = FileSystemLoader(dir_)
    env = Environment(loader=file_loader, undefined=DebugUndefined)
    env.filters["hash"] = j2_hash_filter    
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.rstrip_blocks = True            

    template = env.get_template(filename_)

    real_yaml = ''
    try:
        for try_ in range(5):
            real_yaml = template.render(vars_)
            ld = yaml.safe_load(real_yaml)
            vars_ = {**vars_, **ld}

        # for key in vars_:
        #     if key.endswith('_dir'):
        #         vars_[key] = vars_[key].replace('/', '@')

        real_yaml = template.render(vars_)
        fc = edict(yaml.safe_load(template.render(vars_)))
    except Exception as ex_:
        print(f'Error parsing {filename_} see "troubles.yml" ')    
        with open("troubles.yml", 'w', encoding='utf-8') as lf:
            lf.write(real_yaml)
        raise ex_    
    # for key in fc:
    #     if key.endswith('_dir'):
    #         fc[key] = fc[key].replace('/', '\\')
    return fc, vars_


# def fix_elf_for_interpreter(path, libpath):
#     '''
#     Make "portable" Elf-binary or SO-library.

#     Calling patchelf to set RUNPATH to given libpath.
#     '''

#     from tempfile import mkstemp
#     patching_dir = 'tmp/patching'
#     mkdir_p(patching_dir)

#     fd_, patched_elf = mkstemp(dir=patching_dir)
#     shutil.copy2(path, patched_elf)
    
#     orig_perm = stat.S_IMODE(os.lstat(path).st_mode)
#     os.chmod(patched_elf, orig_perm | stat.S_IWUSR)         

#     try:
#         subprocess.check_call(['patchelf',
#                                '--set-rpath',
#                                libpath,
#                                patched_elf])
#     except Exception as ex_:
#         print("Cannot patch ", path)
#         # raise ex_
#         pass

#     os.close(fd_)
#     os.chmod(patched_elf, orig_perm)         
#     return patched_elf

def rmdir(oldpath):
    if os.path.exists(oldpath):
        shutil.rmtree(oldpath, ignore_errors=True)
    if os.path.exists(oldpath):
        os.system('sudo rm -rf "%s"' % oldpath)
    #     elevate(graphical=False)
    #     shutil.rmtree(oldpath)
    pass

def git2dir(git_url, git_branch, path_to_dir):
    oldpath = path_to_dir + '.old'
    newpath = path_to_dir + '.new'
    rmdir(oldpath)
    pdir = os.path.split(path_to_dir)[0]
    os.chdir(pdir)
    scmd = 'git --git-dir=/dev/null clone --single-branch --branch %(git_branch)s  --depth=1 %(git_url)s %(newpath)s ' % vars()
    rmdir(newpath)
    os.system(scmd)
    if os.path.exists(newpath):
        if os.path.exists(path_to_dir):
            rmdir(oldpath)
            shutil.move(path_to_dir, oldpath)
        print(newpath, "->", path_to_dir)    
        shutil.move(newpath, path_to_dir)
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


def expandpath(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def write_doc_table(filename, headers, rows):
    with open(filename, 'w', encoding='utf-8') as lf:
        lf.write(f"""
<table class='wikitable' border=1>
""")
        lf.write(f"""<tr>""")
        for col_ in headers:
            lf.write(f"""<th>{col_}</th>""")
        lf.write(f"""</tr>\n""")
        for row_ in rows:
            lf.write(f"""<tr>""")
            for col_ in row_:
                lf.write(f"""<td>{col_}</td>""")
            lf.write(f"""</tr>\n""")
        lf.write(f"""
</table>
""")
    return


def fucking_magic(f):
    # m = magic.detect_from_filename(f)
    if "ld.so" in f:
        wtf = 1
        pass

    if not os.path.exists(f):
        return ''

    if not os.path.isfile(f):
        return ''

    m = magic.from_file(f)
    # if m.mime_type in ['inode/symlink', 'text/plain']:
    #     return
    return m


def get_git_version():
    tags = subprocess.check_output("git tag --sort=-creatordate --merged", 
                                    shell=True, universal_newlines=True)
    for tag in tags.strip().split('\n'):
        if tag.startswith('v'):
            return tag[1:] 
    return '1.0.0'
