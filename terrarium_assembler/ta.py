"""Main module."""
import argparse
import io
import os
import pathlib
import subprocess
import shutil
import sys
from tempfile import mkstemp
import magic
import stat
import re
import yaml
import json
import dataclasses as dc
import dnf
import datetime
import tarfile
import hashlib 
import time
import glob
import csv


from contextlib import suppress
from wheel_filename import parse_wheel_filename

from .utils import *

#будет отключено
from .nuitkaflags import *

#новая ветка
from .nuitkaprofiles import *

from pytictoc import TicToc
t = TicToc()


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

from typing import List

@dc.dataclass
class BinRegexps:
    '''
    Binary regexps. 
    '''
    need_patch: list #bins that need to be patched.   
    just_copy:  list #bins that just need to be copied.
    need_exclude: dict #bins that just need to be copied.
    debug: bool 

    def __post_init__(self):
        def add_rex2list(rex, alist):            
            try:
                re_ = re.compile(rex + '$')
                alist.append(re_) 
            except Exception as ex_:
                print("*"*20)
                print("Cannot regx-compile", rex)
                print("*"*20)
                raise ex_

        def add_listrex2dict(listrex, adict):            
            for rex in listrex or []:
                try:
                    re_ = re.compile(rex + '$')
                    adict[re_] = 0
                except Exception as ex_:
                    print("*"*20)
                    print("Cannot regx-compile", rex)
                    print("*"*20)
                    raise ex_


        def add_listrex2list(listrex, alist):            
            for rex in listrex or []:
                add_rex2list(rex, alist)

        self.just_copy_re = []
        add_listrex2list(self.just_copy, self.just_copy_re)

        self.need_patch_re = []
        add_listrex2list(self.need_patch, self.need_patch_re)

        self.need_exclude_re = {}
        add_listrex2dict(self.need_exclude.common, self.need_exclude_re)
        if self.debug:
            add_listrex2dict(self.need_exclude.debug, self.need_exclude_re)
        else:
            add_listrex2dict(self.need_exclude.release, self.need_exclude_re)                       
        pass

    def is_just_copy(self, f):
        for re_ in self.just_copy_re:
            if re_.match(f):
                return True
        return False    

    def is_need_patch(self, f):
        for re_ in self.need_patch_re:
            if re_.match(f):
                return True
        return False    

    def is_need_exclude(self, f):
        for re_ in self.need_exclude_re:
            if re_.match(f):
                return True
        return False    

    def is_needed(self, f):
        return (self.is_just_copy(f) or self.is_need_patch(f)) 
        #and not self.is_need_exclude(f)
    
    pass


@dc.dataclass
class PythonPackagesSpec:
    '''
    Specification of set of python packages,
    by pip modules and by git-nodes of some python projects
    '''
    pip: list = None
    projects: list = None


@dc.dataclass
class PythonPackages:
    '''
        We separate python projects to two classes:
        build: only needed for building on builder host
        terra: needed to be install into terrarium
    '''
    build: PythonPackagesSpec
    terra: PythonPackagesSpec

    def __post_init__(self):    
        '''
        Recode from easydicts to objects
        '''
        self.build = PythonPackagesSpec(**self.build)
        self.terra = PythonPackagesSpec(**self.terra)
        pass

    def pip(self):
        '''
        Get full list of pip packages
        ''' 
        return (self.build.pip or []) + (self.terra.pip or [])

    def projects(self):
        '''
        Get full list of python projects
        ''' 
        return (self.build.projects or []) + (self.terra.projects or [])


@dc.dataclass
class GoPackagesSpec:
    '''
    Specification of set of go packages,
    by goget modules and by git-nodes of some go projects
    '''
    # pip: list = None
    projects: list = None


@dc.dataclass
class GoPackages:
    '''
        We separate go projects to two classes:
        build: only needed for building on builder host
        terra: needed to be install into terrarium
    '''
    build: GoPackagesSpec
    terra: GoPackagesSpec

    def __post_init__(self):    
        '''
        Recode from easydicts to objects
        '''
        self.build = GoPackagesSpec(**self.build)
        self.terra = GoPackagesSpec(**self.terra)
        pass

    def projects(self):
        '''
        Get full list of go projects
        ''' 
        return (self.build.projects or []) + (self.terra.projects or [])



@dc.dataclass
class PackagesSpec:
    '''
    Packages Spec.
    '''
    repos: list
    build:  list
    terra:  list 
    exclude_prefix: list 
    exclude_suffix: list
    remove_from_download: list

    def __post_init__(self):    
        '''
        Add some base-packages to «build» list
        '''
        for p_ in ['git', 'mc', 'pipenv']:
            if p_ not in self.build:
                self.build.append(p_)
        pass                

    def is_package_needed(self, package):
        '''
        Фильтруем базовые пакеты, они приедут по зависимостям, но для переносимого питона не нужны.
        Заодно фильтруем всякое, что может каким-то хреном затесаться. Например, 32 битное.
        '''
        for x in self.exclude_prefix:
            if package.startswith(x):
                return False
    
        for x in self.exclude_suffix:
            if package.endswith(x):
                return False
    
        return True 




@dc.dataclass
class FoldersSpec:
    folders:   list


@dc.dataclass
class ModulesSpec:
    modules:   list



class TerrariumAssembler:
    '''
    Генерация переносимой сборки бинарных линукс-файлов (в частности питон)
    '''
    
    def __init__(self):
        self.curdir = os.getcwd()
        self.root_dir = None

        # Потом сделать параметром функций.
        self.overwrite_mode = False 

        ap = argparse.ArgumentParser(description='Create a portable linux folder-application')
        # ap.add_argument('--output', required=True, help='Destination directory')
        ap.add_argument('--debug', default=False, action='store_true', help='Debug version of release')
        ap.add_argument('--docs', default=False, action='store_true', help='Output documentation version')

        self.stages = {
            'download-rpms' : 'download RPMs',
            'download-sources-for-rpms': 'download SRPMs — sources packages for RPMS',
            'checkout' : 'checkout sources',
            'install-repos': 'install RPM repositories',
            'install-rpms': 'install downloaded RPMS',
            'download-wheels': 'download needed WHL-python packages',
            # 'preinstall-wheels': 'Bootstrap build environment with external wheel packages',
            'init-env': 'Create  build environment with some bootstrapping',
            'build-wheels': 'compile wheels for our python sources',
            'install-wheels': 'Install our and external Python wheels',
            'build-nuitka': 'Compile Python packages to executable',
            'build-go': 'Compile Go projects to executable',
            'make-isoexe': 'Also make self-executable install archive and ISO disk',
            'pack-me' :  'Pack current dir to time prefixed tar.bz2'
        }

        for stage, desc in self.stages.items():
            ap.add_argument('--stage-%s' % stage, default=False, action='store_true', help='Stage for %s ' % desc)

        # ap.add_argument('--stage-download', default=False, action='store_true', help='Stage for download binary artifacts')
        # ap.add_argument('--stage-build-wheels', default=False, action='store_true', help='Build Wheels for source packages')
        # ap.add_argument('--stage-setupsystem', default=False, action='store_true', help='Stage for setup local OS')
        # ap.add_argument('--stage-build-nuitka', default=False, action='store_true', help='Compile Nuitka packages')
        ap.add_argument('--stage-build-and-pack', default='', type=str, help='Install, build and pack')
        ap.add_argument('--stage-download-all', default=False, action='store_true', help='Download all — sources, packages')
        ap.add_argument('--stage-my-source-changed', default='', type=str, help='Fast rebuild/repack if only pythonsourcechanged')
        ap.add_argument('--stage-all', default='', type=str, help='Install, build and pack')
        ap.add_argument('--stage-pack', default='', type=str, help='Stage pack to given destination directory')
        ap.add_argument('--analyse', default='', type=str, help='Analyse resulting pack')
        ap.add_argument('--folder-command', default='', type=str, help='Perform some shell command for all projects')
        ap.add_argument('specfile', type=str, help='Specification File')
        
        self.args = args = ap.parse_args()
        if self.args.stage_all:
            self.args.stage_build_and_pack = self.args.stage_all
            self.args.stage_download_all = True

        if self.args.stage_build_and_pack:
            self.args.stage_install_rpms = True
            # self.args.stage_preinstall_wheels = True
            self.args.stage_init_env = True
            self.args.stage_build_wheels = True
            self.args.stage_install_wheels = True
            self.args.stage_build_nuitka = True
            self.args.stage_build_go = True
            self.args.stage_pack = self.args.stage_build_and_pack

        if self.args.stage_my_source_changed:
            # self.args.stage_checkout = True
            self.args.stage_download_wheels = True
            self.args.stage_init_env = True
            # self.args.stage_preinstall_wheels = True
            self.args.stage_build_wheels = True
            self.args.stage_install_wheels = True
            self.args.stage_build_nuitka = True
            self.args.stage_build_go = True
            self.args.stage_pack = self.args.stage_my_source_changed

        if self.args.stage_download_all:
            self.args.stage_install_repos = True
            self.args.stage_download_rpms = True
            self.args.stage_checkout = True
            self.args.stage_download_wheels = True

        specfile_  = expandpath(args.specfile)
        os.environ['TERRA_SPECDIR'] = os.path.split(specfile_)[0]


        self.tvars = edict() 
        self.tvars.python_version_1, self.tvars.python_version_2 = sys.version_info[:2]
        self.tvars.py_ext = ".pyc"
        if self.args.debug:
            self.tvars.py_ext = ".py"
        self.tvars.release = not self.args.debug
        self.tvars.fc_version = ''
        try:
            with open('/etc/fedora-release', 'r', encoding='utf-8') as lf:
                ls = lf.read()
                self.tvars.fc_version = re.search('(\d\d)', ls).group(1)
        except:
            pass # Building not on Fedora.        

        self.spec, vars_ = yaml_load(specfile_, self.tvars)    
        self.tvars = edict(vars_)
        spec = self.spec

        self.start_dir = os.getcwd()
         


        need_patch = just_copy = need_exclude = None    
        if 'bin_regexps' in spec:
            br_ = spec.bin_regexps
            if "need_patch" in br_:
                need_patch = br_.need_patch
            if "just_copy" in br_:
                just_copy = br_.just_copy
            if "need_exclude" in br_:
                need_exclude = br_.need_exclude


        self.br = BinRegexps(
            need_patch=need_patch,
            just_copy=just_copy,
            need_exclude=need_exclude,
            debug=self.args.debug,
        )

        self.need_packages = ['patchelf', 'ccache', 'gcc', 'gcc-c++', 'gcc-gfortran', 'chrpath', 
                                'python3-wheel', 'python3-pip', 'python3-devel', 'python3-yaml',
                                'genisoimage', 'makeself', 'dnf-utils']

        nflags_ = {}
        if 'nuitka' in spec:
            nflags_ = spec.nuitka

        # self.nuitkas = NuitkaFlags(**nflags_)

        self.nuitka_profiles = {}
        if 'nuitka_profiles' in spec:
            self.nuitka_profiles = NuitkaProfiles(spec.nuitka_profiles)

        self.ps = PackagesSpec(**spec.packages)
        self.pp = PythonPackages(**spec.python_packages)
        self.gp = None
        if 'go_packages' in spec:
            self.gp = GoPackages(**spec.go_packages)

        fs_ = []
        if 'folders' in spec:
            fs_ = spec.folders
        self.fs = FoldersSpec(folders=fs_)

        self.in_bin = os.path.abspath('in/bin')
        self.src_dir = 'in/src'
        if 'src_dir' in spec:
            self.src_dir = expandpath(self.src_dir)    
        self.out_dir = 'out'
        self.out_dir = expandpath(self.out_dir)    
        mkdir_p(self.src_dir)    
        mkdir_p(self.out_dir)    
        mkdir_p(self.in_bin)    

        self.our_whl_path = os.path.join(self.in_bin, "ourwheel")
        mkdir_p(self.our_whl_path)    

        self.ext_whl_path = os.path.join(self.in_bin, "extwheel")
        mkdir_p(self.ext_whl_path)    


        os.environ['PATH']="/usr/lib64/ccache:" + os.environ['PATH']

        base = dnf.Base()
        base.fill_sack()
        q_ = base.sack.query()
        self.installed_packages = q_.installed()

        self.nuitka_plugins_dir = os.path.realpath(os.path.join(os.path.split(__file__)[0], '..', 'nuitka_plugins'))

        pass

    def cmd(self, scmd):
        '''
        Print command and perform it.
        May be here we will can catch output and hunt for heizenbugs
        '''    
        print(scmd)
        return os.system(scmd)

    def packages2list(self, pl):
        pl_ = []
        for node in pl:
            if isinstance(node, str):
                pl_.append(node)
            if isinstance(node, dict):
                if 'name' in node:
                    pl_.append(node['name'])
        return pl_            



    def lines2sh(self, name, lines, stage=None):
        import stat
        os.chdir(self.curdir)
        fname = name + '.sh'

        with open(os.path.join(fname), 'w', encoding="utf-8") as lf:
            lf.write("#!/bin/sh\n#Generated %s \n " % name)
            if stage:
                desc = self.stages[stage]
                stage_  = stage.replace('_', '-')
                lf.write('''
# Stage "%s"
# Automatically called when terrarium_assembler --stage-%s "%s" 
set -x
#sudo chmod a+rwx in/src  -R
''' % (desc, stage_, self.args.specfile))
            lf.write("\n".join(lines))

        st = os.stat(fname)
        os.chmod(fname, st.st_mode | stat.S_IEXEC)

        if stage:
            param = stage.replace('-', '_')
            option = "stage_" + param
            dict_ = vars(self.args)
            if option in dict_:
                if dict_[option]:
                    print("*"*20)
                    print("Executing ", fname)
                    print("*"*20)
                    os.system("./" + fname)
        pass  


    def build_nuitkas(self):
        # if not self.nuitkas:
        #     return

        if not self.nuitka_profiles:
            return

        tmpdir = os.path.join(self.curdir, "tmp/ta")
        tmpdir = os.path.relpath(tmpdir)
        bfiles = []

        #First pass
        module2build = {}
        standalone2build = []
        referenced_modules = set()

        for np_name, np_ in self.nuitka_profiles.profiles.items():
            for target_ in np_.builds or []:
                srcname = target_.utility
                outputname = target_.utility
                nflags = np_.get_flags(tmpdir, target_)
                target_dir = os.path.join(tmpdir, outputname + '.dist')
                target_dir_ = os.path.relpath(target_dir, start=self.curdir)
                src_dir = os.path.relpath(self.src_dir, start=self.curdir)
                src = os.path.join(src_dir, target_.folder, target_.utility) + '.py'
                flags_ = ''
                if 'flags' in target_:
                    flags_ = target_.flags
                lines = []
                lines.append("""
export PATH="/usr/lib64/ccache:$PATH"
    """ % vars(self))
                build_name = 'build_' + srcname
                lines.append(fR"""
time nice -19 pipenv run python3 -m nuitka  {nflags} {flags_} {src} 2>&1 > {build_name}.log
#time nice -19 pipenv run python3 -m nuitka --recompile-c-only {nflags} {flags_} {src} 2>&1 > {build_name}.log
#time nice -19 pipenv run python3 -m nuitka --generate-c-only {nflags} {flags_} {src} 2>&1 > {build_name}.log
pipenv run python3 -m pip freeze > {target_dir_}/{build_name}-pip-freeze.txt 
    """ )
                self.fs.folders.append(target_dir)
                if "outputname" in target_:
                    srcname = target_.outputname
                    if srcname != outputname:
                        lines.append(R"""
    mv  %(target_dir_)s/%(outputname)s   %(target_dir_)s/%(srcname)s 
    """ % vars())

#             if "modules" in target_:
#                 force_modules = []
#                 if 'force_modules' in target_:
#                     force_modules = target_.force_modules

#                 for it in target_.modules + force_modules:
#                     mdir_ = None
#                     try:
#                         mdir_ = dir4module(it) 
#                         mdir__ = os.path.relpath(mdir_)
#                         if len(mdir__)<len(mdir_):
#                             mdir_ = mdir__
#                     except:
#                         pass                

#                     try:
#                         mdir_ = module2build[it].folder 
#                     except:
#                         pass                

#                     if mdir_:        
#                         lines.append(R"""
# rsync -rav --exclude=*.py --exclude=*.pyc --exclude=__pycache__ --prune-empty-dirs %(mdir_)s %(target_dir_)s/                
# """ % vars())

#                 force_modules = []
#                 for it in target_.modules:
#                     lines.append(R"""
# rsync -av --include=*.so --include=*.bin --exclude=*  %(tmpdir_)s/modules/%(it)s/ %(target_dir_)s/.                
# rsync -rav  %(tmpdir_)s/modules/%(it)s/%(it)s.dist/ %(target_dir_)s/.                
# """ % vars())
                self.lines2sh(build_name, lines, None)
                bfiles.append(build_name)


        lines = []
        for b_ in bfiles:
            lines.append("./" + b_ + '.sh')
        self.lines2sh("40-build-nuitkas", lines, "build-nuitka")
        pass


    def build_go(self):
        if not self.gp:
            return

        tmpdir = os.path.join(self.curdir, "tmp/ta")
        bfiles = []

        #First pass
        module2build = {}
        standalone2build = []
        referenced_modules = set()

        for td_ in self.gp.projects():
            git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)
            os.chdir(self.curdir)
            if os.path.exists(path_to_dir_):
                os.chdir(path_to_dir_)
                path_to_dir__ = os.path.relpath(path_to_dir_, start=self.curdir)
                outputname = os.path.split(path_to_dir_)[-1]
                target_dir = os.path.join(tmpdir, outputname + '.build')
                target_dir_ = os.path.relpath(target_dir, start=path_to_dir_)
                lines = []
                build_name = 'build_' + outputname
                lines.append(fR"""
pushd {path_to_dir_}       
go mod download          
CGO_ENABLED=0 go build -ldflags="-linkmode=internal -r" -o {target_dir_}/{outputname} 2>&1 >{outputname}.log
popd
    """ )
                self.fs.folders.append(target_dir)
                self.lines2sh(build_name, lines, None)
                bfiles.append(build_name)

        lines = []
        for b_ in bfiles:
            lines.append("./" + b_ + '.sh')
        self.lines2sh("42-build-go", lines, "build-go")
        pass


    def mycopy(self, src, dst):
        '''
        Адаптивная процедура копирования в подкаталоги окружения — симлинки релятивизируются 
        и остаются симлинками.
        '''
        if os.path.exists(dst) and not self.overwrite_mode:
            return
        if '__pycache__' in src:
            return
        try:
            # 
            # if wtf(src):
            #     return
            if src in ["/etc/environment"]:
                return
            if os.path.islink(src):
                linkto = os.readlink(src)
                dir_, file_ = os.path.split(linkto)
                dirdst_, filedst_ = os.path.split(dst)
                dirsrc_, filesrc_ = os.path.split(src)
                if not dir_ or dirsrc_ == dir_:
                    if not os.path.exists(dst):
                        # if file_ == '/usr/lib/python3.7/site-packages/__pycache__/six.cpython-37.opt-1.pyc' or dst=='/home/stas/projects/docmarking/dm-deploy/envs/v013/lib64/python3.7/site-packages/urllib3/packages/__pycache__/six.cpython-37.opt-1.pyc':
                        #     wtf_=1
                        os.symlink(file_, dst)
                else:
                    pass
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        except Exception as ex:
            print('Cannot copy ', src, dst)
            raise ex
        pass    
            

    def should_copy(self, f):
        '''
        Получив файл, возвращает, заинтересованы ли мы в копировании этого файла или нет.
        Нам нужен реальный интерпретатор, а также файлы в /lib(64) и /usr/lib(64)
        
        Все файлы из /var и т.п. пофиг для нашего портабельного питона. 
        Также выкидываем локализацию.
    
        Файлы build_id будут сим-ссылками на двоичные файлы и разделяемые библиотеки, которые мы не хотим хранить.
    '''
        if wtf(f):
            return False

        if "__pycache__" in f:
            return False

        if 'nginx' in f and 'sbin' in f:
            w_ = 1
        
        if f == "": 
            return False

        if f == '/lib/libpthread-2.31.so':
            wtf333 = 1


        # if self.br.is_need_exclude(f):
        #     return False

        if self.br.is_needed(f):
            return True

        if f.startswith("/lib64/ld-linux"): # Этот файл надо специально готовить, чтобы сделать перемещаемым.
            return False
    
        parts = list(pathlib.PurePath(f).parts)
        el = parts.pop(0)
        if el != "/":
            raise RuntimeError("unexpected path: not absolute! {}".format(f))
    
        if len(parts) > 0 and parts[0] == "usr":
            parts.pop(0)
            if len(parts) > 0 and parts[0] == "local":
                parts.pop(0)
    
        if not parts:
            return False
    
        if not self.args.debug:
            if (parts[0] not in ["lib", "lib64"]) and (parts != ['bin', 'bash', 'sbin']):
                return False
        parts.pop(0)
    
        if len(parts) > 0 and (parts[0] == "locale" or parts[0] == ".build-id"):
            return False
    
        # что не отфильтровалось — берем.
        return True



    def rpm_update_time(self):
        import time
        for rpmdbpath in ["/var/lib/rpm/Packages", "/var/lib/rpm/rpmdb.sqlite"]:
            if os.path.exists(rpmdbpath):
                return str(time.ctime(os.path.getmtime(rpmdbpath)))        
        return None        


    def dependencies(self, package_list, local=True):
        '''
        Генерируем список RPM-зависимостей для заданного списка пакетов.
        '''

        pl_ = self.packages2list(package_list)
        package_list_md5 = hashlib.md5((self.rpm_update_time() + '\n' + '\n'.join(pl_)).encode('utf-8')).hexdigest()
        cache_filename = 'cache_' + package_list_md5 + '.list' 
        if os.path.exists(cache_filename):
            with open(cache_filename, 'r', encoding='utf-8') as lf:
                ls_ = lf.read()
                list_ = ls_.split(',')
                return list_

        repoch = re.compile("\d+\:")
        def remove_epoch(package):
            package_ = repoch.sub('', package)    
            return package_

        options_ = [
            # Фильтруем пакеты по 64битной архитектуре (ну или 32битной, если будем собирать там.),
            # хотя сейчас почти везде хардкодинг на 64битную архитектуру.
            '--archlist=noarch,{machine}'.format(machine=os.uname().machine),
                    '--resolve',
                    '--requires',
                    '--recursive'
            ]
        if local:
            options_ += [
                '--cacheonly', 
                '--installed',
            ]    
    
    
        if 1:
            # res = subprocess.check_output(['repoquery'] + options_  + ['--tree', '--whatrequires'] + package_list,  universal_newlines=True)
            res = ''
            for try_ in range(3):
                try:
                    res = subprocess.check_output(['repoquery', '-y'] + options_  + pl_,  universal_newlines=True)
                    break
                except subprocess.CalledProcessError:
                    #  died with <Signals.SIGSEGV: 11>.
                    time.sleep(2)                    
            # res = subprocess.check_output(['repoquery'] + options_  + ['--output', 'dot-tree'] + package_list,  universal_newlines=True)
            with open(os.path.join(self.start_dir, 'deps.txt'), 'w', encoding='utf-8') as lf:
                lf.write('\n -'.join(pl_))
                lf.write('\n----------------\n')
                lf.write(res)
    

        output  = subprocess.check_output(['repoquery'] + options_ + pl_,  universal_newlines=True).splitlines()
        output = [remove_epoch(x) for x in output if self.ps.is_package_needed(x)]
        packages_ = output + pl_
        with open(os.path.join(self.start_dir, 'selected-packages.txt'), 'w', encoding='utf-8') as lf:
            lf.write('\n- '.join(packages_))



        packages_set_ = set()
        for package_ in packages_:
            purepackage = package_.split('.', 1)[0]
            if len(purepackage) < len(package_):
                purepackage  = purepackage.rsplit('-', 1)[0]
            packages_set_.add(purepackage)

        rows_ = []
        for package_ in sorted(packages_set_):
            res_ = list(self.installed_packages.filter(name=package_))
            if len(res_)==0:
                continue
            name_ = res_[0].name
            version_ = res_[0].version
            wtf = 1

            rows_.append([name_, version_])
            pass

        write_doc_table('doc-rpm-packages.htm', ['Packages', 'Version'], rows_)

        with open(cache_filename, 'w', encoding='utf-8') as lf:
            lf.write(','.join(packages_))

        return packages_ 

    def generate_file_list_from_pips(self, pips):
        '''
        Для заданного списка PIP-пакетов, возвращаем список файлов в этих пакетах, которые нужны нам.
        '''
        file_list = []
        pips_ = [p.split('==')[0] for p in pips]
        import pkg_resources
        for dist in pkg_resources.working_set:
            if dist.key in pips_:
                if dist.has_metadata('RECORD'):
                    lines = dist.get_metadata_lines('RECORD')
                    paths = [line.split(',')[0] for line in lines]
                    paths = [os.path.join(dist.location, p) for p in paths]
                    file_list.extend(paths)

        pass
        res_ = [x for x in file_list if self.should_copy(x)]
        return res_
        pass


    def generate_file_list_from_packages(self, packages):
        '''
        Для заданного списка RPM-файлов, возвращаем список файлов в этих пакетах, которые нужны нам.
        '''
    
        package_list_md5 = hashlib.md5((self.rpm_update_time() + '\n' + '\n'.join(packages)).encode('utf-8')).hexdigest()
        cache_filename = 'cachefilelist_' + package_list_md5 + '.list' 
        if os.path.exists(cache_filename):
            with open(cache_filename, 'r', encoding='utf-8') as lf:
                ls_ = lf.read()
                list_ = ls_.split('\n')
                return list_



        exclusions = []
        for package_ in packages:
            exclusions += subprocess.check_output(['rpm', '-qd', package_], universal_newlines=True).splitlines()
    
        # we don't want to use --list the first time: For one, we want to be able to filter
        # out some packages with files
        # we don't want to copy
        # Second, repoquery --list do not include the actual package files when used with --resolve 
        # and --recursive (only its dependencies').
        # So we need a separate step in which all packages are added together.
    
        # for package_ in packages:
        #     # if 'postgresql12-server' == package_:
        #     #     wttt=1

        #     # TODO: Somehow parallelize repoquery running
        #     for try_ in range(3):
        #         try:
        #             files = subprocess.check_output(['repoquery',
        #                                         '-y',
        #                                         '--installed',
        #                                         '--archlist=x86_64,noarch'
        #                                         '--cacheonly',
        #                                         '--list' ] + [package_], universal_newlines=True).splitlines()
        #             break                                
        #         except:
        #             pass                                            

        #     for file in files:
        #         if 'i686' in file:
        #             assert(True)
            
        
        candidates = subprocess.check_output(['repoquery',
                                     '-y',
                                     '--installed',
                                     '--archlist=x86_64,noarch',                                     
                                     '--cacheonly',
                                     '--list' ] + packages, universal_newlines=True).splitlines()
    
        # candidates = subprocess.check_output(executables, universal_newlines=True).splitlines()
    
        pass
        res_ = [x for x in set(candidates) - set(exclusions) if self.should_copy(x)]

        with open(cache_filename, 'w', encoding='utf-8') as lf:
            lf.write('\n'.join(res_))

        return res_

    def add(self, what, to_=None, recursive=True):
        if 'java-11' in what:
            dfsfsfdsf=1
        try:
            if not to_:
                to_ = what
                if to_.startswith('/'):
                    to_ = to_[1:]
                
            dir_, _ = os.path.split(to_)
            pathlib.Path(os.path.join(self.root_dir, dir_)).mkdir(parents=True, exist_ok=True)
            # ar.add(f)
            if os.path.isdir(what):
                # copy_tree(what, os.path.join(root_dir, to_))
                # Какого хуя!!!!!!!!!!
                # if not os.path.exists(os.path.join(self.root_dir, to_)):
                #     shutil.copytree(what, os.path.join(self.root_dir, to_), symlinks=True, copy_function=self.mycopy)
                #     #, exist_ok=True)
                pass
            else:
                self.mycopy(what, os.path.join(self.root_dir, to_))
            pass
        except Exception as ex_:
            print("Troubles on adding", to_ , "<-", what)
            pass
            #raise ex_
            pass

    def projects(self):
        """
        return all projects list (Python/go/etc)
        """
        projects_ = []
        if self.pp:
            projects_ += self.pp.projects() 

        if self.gp:
            projects_ += self.gp.projects() 
        return projects_


    def process_binary(self, binpath):
        '''
        Фиксим бинарник.
        '''
        for wtf_ in ['libldap']:
            if wtf_ in binpath:
                return
        
        # m = magic.detect_from_filename(binpath)
        m = fucking_magic(binpath)
        if m in ['inode/symlink', 'text/plain']:
            return
        
        # if m.mime_type not in ['application/x-sharedlib', 'application/x-executable']
        if not 'ELF' in m:
            return
    
        pyname = os.path.basename(binpath)
        try:
            patched_binary = fix_binary(binpath, '$ORIGIN/../lib64/')
        except Exception as ex_:
            print("Mime type ", m)
            print("Cannot fix", binpath)
            raise ex_
    
        try:
            interpreter = subprocess.check_output(['patchelf',
                                               '--print-interpreter',
                                               patched_binary], universal_newlines=True).splitlines()[0]
            self.add(os.path.realpath(interpreter), os.path.join("pbin", "ld.so"))
        except Exception as ex_:
            print('Cannot get interpreter for binary', binpath)
            # raise ex_
        pass
        
        self.add(patched_binary, os.path.join("pbin", pyname))
        os.remove(patched_binary)
    
    
    def fix_sharedlib(self, binpath, targetpath):
        relpath =  os.path.join(os.path.relpath("lib64", targetpath), "lib64")
        patched_binary = fix_binary(binpath, '$ORIGIN/' + relpath)
        self.add(patched_binary, targetpath)
        os.remove(patched_binary)
        pass

    def get_all_sources(self):
        for td_ in self.projects() + self.spec.templates_dirs:
            git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)
            yield git_url, git_branch, path_to_dir_

    def folder_command(self):
        '''
            Just checking out sources.
            This stage should be done when we have authorization to check them out.
        '''
        if not self.pp:
            return

        curdir = os.getcwd()
        args = self.args
        in_src = os.path.relpath(self.src_dir, start=self.curdir)
        # for td_ in self.projects() + self.spec.templates_dirs:
        #     git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)

        for git_url, git_branch, path_to_dir_ in self.get_all_sources():
            os.chdir(curdir)
            if os.path.exists(path_to_dir_):
                os.chdir(path_to_dir_)
                print('*'*10 + f' Git «{args.folder_command}» for {git_url} ')
                scmd = f'''{args.folder_command}'''
                os.system(scmd)
        pass

    def checkout_sources(self):
        '''
            Just checking out sources.
            This stage should be done when we have authorization to check them out.
        '''
        if not self.pp:
            return

        args = self.args
        lines = []
        lines2 = []
        in_src = os.path.relpath(self.src_dir, start=self.curdir)
        # lines.add("rm -rf %s " % in_src)
        lines.append(f"""
mkdir -p tmp/snaphots-src
snapshotdir=$(date +"tmp/snaphots-src/snapshot-src-before-%Y-%m-%d-%H-%M-%S")
mv in/src $snapshotdir
mkdir -p {in_src}
""")
        already_checkouted = set()
        for td_ in self.projects() + self.spec.templates_dirs:
            git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)
            if path_to_dir_ not in already_checkouted:
                probably_package_name = os.path.split(path_to_dir_)[-1]
                already_checkouted.add(path_to_dir_)
                path_to_dir = os.path.relpath(path_to_dir_, start=self.curdir)
                newpath = path_to_dir + '.new'
                lines.append('rm -rf "%(newpath)s"' % vars())
                # scmd = 'git --git-dir=/dev/null clone --single-branch --branch %(git_branch)s  --depth=1 %(git_url)s %(newpath)s ' % vars()
                scmd = '''
git --git-dir=/dev/null clone  %(git_url)s %(newpath)s 
pushd %(newpath)s 
git checkout %(git_branch)s
git config core.fileMode false
git config core.autocrlf input
git lfs install 
git lfs pull
popd
''' % vars()
                lines.append(scmd)

                lines2.append('''
pushd "%(path_to_dir)s"
git config core.fileMode false
git config core.autocrlf input
git pull
pipenv run python -m pip uninstall  %(probably_package_name)s -y
pipenv run python setup.py develop
popd
''' % vars())


                # Fucking https://www.virtualbox.org/ticket/19086 + https://www.virtualbox.org/ticket/8761
                lines.append("""
if [ -d "%(newpath)s" ]; then
  echo 2 > /proc/sys/vm/drop_caches 
  find  "%(path_to_dir)s" -type f -delete;
  find  "%(path_to_dir)s" -type f -exec rm -rf {} \;
  rm -rf "%(path_to_dir)s"  
  mv "%(newpath)s" "%(path_to_dir)s"
  rm -rf "%(newpath)s"  
fi            
""" % vars())

        self.lines2sh("05-checkout", lines, 'checkout')    
        # self.lines2sh("96-pullall", lines2)    
        pass

    def explode_pp_node(self, td_):
        '''
        Преобразует неоднозначное описание yaml-ноды пакета в git_url и branch
        '''    
        git_url = None
        git_branch = 'master'

        if isinstance(td_, str):
            git_url = td_
        else:
            git_url = td_.url
            if 'branch' in td_:
                git_branch = td_.branch
            if 'cache' in td_:
                git_url = expandpath(td_.cache)

        path_to_dir = os.path.join(self.src_dir, giturl2folder(git_url))
        setup_path = path_to_dir

        if 'subdir' in td_:
            subdir = td_.subdir

        setup_path = path_to_dir

        return git_url, git_branch, path_to_dir, setup_path


    def pip_install_offline_cmd(self, target):
        '''
        Get options for installing by pip only using offline downloaded wheel packages
        '''
        our_whl_path = os.path.relpath(self.our_whl_path, self.curdir)
        ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)
        scmd = f' -m pip install {target} --no-index --no-cache-dir --use-deprecated=legacy-resolver --find-links="{ext_whl_path}" --find-links="{our_whl_path}"  --force-reinstall  --ignore-installed ' 
        return scmd


    def pip_install_offline(self, target):
        '''
        Installing by pip only using offline downloaded wheel packages
        '''
        opts_ = self.pip_install_offline_cmd(target)
        scmd = f'{self.root_dir}/ebin/python3 {opts_} ' 
        self.cmd(scmd)
        pass                        


    def install_terra_pythons(self):
        # if not self.pp.terra.pip and not self.pp.terra.projects:
        #     return

        # Пока хардкодим вставку нашего питон-пипа. потом конечно надо бы избавится.
        root_dir = self.root_dir
        os.chdir(self.curdir)

        pipdir = ''
        for pdir in ('github-belonesox-pip', 'pip'):
            pipdir = os.path.join('in', 'src', pdir)
            if os.path.exists(pipdir):
                break

        os.chdir(pipdir)    
        # os.chdir(os.path.join('in', 'src', 'github-belonesox-pip'))
        scmd = f'''{self.root_dir}/ebin/python3 setup.py install --single-version-externally-managed --root / '''
        os.system(scmd)

        os.chdir(self.curdir)
        args = self.args

        terra_ = True
        if self.args.debug:
            terra_ = False

        pip_args_ = self.pip_args_from_sources(terra=terra_)

        our_whl_path = os.path.relpath(self.our_whl_path, self.curdir)
        ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)

        # os.system(f'''{self.root_dir}/ebin/python3 -m pip install {pip_args_} --find-links="{our_whl_path}" --find-links="{ext_whl_path}"''')

        scmd = f'''
{self.root_dir}/ebin/python3 -m pip install setuptools --find-links="{our_whl_path}" --find-links="{ext_whl_path}" --force-reinstall --ignore-installed --no-warn-script-location     
        '''
        self.cmd(scmd)


        if self.args.debug:
            scmd = f'''
{self.root_dir}/ebin/python3 -m pip install pip {our_whl_path}/*.whl {ext_whl_path}/*.whl --find-links="{our_whl_path}" --find-links="{ext_whl_path}" --force-reinstall --ignore-installed --no-warn-script-location     
            '''
        else:
            scmd = f'''
{self.root_dir}/ebin/python3 -m pip install {pip_args_} --find-links="{our_whl_path}" --find-links="{ext_whl_path}" --force-reinstall --ignore-installed --no-warn-script-location     
            '''

        os.chdir(self.curdir)
        self.cmd(scmd)
        if self.tvars.fc_version == '32':
            os.system(f"rm -f {root_dir}/local/lib/python3.8/site-packages/typing.*")

        # if self.args.debug:
        #     pl_ = self.get_wheel_list_to_install()
        #     # pls_ = " ".join(pl_)
        #     for pls_ in pl_:
        #         if 'urllib3' in pls_:
        #             wt_ = 1
        #         scmd = '%(root_dir)s/ebin/python3 -m pip install  %(pls_)s --no-deps --force-reinstall --no-dependencies --ignore-installed ' % vars()
        #         print(scmd)
        #         os.system(scmd)
        #         wtf_path = f'{root_dir}/local/lib/python3.8/site-packages/enum'
        #         if os.path.exists(wtf_path):
        #             print('Fucking enum34 here')
        #             sys.exit(0)

        # # ext_whl_path = os.path.join(self.in_bin, "extwheel")
        # if self.pp.terra.pip:
        #     for pip_ in self.pp.terra.pip:
        #         self.pip_install_offline(pip_)
        #         # scmd = f'{root_dir}/ebin/python3 -m pip install {pip_} --no-index --no-cache-dir --find-links="{ext_whl_path}"  --force-reinstall  --ignore-installed ' 
        #         # print(scmd)
        #         # os.system(scmd)
        #         os.system(f"rm -f {root_dir}/local/lib/python3.8/site-packages/typing.*")



        if self.pp.terra.projects:
            nodes_ = self.pp.terra.projects
            if self.args.debug:
                nodes_ += (self.pp.build.projects or [])
            for td_ in nodes_:
                git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)
        
                os.chdir(setup_path)
                # make_setup_if_not_exists()
                if setup_path.endswith('pip'):
                    continue
                # if 'dm-psi' in setup_path:
                #     wrrr = 1
                # if '18' in setup_path:
                #     wrrr = 1

                release_mod = ''

                # scmd = "%(root_dir)s/ebin/python3 setup.py install --single-version-externally-managed  %(release_mod)s --root / --force   " % vars()
                self.cmd(f"{root_dir}/ebin/python3 setup.py install --single-version-externally-managed  {release_mod} --root / --force  ")  #--no-deps

                # os.chdir(setup_path)
                # for reqs_ in glob.glob(f'**/package.json', recursive=True):
                #     if not 'node_modules' in reqs_:
                #         os.chdir(setup_path)
                #         dir_ = os.path.split(reqs_)[0]
                #         if dir_:
                #             os.chdir(dir_)
                #         os.system(f"yarn install ")
                #         os.system(f"yarn build ")


        if self.tvars.fc_version == '32':
            scmd = f"rm -f {root_dir}/local/lib/python3.8/site-packages/typing.*"
        print(scmd)
        os.system(scmd)
        pass


    def install_repos(self):
        root_dir = self.root_dir
        args = self.args
        packages = []
        lines = []

    
        for rp_ in self.ps.repos or []:
            if rp_.lower().endswith('.gpg'):
                lines.append(f'sudo rpm --import {rp_} ')
            elif rp_.endswith('.rpm'):
                lines.append(f'sudo dnf install --nogpgcheck {rp_} -y ')
            else:
                lines.append(f'sudo dnf config-manager --add-repo {rp_} -y ')
            pass

        self.lines2sh("00-install-repos", lines, "install-repos")    
        pass


    def download_packages(self):
        root_dir = self.root_dir
        args = self.args
        packages = []
        lines = []

        base = dnf.Base()
        base.fill_sack()
        q_ = base.sack.query()
        self.installed_packages = q_.installed()
    
        lines = []
        lines_src = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)

        scmd = f"rm -rf '{in_bin}/rpms'"
        lines.append(scmd)
        scmd = "sudo yum-config-manager --enable remi"
        lines.append(scmd)
        pls_ = [p for p in self.need_packages + self.ps.build + self.ps.terra if isinstance(p, str)]
        purls_ = [p.url for p in self.need_packages + self.ps.build + self.ps.terra if not isinstance(p, str)]

        packages = " ".join(self.dependencies(pls_, local=False) + purls_)    
        scmd = 'dnf download --skip-broken --downloaddir "%(in_bin)s/rpms" --arch=x86_64  --arch=x86_64 --arch=noarch  %(packages)s -y ' % vars()
        lines.append(scmd)
        scmd = 'dnf download --skip-broken --downloaddir "%(in_bin)s/src-rpms" --arch=x86_64 --arch=noarch  --source %(packages)s -y ' % vars()
        lines_src.append(scmd)

        for pack_ in self.ps.remove_from_download or []:
            scmd = f'rm -f {in_bin}/rpms/{pack_}* '
            lines.append(scmd)

        # for package in self.dependencies(pls_, local=False) + purls_:
        #     # потом написать идемпотентность, проверки на установленность, пока пусть долго, по одному ставит
        #     scmd = 'dnf download --downloaddir "%(in_bin)s/rpms" --arch=x86_64 "%(package)s" -y ' % vars()
        #     lines.append(scmd)
        #     scmd = 'dnf download --downloaddir "%(in_bin)s/src-rpms" --arch=x86_64 --source "%(package)s" -y ' % vars()
        #     lines_src.append(scmd)


        self.lines2sh("01-download-rpms", lines, "download-rpms")    
        self.lines2sh("90-download-sources-for-rpms", lines_src, "download-sources-for-rpms")    

        shfilename = "02-install-rpms"    
        ilines = [
"""
sudo dnf install --nogpgcheck --skip-broken %(in_bin)s/rpms/*.rpm -y --allowerasing
""" % vars()
        ]
        self.lines2sh("02-install-rpms", ilines, "install-rpms")    

        # self.lines2sh("03-download-rpms", lines, "download-rpms")    
        # self.lines2sh("04-install-rpms", ilines, "install-rpms")    
        pass


    def build_wheels(self):
        os.chdir(self.curdir)
        bindir_ = os.path.abspath(self.in_bin)
        lines = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)
        wheelpath = os.path.join(self.in_bin, "ourwheel")
        relwheelpath = os.path.relpath(wheelpath, start=self.curdir)
        lines.append(R"rm -rf %(relwheelpath)s/*.*" % vars())
        for td_ in self.pp.projects():
        # , local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in (self.pp.terra if self.pp.terra else [])]:
            git_url, git_branch, path_to_dir_, setup_path = self.explode_pp_node(td_)
            path_to_dir = os.path.relpath(path_to_dir_, start=self.curdir)
            relwheelpath = os.path.relpath(wheelpath, start=path_to_dir_)
            # scmd = "pushd %s" % (path_to_dir)
            # lines.append(scmd)
            scmd = f"""
pipenv run sh -c "pushd {path_to_dir};python3 setup.py clean --all;python3 setup.py bdist_wheel -d {relwheelpath};popd" 
"""
            lines.append(scmd)
            pass
        self.lines2sh("06-build-wheels", lines, "build-wheels")


#     def preinstall_wheels(self):
#         os.chdir(self.curdir)

#         ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)

#         lines = []
#         scmd = f'''
# pipenv --rm
# rm -f Pipfile*
# pipenv install --python {self.tvars.python_version_1}.{self.tvars.python_version_2}
# pipenv run python3 -m pip install ./in/bin/extwheel/*.whl --find-links="{ext_whl_path}"  --force-reinstall --ignore-installed  --no-cache-dir --no-index 
# ''' 
#         lines.append(scmd)   
#         self.lines2sh("08-preinstall-wheels", lines, "preinstall-wheels")
#         pass    

    def init_env(self):
        os.chdir(self.curdir)

        ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)

        lines = []
        scmd = f'''
python -m pipenv --rm
rm -f Pipfile*
sudo python -m pip uninstall virtualenv
python -m pipenv install --python {self.tvars.python_version_1}.{self.tvars.python_version_2}
python -m pipenv install setuptools_git_versioning wheel
''' 
        lines.append(scmd)   
        self.lines2sh("04-init-env", lines, "init-env")
        pass    

    def install_wheels(self):
        os.chdir(self.curdir)
        lines = []

        our_whl_path = os.path.relpath(self.our_whl_path, self.curdir)
        ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)

        scmd = f'''
pipenv --rm
pipenv install --python {self.tvars.python_version_1}.{self.tvars.python_version_2}
pipenv run python3 -m pip install ./in/bin/ourwheel/*.whl ./in/bin/extwheel/*.whl --find-links="{our_whl_path}" --find-links="{ext_whl_path}"  --force-reinstall --ignore-installed  --no-cache-dir --no-index 
''' 
        lines.append(scmd)   # --no-cache-dir
        self.lines2sh("15-install-wheels", lines, "install-wheels")
        pass    

    def get_pip_targets_and_reqs_from_sources(self, terra=False):
        '''
        Analyse sources and get list of targets, likely python packages, and "requirements.txt"
        for python code without packaging
        '''
        os.chdir(self.curdir)
        os.chdir(self.out_dir)

        root_dir = self.root_dir
        args = self.args

        bin_dir = os.path.relpath(self.in_bin, start=self.curdir)

        pip_targets = []
        pip_reqs = []
        projects = []


        if terra:
            projects += self.pp.terra.projects or []
            pip_targets += self.pp.terra.pip or []
        else:
            projects += self.pp.projects()
            pip_targets += self.pp.pip()

        for td_ in projects:
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)
            if not os.path.exists(setup_path):
                continue
            os.chdir(setup_path)
            
            is_python_package = False
            for file_ in ['setup.py', 'pyproject.toml', 'requirements.txt']:
                if os.path.exists(file_):
                    is_python_package = True
                    break

            if is_python_package:
                pip_targets.append(os.path.relpath(setup_path, start=self.curdir))

            reqs_path = 'requirements.txt'
            for reqs_ in glob.glob(f'**/{reqs_path}', recursive=True):
                if 'django-q' in setup_path:
                    rewqwerew=1
                with open(reqs_, 'r', encoding='utf-8') as lf:
                    reqs__ = lf.read()
                    if not '--hash=sha256' in reqs__:
                        # very strange requirements.txt, we cannot download it.
                        pip_reqs.append(os.path.join(os.path.relpath(setup_path, start=self.curdir), reqs_))
                    else:
                        print(f'«--hash=sha256» in {setup_path}/{reqs_}')
                        
            pass

        return pip_targets, pip_reqs

    def pip_args_from_sources(self, terra=False):
        pip_targets, pip_reqs = self.get_pip_targets_and_reqs_from_sources(terra=terra)
        # pip_targets += self.pp.pip()

        pip_targets_ = " ".join(pip_targets)
        pip_reqs_ = " ".join([f" -r {r} " for r in pip_reqs])
        return f" {pip_reqs_} {pip_targets_} "



    def download_pip(self):
        '''
        Consistent downloading all needed pip wheel packages
        '''
        os.chdir(self.curdir)
        os.chdir(self.out_dir)

        root_dir = self.root_dir
        args = self.args

        bin_dir = os.path.relpath(self.in_bin, start=self.curdir)

        lines = []
        lines.append('''
rm -f %s/extwheel/*        
''' % bin_dir)

        pip_args_ = self.pip_args_from_sources()

        scmd = f"python3 -m pip download wheel {pip_args_} --dest {bin_dir}/extwheel --find-links='{bin_dir}/ourwheel' "  
        lines.append(scmd)                

        scmd = f"rm -f {bin_dir}/extwheel/enum34* "
        lines.append(scmd)                

        scmd = f"""
pushd {bin_dir}/extwheel
ls *.tar.* | xargs -i[] -t python3 -m pip wheel [] --no-deps
rm -f *.tar.*
popd
python -c "import os; whls = [d.split('.')[0]+'*' for d in os.listdir('{bin_dir}/ourwheel')]; os.system('cd {bin_dir}/extwheel; rm -f ' + ' '.join(whls))"
""" 
        lines.append(scmd)                
        self.lines2sh("07-download-wheels", lines, "download-wheels")
        pass    


    def analyse(self):    
        '''
        Analyse strace file to calculate unused files.
        '''
        args = self.args
        spec = self.spec
        abs_path_to_out_dir = os.path.abspath(args.analyse)
        root_dir = self.root_dir 
        lastdirs = os.path.sep.join(abs_path_to_out_dir.split(os.path.sep)[-3:])

        trace_file = None
        try:
            trace_file_glob = spec.tests.tracefile
        except:
            print('You should specify tests→tracefile')    
            return

        used_files = set()
        for trace_file in glob.glob(trace_file_glob):
            re_file = re.compile(r'''.*\(.*"(?P<filename>[^"]+)".*''')
            for line in open(trace_file, 'r', encoding='utf-8').readlines():
                m_ = re_file.match(line)
                if m_:
                    fname = m_.group('filename')
                    # Heuristic to process strace files from Vagrant virtualboxes
                    fname = fname.replace('/vagrant', self.curdir)
                    # Heuristic to process strace files from remote VM, mounted by sshmnt
                    fname = re.sub(fr'''/mnt/.*{lastdirs}''', abs_path_to_out_dir, fname)
                    if os.path.isabs(fname):
                        fname = os.path.abspath(fname)
                        if fname.startswith(abs_path_to_out_dir):
                            if os.path.islink(fname):
                                link_ = os.readlink(fname)
                                fname = os.path.abspath(os.path.join(os.path.split(fname)[0], link_))
                            used_files.add(os.path.abspath(fname))

        existing_files = {}
        for dirpath, dirnames, filenames in os.walk(abs_path_to_out_dir):
            for filename in filenames:
                fname_ = os.path.join(abs_path_to_out_dir, dirpath, filename)
                fname_ = os.path.abspath(fname_)
                if 'cv2.cpython-38-x86_64-linux-gnu.so' in filename:
                    wtff = 1
                if fname_ not in used_files:
                    if not os.path.islink(fname_):
                        size_ = os.stat(fname_).st_size
                        existing_files[fname_] = size_

        top10 = sorted(existing_files.items(), key=lambda x: -x[1])[:1000]
        print("Analyse first:") 
        for f, s in top10:
            rel_f = f.replace(abs_path_to_out_dir, '')
            unban_ = False
            for unban in [#'/usr/lib64/python3.9','/lib64/python3.9'
                        ]:
                if unban in rel_f:
                    unban_ = True
                    breakpoint
            if unban_ :
                continue        
            # f_ = re.escape(rel_f)
            f_ = rel_f
            for rex_ in [
                r"\.so\.[\d]+\.[\d]+\.[\d]+", #act\.so\.4\.0\.1 → act\.so\.\d\.\d\.\d 
                r"\.so\.[\d]+\.[\d]+", 
                r"\.so\.[\d]+", 
                r"[\d]+\.[\d]+\.so", 
                r"[\d]+\.so", 
                r"-[\d]+\.[\d]+-[\d]+", 
            ]:
                def replacement_f(mobj):
                    return rex_
                f_ = re.sub(rex_, replacement_f, f_)
            if f_ == rel_f:    
               f_ = re.escape(rel_f)

            print(f'      - .*{f_} # \t {s} \t {rel_f}')

        # print("\n".join([f'{f}: \t {s}' for f,s in top10]))

        pass


    def pack_me(self):    
        time_prefix = datetime.datetime.now().replace(microsecond=0).isoformat().replace(':', '-')
        parentdir, curname = os.path.split(self.curdir)
        disabled_suffix = curname + '.tar.bz2'

        banned_ext = ['.old', '.iso', '.lock', disabled_suffix, '.dblite', '.tmp', '.log']
        banned_start = ['tmp']
        banned_mid = ['/out', '/wtf', '/ourwheel/', '/.vagrant', '/.git', '/.vscode', '/key/', '/tmp/', '/src.', '/bin.',  '/cache_', 'cachefilelist_', '/.image', '/!']

        # there are regularly some files unaccessable for reading.
        self.cmd('sudo chmod a+r /usr/lib/cups -R')
        self.cmd('systemd-tmpfiles --remove dnf.conf')

        def filter_(tarinfo):
            for s in banned_ext:
                if tarinfo.name.endswith(s):
                    print(tarinfo.name)
                    return None

            for s in banned_start:
                if tarinfo.name.startswith(s):
                    print(tarinfo.name)
                    return None

            for s in banned_mid:
                if s in tarinfo.name:
                    print(tarinfo.name)
                    return None

            return tarinfo          


        tbzname = os.path.join(self.curdir, 
                "%(time_prefix)s-%(curname)s.tar.bz2" % vars())
        tar = tarfile.open(tbzname, "w:bz2")
        tar.add(self.curdir, "./sources-for-audit", recursive=True, filter=filter_)
        tar.close()    

    def remove_exclusions(self):
        '''
        Postprocessing, removing not needed files after installing python modules, etc
        '''
        from pathlib import Path
        for path in Path(self.root_dir).rglob('*'):
            rp_ = str(path.absolute())
            if '/mildata/baselines_model.ckpt' in rp_:
                sfdsfds =1
            if self.br.is_need_exclude(rp_):
                    path.unlink(missing_ok=True)

        # killing broken links    
        for path in Path(self.root_dir).rglob('*'):
            rp_ = str(path.absolute())
            if os.path.islink(rp_) and not os.path.exists(rp_):
                    path.unlink(missing_ok=True)

        pass            

    def process(self):
        '''
        Основная процедура генерации переносимого питон окружения.
        '''
        
        args = self.args
        spec = self.spec
        root_dir = self.root_dir 
        
        def install_templates(root_dir, args):
            if 'prepare_install' in self.spec:
                for scmd in self.spec.prepare_install.strip().split('\n'):
                    self.cmd(scmd)


            if 'copy_folders' in self.spec:
                for it_ in self.spec.copy_folders:
                    pass
                from_ = os.path.join(self.src_dir, it_['from'])
                to_ = os.path.join(root_dir, it_['to'])
                mkdir_p(to_)

                # from distutils.dir_util import copy_tree
                # # copy_tree(from_, to_)
                # copy_tree(from_, to_, preserve_symlinks=True)
                # All standard python copy_tree is broken
                # https://bugs.python.org/issue41134
                # https://stackoverflow.com/questions/53090360/python-distutils-copy-tree-fails-to-update-if-there-are-symlinks
                scmd = f'rsync -rav {from_}/ {to_}'
                print(scmd)
                os.system(scmd)

                wtfff=1

            from jinja2 import Environment, FileSystemLoader, Template
            t.tic()
    
            for td_ in spec.templates_dirs:
                git_url, git_branch, path_to_dir, _ = self.explode_pp_node(td_)
                if 'subdir' in td_:
                    path_to_dir = os.path.join(path_to_dir, td_.subdir)
            
                file_loader = FileSystemLoader(path_to_dir)
                env = Environment(loader=file_loader)
                env.filters["hash"] = j2_hash_filter    
                env.trim_blocks = True
                env.lstrip_blocks = True
                env.rstrip_blocks = True            
                
                print(path_to_dir)
                os.chdir(path_to_dir)
                for dirpath, dirnames, filenames in os.walk('.'):
                    if '.git' in dirpath:
                        continue
                    for dir_ in dirnames:
                        out_dir = os.path.join(root_dir, dirpath, dir_)
                        print(out_dir)
                        mkdir_p(out_dir)
                        # if not os.path.exists(out_dir):
                        #     os.mkdir(out_dir)
                        
                    for filename in filenames:
                        fname_  = os.path.join(dirpath, filename)
                        if 'python' in fname_:
                            wtf=1
                        out_fname_ = os.path.join(root_dir, dirpath, filename)
                        out_fname_ = Template(out_fname_ ).render(self.tvars)                    
                        
                        plain = False
                        try:
                            if 'users.xml' in fname_:
                                dfdsfdsf=1
                            m = fucking_magic(fname_)
                            if 'text' in m:
                                plain = True
                        except Exception:
                            pass
                        if os.path.islink(fname_):
                            linkto = os.readlink(fname_)
                            os.symlink(linkto, out_fname_)
                        else:    
                            if plain or fname_.endswith('.nj2'):
                                template = env.get_template(fname_)
                                output = template.render(self.tvars)                    
                                with open(out_fname_, 'w', encoding='utf-8') as lf_:
                                    lf_.write(output)
                            else:
                                shutil.copy2(fname_, out_fname_)
                            shutil.copymode(fname_, out_fname_)                        
            
            from ctypes.util import _findLib_ld
            libc_path = _findLib_ld('c')
            libc_path = "/lib64/libc-2.31.so" # temp hack

            from ctypes.util import _findSoname_ldconfig
            libc_path = "/lib64/" + _findSoname_ldconfig('c')
            shutil.copy2(libc_path, f'{root_dir}/lib64/libc.so', follow_symlinks=True)

            ebin_ = os.path.join(root_dir, 'ebin')
            self.cmd(f'chmod a+x {ebin_}/*')

            print("Install templates takes")
            t.toc()
            pass

        if self.args.folder_command:
            self.folder_command()
            return

        if self.args.analyse:
            self.analyse()
            return

        if self.args.stage_pack_me:
            self.pack_me()
            return

        # if self.args.stage_checkout:


        self.install_repos()
        self.download_packages()
        self.checkout_sources()
        self.download_pip()
        # self.preinstall_wheels()
        self.init_env()
        self.build_wheels()
        self.install_wheels()
        self.build_nuitkas()
        self.build_go()
        # self.install_packages()

        # if self.args.stage_build_nuitka:
        # self.install_localpythons()
        # self.build_nuitkas()
            # return

        try:
            output_ = subprocess.check_output('pipenv run pip list --format json', shell=True)            
            json_ = json.loads(output_)

            rows_ = []
            for r_ in json_:
                rows_.append([r_['name'], r_['version']])

            write_doc_table('doc-python-packages.htm', ['Package', 'Version'], sorted(rows_))
        except Exception as ex_:
            print(ex_)
            pass    

        specfile_ = self.args.specfile
        self.lines2sh("50-pack", [
            '''
#terrarium_assembler --stage-pack=./out "%(specfile_)s" --stage-make-isoexe
terrarium_assembler --stage-pack=./out "%(specfile_)s" 
            ''' % vars()])

        self.lines2sh("51-pack-iso", [
            '''
sudo chmod a+rx /usr/lib/cups -R           
terrarium_assembler --stage-pack=./out "%(specfile_)s" --stage-make-isoexe
            ''' % vars()])

        self.lines2sh("91-pack-debug", [
            '''
sudo chmod a+rx /usr/lib/cups -R           
terrarium_assembler --debug --stage-pack=./out-debug "%(specfile_)s" 
            ''' % vars()])

        self.lines2sh("92-pack-debug-iso", [
            '''
sudo chmod a+rx /usr/lib/cups -R           
terrarium_assembler --debug --stage-pack=./out-debug "%(specfile_)s" --stage-make-isoexe
            ''' % vars()])

        root_dir = 'out'


        if self.args.stage_pack:
            root_dir = self.root_dir = expandpath(args.stage_pack)
            # self.remove_exclusions()

            cloc_csv = 'tmp/cloc.csv'
            if not os.path.exists(cloc_csv):
                if shutil.which('cloc'):
                    os.system('cloc ./in/src/ --csv  --report-file=tmp/cloc.csv --3')
            if os.path.exists(cloc_csv):
                table_csv = []
                with open(cloc_csv, newline='') as csvfile:
                    csv_r = csv.reader(csvfile, delimiter=',', quotechar='|')
                    for row in list(csv_r)[1:]:
                        row[-1] = int(float(row[-1]))
                        table_csv.append(row)

                table_csv[-1][-2], table_csv[-1][-1] = table_csv[-1][-1], table_csv[-1][-2]       
                write_doc_table('doc-cloc.htm', ['Файлов', 'Язык', 'Пустых', 'Комментариев', 'Строчек кода', 'Мощность языка', 'COCOMO строк'], 
                                table_csv)        

            # install_templates(root_dir, args)

            packages_to_deploy = []
            pips_to_deploy = []
            if self.args.debug:
                packages_to_deploy += self.ps.terra + self.ps.build
                pips_to_deploy = self.pp.pip()
            else:
                packages_to_deploy = self.ps.terra
                pips_to_deploy = self.pp.terra.pip or []


            fs_ = self.generate_file_list_from_pips(pips_to_deploy)
            file_list = self.generate_file_list_from_packages(self.dependencies(packages_to_deploy))
            if '/lib/libpthread-2.31.so' in fs_:
                dfsfdf = 1
            if '/lib/libpthread-2.31.so' in file_list:
                dfsfdf = 1
            if 'java-11' in file_list:
                fdsfsdfds=1
            file_list.extend(fs_)

            os.system('echo 2 > /proc/sys/vm/drop_caches ')
            if os.path.exists(root_dir + ".old"):
                shutil.rmtree(root_dir + ".old", ignore_errors=True)
            if os.path.exists(root_dir + ".old"):
                os.system("rm -rf " + root_dir + ".old")
            if os.path.exists(root_dir):
                shutil.move(root_dir, root_dir + ".old")

            mkdir_p(root_dir)    
        
            def copy_file_to_environment(f):
                if 'libc-2.31.so' in f:
                    wtff = 1
                if not self.should_copy(f):
                    return
                if 'libpthread-2.31.so' in f:
                    wtff = 1

                if 'java-11' in f:
                    wtff = 1

                if self.br.is_need_patch(f):  
                    self.process_binary(f)
                    self.add(f)
                elif self.br.is_just_copy(f):
                    self.add(f)
                elif self.args.debug and f.startswith("/usr/include"):
                    self.add(f)
                else:
                    libfile = f
                    # python tends  install in both /usr/lib and /usr/lib64, which doesn't mean it is
                    # a package for the wrong arch. 
                    # So we need to handle both /lib and /lib64. Copying files
                    # blindly from /lib could be a problem, but we filtered out all the i686 packages during
                    # the dependency generation.
                    if libfile.startswith("/usr/local/"):
                        libfile = libfile.replace("/usr/local/", "/", 1)
                    
                    if libfile.startswith("/usr/"):
                        libfile = libfile.replace("/usr/", "/", 1)
            
                    if libfile.startswith("/lib/"):
                        libfile = libfile.replace("/lib/", "lib64/", 1)
                    elif libfile.startswith("/lib64/"):
                        libfile = libfile.replace("/lib64/", "lib64/", 1)
                    else:
                        return 
            
                    # copy file instead of link unless we link to the current directory.
                    # links to the current directory are usually safe, but because we are manipulating
                    # the directory structure, very likely links that transverse paths will break.
                    # os.path.islink(f) and os.readlink(f) != os.path.basename(os.readlink(f)):
                    #     rp_ = os.path.realpath(f)
                    #     if os.path.exists(rp_):
                    #         add(os.path.realpath(f), libfile)
                    if 1:
                        if not os.path.exists(f):
                            print("Missing %s" % f)
                            return
                            # # assert(False)
                        try:    
                            m = fucking_magic(f)
                        except Exception as ex_:
                            print("Cannot detect Magic for ", f)    
                            raise ex_
                        if m.startswith('ELF') and 'shared' in m:
                        # startswith('application/x-sharedlib') or m.startswith('application/x-pie-executable'):
                            self.fix_sharedlib(f, libfile)
                        else:
                            # in case this is a directory that is listed, we don't want to include everything that is in that directory
                            # for instance, the python3 package will own site-packages, but other packages that we are not packaging could have
                            # filled it with stuff.
                            self.add(f, libfile, recursive=False)
                            # shutil.copy2(f, os.path.join(root_dir, libfile))
                            # add(f, arcname=libfile, recursive=False)  
                pass
                # if os.path.exists('/home/stas/projects/deploy-for-audit/linux_distro/out/lib64/jvm/java-11-openjdk-11.0.11.0.9-4.fc33.x86_64-slowdebug/lib/modules'):
                #     fsdfsdf = 1  

        
            dfsfsdfsdf=1
            with open('file-list-from-packages.txt', 'w', encoding='utf-8') as lf:
                lf.write('\n'.join(file_list))

            self.cmd('sudo chmod a+r /usr/lib/cups -R')
            for f in file_list:
                copy_file_to_environment(f)

            # if os.path.exists('/home/stas/projects/deploy-for-audit/linux_distro/out/lib64/jvm/java-11-openjdk-11.0.11.0.9-4.fc33.x86_64-slowdebug/lib/modules'):
            #     fsdfsdf = 1  

            if self.fs:    
                for folder_ in self.fs.folders:
                    for dirpath, dirnames, filenames in os.walk(folder_):
                        for filename in filenames:
                            f = os.path.join(dirpath, filename)
                            # if self.br.is_need_exclude(f):
                            #     continue
                            # if not self.should_copy(f):
                            #     continue
                            if self.br.is_need_patch(f):  
                                self.process_binary(f)
                                continue
                            libfile = os.path.join(self.root_dir, f.replace(folder_, 'pbin'))
                            if 'java-11' in libfile:
                                erwerew =1
                            # if self.br.is_need_exclude(libfile):
                            #     continue
                            self.add(f, libfile, recursive=False)
                    pass




            install_templates(root_dir, args)
            self.install_terra_pythons()
            # install_templates(root_dir, args)
            # self.install_terra_pythons()

            # if self.args.debug:    
            #     self.overwrite_mode = True
            #     for f in file_list:
            #         copy_file_to_environment(f)


            os.chdir(root_dir)
            scmd = "%(root_dir)s/ebin/python3 -m compileall -b . " % vars()
            print(scmd)
            os.system(scmd)
    
            if 0 and not self.args.debug:
                # Remove source files.
                scmd = "shopt -s globstar; rm  **/*.py; rm  -r **/__pycache__"
                print(scmd)
                os.system(scmd)
                pass

            with open(f'{self.curdir}/obsoletes_excludes.txt', 'wt', encoding='utf-8') as lf:
                lf.write('obsoletes excludes \n')
                for re_, cnt_ in self.br.need_exclude_re.items():
                    if cnt_ == 0:
                        pat_ = re_.pattern
                        lf.write(f'   {pat_} \n')

            # size_ = sum(file.stat().st_size for file in pathlib.Path(self.root_dir).rglob('*'))
            
            # Postprocessing, removing not needed files after installing python modules, etc
            self.remove_exclusions()
            
            size_  = folder_size(self.root_dir, follow_symlinks=False)

            print("Size ", size_/1024/1024, 'Mb')

        if self.args.stage_make_isoexe:
            from dateutil.relativedelta import relativedelta            

            os.chdir(self.curdir)

            isodir = root_dir + '.iso'
            mkdir_p(isodir)
            old_isos = [f_ for f_ in os.listdir(isodir) if f_.endswith('.iso')]
            
            current_time = datetime.datetime.now().replace(microsecond=0) 
            prev_release_time = current_time + relativedelta(months=-1)
            
            for iso_ in reversed(sorted(old_isos)):
                try:
                    prev_release_time = datetime.datetime.strptime(iso_[:19], '%Y-%m-%dT%H-%M-%S')
                    break
                except:    
                    pass

            since_time_ = prev_release_time.isoformat()
            gitlogcmd_ = f'git log --since="{since_time_}" --pretty --name-status '

            lines_ = []
            for git_url, git_branch, path_to_dir_ in self.get_all_sources():
                os.chdir(self.curdir)
                if os.path.exists(path_to_dir_):
                    os.chdir(path_to_dir_)
                    with suppress(Exception):
                        change_ = subprocess.check_output(gitlogcmd_, shell=True).decode('utf-8').strip()
                        if change_:
                            lines_.append(f'----\n Changelog for {path_to_dir_} ({git_url} / {git_branch})')
                            lines_.append(change_)

            pass


            # current_time = datetime.datetime.now().replace(microsecond=0) 
            time_prefix = current_time.isoformat().replace(':', '-')
            label = 'disk'
            if 'label' in self.spec:
                label = self.spec.label
            installscript = "install-me.sh" % vars()
            os.chdir(self.curdir)
            installscriptpath = os.path.abspath(os.path.join("tmp/", installscript))
            if os.path.exists(installscriptpath):
                os.unlink(installscriptpath)

            pmode = ''
            if shutil.which('pbzip2'):
                pmode = ' --threads 8 --pbzip2 ' 
            os.chdir(self.curdir)
            self.cmd(f'chmod a+x {root_dir}/install-me')

            filename = f"{time_prefix}-{label}-dm.iso" % vars()
            with suppress(Exception):
                chp_ = os.path.join(root_dir, 'isodistr.txt')
                open(chp_, 'w', encoding='utf-8').write(filename)

            scmd = (f'''
            makeself.sh {pmode} --needroot {root_dir} {installscriptpath} "Installation" ./install-me             
        ''' % vars()).replace('\n', ' ').strip()
            if not self.cmd(scmd)==0:
                print(f'« {scmd} » failed!')
                return
            os.chdir(self.curdir)
            changelogfilename = filename + '.changelog.txt'

            filepath = os.path.join(isodir, filename)
            scmd = ('''
        mkisofs -r -J -o  %(filepath)s  %(installscriptpath)s 
        ''' % vars()).replace('\n', ' ').strip()
            os.chdir(self.curdir)
            self.cmd(scmd)
            scmd = (f'''
        md5sum {filepath}
        ''').replace('\n', ' ').strip()
            os.chdir(self.curdir)
            md5s_ = subprocess.check_output(scmd, shell=True).decode('utf-8').strip().split()[0]
            lines_.insert(0, f';MD5: {md5s_}')

            with suppress(Exception):
                chp_ = os.path.join(isodir, changelogfilename)
                open(chp_, 'w', encoding='utf-8').write('\n'.join(lines_))
                open(f'{filepath}.md5', 'w', encoding='utf-8').write(md5s_)

            os.chdir(isodir)
            scmd = f'''ln -sf {filename} last.iso'''
            self.cmd(scmd)
            print(filepath)
    
