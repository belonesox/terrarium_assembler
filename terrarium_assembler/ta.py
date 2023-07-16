"""Main module."""
from typing import List
import argparse
import os
import subprocess
import shutil
import sys
import stat
import re
import json
import dataclasses as dc
import datetime
import hashlib
import time
import glob
import csv
import jinja2.exceptions
import version_utils.rpm

from imohash import hashfile
#from simplekv.fs import FilesystemStore
from itertools import islice


from jinja2 import Environment, FileSystemLoader, Template
from tempfile import mkstemp


from contextlib import suppress
from pathlib import Path, PurePath
from packaging import version

from .utils import *
from dateutil.relativedelta import relativedelta

# будет отключено
# from .nuitkaflags import *

# новая ветка
from .nuitkaprofiles import *
from .python_rebuild_profiles import *


from pytictoc import TicToc
t = TicToc()

ROW_SPLIT = ' ||| '

@dc.dataclass
class PackageFileRow:
    '''
    Info about file in package
    '''
    package: str
    version: str
    release: str
    buildtime: str
    buildhost: str
    filename: str


@dc.dataclass
class FileInBuild:
    '''
    Info about file in out build
    '''
    relname: str
    source_type: str
    source: str
    source_path: str

    def __post_init__(self):
        assert(self.relname)
        assert(not self.relname.startswith('/'))
        assert(self.source_type)
        assert(self.source)
        assert(self.source_path)

@dc.dataclass
class BinRegexps:
    '''
    Binary regexps.
    '''
    need_patch: list  # bins that need to be patched.
    just_copy:  list  # bins that just need to be copied.
    need_exclude: dict  # bins that just need to be copied.
    debug: bool
    # optional_patcher: str # path to script for optional patching

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
        self.ignore_re = {}
        if 'ignore' in self.need_exclude:
            add_listrex2dict(self.need_exclude.ignore, self.ignore_re)

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
        if 'libtorch_cuda.so' in f:
            wtf = 34342
        for re_ in self.ignore_re:
            if re_.match(f):
                return False
        for re_ in self.need_exclude_re:
            if re_.match(f):
                cnt_ = self.need_exclude_re[re_]
                if not cnt_:
                    cnt = 0
                self.need_exclude_re[re_] = cnt_ + 1
                return True
        return False

    def is_needed(self, f):
        return (self.is_just_copy(f) or self.is_need_patch(f))
        # and not self.is_need_exclude(f)

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
    rebuild: list = None
    remove_from_download: list = None
    shell_commands: list = None

    def __post_init__(self):
        '''
        Recode from easydicts to objects
        '''
        self.build = PythonPackagesSpec(**self.build)
        self.terra = PythonPackagesSpec(**self.terra)
        # if 'remove_from_download' not in dir(self):
        #     self.remove_from_download = []
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
    rebuild:  list
    rebuild_disable_features: list
    terra_exclude: list    
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

        if not self.rebuild:
            self.rebuild = []

        if not self.terra:
            self.terra = []

        if not self.terra_exclude:
            self.terra_exclude = []

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
        # self.curdir = os.getcwd()
        self.ta_str_time =  datetime.datetime.now().replace(microsecond=0).isoformat().replace(':', '').replace('-', '').replace('T', '')

        self.root_dir = None
        self.toolbox_mode = True
        self.build_mode = False

        self.container_info = None
        self.container_path = None        

        self.patching_dir = 'tmp/patching'
        mkdir_p(self.patching_dir)

        self.file_list_from_terra_rpms = 'tmp/file-list-from-terra-rpms.txt'
        self.terra_rpms_closure = 'tmp/terra-rpms-closure.txt'
        self.doc_list_from_terra_rpms = 'tmp/doc-list-from-terra-rpms.txt'
        self.file_list_from_deps_rpms = 'tmp/file-list-from-deps-rpms.txt'
        self.doc_list_from_deps_rpms = 'tmp/doc-list-from-deps-rpms.txt'
        self.so_files_from_venv = 'tmp/so-files-from-venv.txt'
        self.so_files_from_rebuilded_pips = 'tmp/so-files-from-rebuilded-pips.txt'
        self.so_files_from_our_packages = 'tmp/so-files-from-our-packages.txt'
        self.file_list_from_rpms = 'tmp/file-list-from-rpm.txt'
        self.file_package_list_from_rpms = 'tmp/file-package-list-from-rpm.txt'
        self.src_deps_packages = 'tmp/src_deps_packages.txt'
        self.src_deps_packages_main = 'tmp/src_deps_packages_main.txt'
        self.src_deps_packages_add = 'tmp/src_deps_packages_add.txt'
        self.glibc_devel_packages = 'tmp/glibc_devel_packages.txt'
        self.files_source_path = 'tmp/files-source.txt'
        # self.doc_list_from_rpms = 'tmp/doc-list-from-rpm.txt'
        self.pipdeptree_graph_dot = 'tmp/pipdeptree-graph.dot'
        self.pipdeptree_graph_mw = 'tmp/pipdeptree-graph.mw'
        self.pip_list = 'tmp/pip-list.txt'
        self.pip_list_json = 'tmp/pip-list.json'

        self.out_interpreter = "pbin/ld.so"
        self.bin_files_name = "tmp/bin-files.txt"

        self.bin_files = set()
        self.bin_files_sources = {}

        self.changelogdir= 'changelogs'
        mkdir_p(self.changelogdir)
        # Потом сделать параметром функций.
        self.overwrite_mode = False
        self.interpreter = None

        ap = argparse.ArgumentParser(
            description='Create a «portable linux folder»-application')
        # ap.add_argument('--output', required=True, help='Destination directory')
        ap.add_argument('--debug', default=False,
                        action='store_true', help='Debug version of release')
        ap.add_argument('--docs', default=False, action='store_true',
                        help='Output documentation version')

        self.stages_names = sorted([method_name for method_name in dir(self) if method_name.startswith('stage_')])
        self.stage_methods = [getattr(self, stage_) for stage_ in self.stages_names]

        self.stages = {}
        for s_, sm_ in zip(self.stages_names, self.stage_methods):
            self.stages[fname2stage(s_)] = sm_.__doc__.strip() 


        for stage, desc in self.stages.items():
            ap.add_argument(f'--{fname2option(stage)}', default=False,
                            action='store_true', help=f'{desc}')

        ap.add_argument('--analyse', default=False, action='store_true', help='Analyse resulting pack')
        ap.add_argument('--folder-command', default='', type=str,
                        help='Perform some shell command for all projects')
        ap.add_argument('--git-sync', default='', type=str,
                        help='Perform lazy git sync for all projects')
        ap.add_argument('--step-from', type=int, default=0, help='Step from')
        ap.add_argument('--step-to', type=int, default=0, help='Step from')
        ap.add_argument('specfile', type=str, help='Specification File')
        ap.add_argument('-o', '--override-spec', action='append', help='Override variable from SPEC file', default=[])


        complex_stages = {
            "stage-all": lambda stage: fname2num(stage)<60 and not 'audit' in stage,
            "stage-rebuild": lambda stage: fname2num(stage)<60 and not 'checkout' in stage and not 'download' in stage and not 'audit' in stage,
        }

        for cs_, filter_ in complex_stages.items():
            desc = []
            selected_stages_ = [fname2stage(s_) for s_ in self.stages_names if filter_(s_)]
            desc = ' + '.join(selected_stages_)
            ap.add_argument(f'--{cs_}', default=False, action='store_true', help=f'{desc}')
            
        self.args = args = ap.parse_args()

        if args.step_from or args.step_to:
            for s_ in self.stages_names:
                if args.step_from <= fname2num(s_) <= args.step_to:
                    setattr(self.args, fname2stage(s_).replace('-','_'), True)

        for cs_, filter_ in complex_stages.items():
            if vars(self.args)[cs_.replace('-','_')]:
                for s_ in self.stages_names:
                    if filter_(s_):
                        setattr(self.args, fname2stage(s_).replace('-','_'), True)


        specfile_ = expandpath(args.specfile)
        
        self.start_dir = self.curdir = os.path.split(specfile_)[0]
        terms = self.curdir.split(os.path.sep)
        terms.reverse()
        self.environ_name = '-'.join(terms[:2])
        self.tb_mod = ''
        if self.toolbox_mode:
            self.tb_mod = f'toolbox run -c {self.environ_name}'

        # self.common_cache_dir = Path('/tmp/ta_cache')
        # self.common_cache_dir.mkdir(exist_ok=True, parents=True)
        # now_ = time.time()
        # delete_time = now_ + 3600
        # for f in self.common_cache_dir.iterdir():
        #     if f.stat().st_atime > delete_time:
        #         f.unlink()

        os.environ['TERRA_SPECDIR'] = self.start_dir
        os.chdir(self.curdir)

        self.tvars = edict()
        self.tvars.python_version_1, self.tvars.python_version_2 = sys.version_info[:2]
        self.tvars.py_ext = ".pyc"
        if self.args.debug:
            self.tvars.py_ext = ".py"
        self.tvars.release = not self.args.debug
        self.tvars.fc_version = ''
        self.tvars.python_major_version = sys.version_info.major
        self.tvars.python_minor_version = sys.version_info.minor

        try:
            with open('/etc/fedora-release', 'r', encoding='utf-8') as lf:
                ls = lf.read()
                self.tvars.fc_version = re.search('(\d\d)', ls).group(1)
        except:
            pass  # Building not on Fedora.

        self.spec, vars_ = yaml_load(specfile_, self.tvars)
        self.tvars = edict(vars_)
        spec = self.spec

        for term in self.args.override_spec:
            if '=' in term:
                k_, v_ = term.split('=')
                self.spec[k_.strip()] = v_.strip()

        self.disttag = 'zzz' + str(self.spec.fc_version)   # self.disttag self.spec.label

        # self.start_dir = os.getcwd()

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

        self.package_modes = 'iso'
        if 'packaging' in self.spec:
            if isinstance(self.spec.packaging, list):
                self.package_modes = ','.join(self.spec.packaging)
            if isinstance(self.spec.packaging, str):
                self.package_modes = self.spec.packaging

        if self.args.stage_make_packages == 'default':
            self.args.stage_make_packages = self.package_modes

        self.minimal_packages = ['libtool', 'dnf-utils', 'createrepo', 'rpm-build']

        self.need_packages = ['patchelf', 'ccache', 'gcc', 'gcc-c++', 'gcc-gfortran', 'chrpath', 'makeself', 'wget',
                              'python3-wheel', 
                              'genisoimage', 'libtool', 'makeself', 'jq', 'curl', 'yum', 'nfpm', 'pandoc', 'python3-devel']

        nflags_ = {}
        if 'nuitka' in spec:
            nflags_ = spec.nuitka

        # self.nuitkas = NuitkaFlags(**nflags_)

        self.nuitka_profiles = {}
        if 'nuitka_profiles' in spec:
            self.nuitka_profiles = NuitkaProfiles(spec.nuitka_profiles)

        self.python_rebuild_profiles = PythonRebuildProfiles({})
        if 'python_rebuild_profiles' in spec:
            self.python_rebuild_profiles = PythonRebuildProfiles(spec.python_rebuild_profiles)


        if not 'rebuild' in spec.packages:
            spec.packages.rebuild = []
        if not 'terra_exclude' in spec.packages:
            spec.packages.terra_exclude = []

        if not 'rebuild_disable_features' in spec.packages:
            spec.packages.rebuild_disable_features = ['tests', 'doc']

        if not 'rebuild' in spec.python_packages:
            spec.python_packages.rebuild = []


        self.ps = PackagesSpec(**spec.packages)
        self.pp = PythonPackages(**spec.python_packages)
        self.gp = None
        if 'go_packages' in spec:
            self.gp = GoPackages(**spec.go_packages)

        fs_ = []
        if 'folders' in spec:
            fs_ = spec.folders
        self.fs = FoldersSpec(folders=fs_)

        self.in_bin = 'in/bin'
        self.src_dir = 'in/src'
        self.tmp_dir = 'tmp'
        if 'src_dir' in spec:
            self.src_dir = expandpath(self.src_dir)
        self.out_dir = 'out'
        if 'output_folder' in self.spec:
            self.out_dir = self.spec.output_folder
        mkdir_p(self.src_dir)
        mkdir_p(self.out_dir)
        mkdir_p(self.in_bin)
        mkdir_p('tmp')

        def in_bin_fld(subfolder):
            folder_ = os.path.join(self.in_bin, subfolder)
            mkdir_p(folder_)
            return folder_

        def rpmrepo(subfolder):
            folder_ =  self.rpmrepo_path + '/' + subfolder
            mkdir_p(folder_)
            return folder_

        def tmp_fld(subfolder):
            folder_ = os.path.join(self.tmp_dir, subfolder)
            mkdir_p(folder_)
            return folder_

        self.rpmrepo_path = in_bin_fld("rpmrepo")
        self.our_whl_path = in_bin_fld("ourwheel")
        self.ext_whl_path = in_bin_fld("extwheel")
        self.rebuilded_whl_path = in_bin_fld("rebuilded_whls")
        self.pip_source_dir = in_bin_fld("pip_source_to_rebuild")
        self.ext_compiled_tar_path = in_bin_fld("ext_compiled_tar")
        self.ext_pip_path = in_bin_fld("extpip")
        self.base_whl_path = in_bin_fld("basewheel")
        self.rpms_path = rpmrepo("rpms")
        self.srpms_path = rpmrepo("srpms")
        self.build_deps_rpms = rpmrepo("build-deps-rpms")
        self.base_rpms_path = rpmrepo("base-rpms")
        self.tarrepo_path = in_bin_fld("rebuilded-repo")
        self.rpmbuild_path =  in_bin_fld("rpmbuild")
        self.rebuilded_rpms_path = in_bin_fld("rebuilded-repo/rebuilded-rpms")

        # looks like we dont need it anymore
        # os.environ['PATH'] = "/usr/lib64/ccache:" + os.environ['PATH']

        self.nuitka_plugins_dir = os.path.realpath(os.path.join(
            os.path.split(__file__)[0], '..', 'nuitka_plugins'))
        self.installed_packages_ = None

        self.optional_bin_patcher = None
        if 'optional_bin_patcher' in self.spec and os.path.exists(self.spec.optional_bin_patcher):
            self.optional_bin_patcher = self.spec.optional_bin_patcher

        self.terra_package_names = " ".join([p for p in self.ps.terra if isinstance(p, str)])

        self.packages_to_rebuild = [p for p in self.ps.terra if isinstance(p, str)] + [p for p in self.ps.rebuild if isinstance(p, str)]

        self.create_repo_cmd = f'{self.tb_mod} createrepo -x "*/BUILD/*" -x "*/BUILDROOT/*" {self.rpmrepo_path}'
        self.create_rebuilded_repo_cmd = f'{self.tb_mod} createrepo -x "*/BUILD/*" -x "*/BUILDROOT/*" {self.tarrepo_path}'
        pass

    def toolbox_create_line(self):
        if not self.toolbox_mode:
            return ''

        scmd = f'''
toolbox rm -f {self.environ_name} -y || true
toolbox create {self.environ_name} --distro fedora --release {self.spec.fc_version} -y;
'''
        return scmd
# toolbox run --container {self.environ_name} sudo dnf install -y dnf-utils


    @property
    def installed_packages(self):
        # Later we made refreshing using atomic_transformation
        if not self.installed_packages_:
            ip_file = './tmp/installed_packages'
            self.cmd(f'rpm -qa > {ip_file}')
            ps_ = []
            with open(ip_file, 'r', encoding='utf-8') as lf:
                ps_ = lf.read().strip().split('\n')
            self.installed_packages_ = []    
            for p_ in ps_:
                try:
                    self.installed_packages_.append(version_utils.rpm.package(p_))
                except:
                    pass              
        return self.installed_packages_

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

    def lines2sh(self, name, lines, stage=None, spy=False):
        os.chdir(self.curdir)

        fname = fname2shname(name, spy)
        if stage:
            stage = fname2stage(stage)

        if self.build_mode:
            if stage:
                option = stage.replace('-', '_')
                dict_ = vars(self.args)
                if option in dict_:
                    if dict_[option]:
                        print("*"*20)
                        print("Executing ", fname)
                        print("*"*20)
                        res = self.cmd("./" + fname)
                        failmsg = f'{fname} execution failed!'
                        if res != 0:
                            print(failmsg)
                        assert res==0, 'Execution of stage failed!'
            return

        with open(os.path.join(fname), 'w', encoding="utf-8") as lf:
            if spy:
                #!/usr/bin/env shellpy
                lf.write(f"#!/usr/bin/env shellpy\n")
            else:    
                lf.write(f"#!/bin/sh\n")
            lf.write(f"# Generated {name} \n ")
            def bash_line(msg):
                mod = ''
                if spy:
                    mod = '`'
                lf.write(f'''{mod}{msg}\n''')

            if stage:
                desc = '# ' + '\n# '.join(self.stages[stage].splitlines())
                stage_ = stage.replace('_', '-')
                if 'packing' in stage_:
                    stage_ += f'={self.package_modes}' 
                lf.write(f'''
{desc}
# Automatically called when terrarium_assembler --{stage_} "{self.args.specfile}"
''')

            bash_line('''export PIPENV_VENV_IN_PROJECT=1\n''')

            for k, v in self.tvars.items():
                if isinstance(v, str) or isinstance(v, int):
                    if not spy:
                        if '\n' not in str(v):
                            bash_line(f'''export TA_{k}="{v}"\n''')
            if not spy:            
                lf.write('''
set -ex
''')
            lf.write("\n".join(lines))
        st = os.stat(fname)
        os.chmod(fname, st.st_mode | stat.S_IEXEC)
        pass

    def stage_40_build_python_projects(self):
        '''
        Build/Compile Python packages to executables
        '''
        # if not self.nuitkas:
        #     return

        if not self.nuitka_profiles:
            return

        tmpdir = os.path.join(self.curdir, "tmp/ta")
        tmpdir = os.path.relpath(tmpdir)
        bfiles = []

        # First pass
        module2build = {}
        standalone2build = []
        referenced_modules = set()

        for np_name, np_ in self.nuitka_profiles.profiles.items():
            for target_ in np_.builds or []:
                srcname = target_.utility
                outputname = target_.utility
                nflags = np_.get_flags(tmpdir, target_)
                target_dir = os.path.join(tmpdir, outputname + '.dist')
                build_dir = os.path.join(tmpdir, outputname + '.build')
                target_dir_ = os.path.relpath(target_dir, start=self.curdir)
                src_dir = os.path.relpath(self.src_dir, start=self.curdir)
                src = os.path.join(src_dir, target_.folder,
                                   target_.utility) + '.py'
                flags_ = ''
                if 'flags' in target_:
                    flags_ = target_.flags
                lines = []
                lines.append("""
export PATH="/usr/lib64/ccache:$PATH"
    """ % vars(self))
                build_name = 'build_' + srcname
                lines.append(fR"""
{bashash_ok_folders_strings(target_dir_, ['.venv', src_dir], [],
        f"Sources for {build_name} not changed, skipping"
        )}

{self.tb_mod} bash -c 'time nice -19 pipenv run python3 -X utf8 -m nuitka --report={build_dir}/report.xml {nflags} {flags_} {src} 2>&1 > {build_name}.log'
# time nice -19 pipenv run python3 -X utf8 -m nuitka  {nflags} {flags_} {src} 2>&1 > {build_name}.log || {{ echo 'Compilation failed' ; exit 1; }}
#time nice -19 pipenv run python3 -m nuitka --recompile-c-only {nflags} {flags_} {src} 2>&1 > {build_name}.log
#time nice -19 pipenv run python3 -m nuitka --generate-c-only {nflags} {flags_} {src} 2>&1 > {build_name}.log
{self.tb_mod} python -m pipenv run python3 -m pip freeze > {target_dir_}/{build_name}-pip-freeze.txt
{self.tb_mod} python -m pipenv run python3 -m pip list > {target_dir_}/{build_name}-pip-list.txt
{save_state_hash(target_dir_)}
    """)
                self.fs.folders.append(target_dir)
                lines.append(fR"""
mv {target_dir}/{outputname}.bin {target_dir}/{outputname} || true 
""")
                if "outputname" in target_:
                    srcname = target_.outputname
                    if srcname != outputname:
                        lines.append(R"""
mv  %(target_dir_)s/%(outputname)s   %(target_dir_)s/%(srcname)s
    """ % vars())

                if "sync" in target_:
                    ts_ = target_.sync
                    for dst_ in ts_:
                        si_ = ts_[dst_]    
                        srcs = []
                        filtermod = ''
                        if isinstance(si_, str):
                            srcs.append(si_)
                        elif isinstance(si_, dict):   
                            filtermods = []
                            if "filters" in si_:
                                filtermods += [
                                    " --include='*/' "
                                ]

                                for fil_ in si_.filters:
                                    filtermods.append(f'--include="{fil_}"')

                                filtermods += [
                                    "--include='*/'",
                                    "--exclude='*'"
                                ]

                            filtermod = ' '.join(filtermods)        
                            for s_ in si_.src:
                                srcs.append(s_)    
                        for src in srcs:        
                            scmd = f'''
mkdir -p {target_dir_}/{dst_}                            
{self.tb_mod} rsync -ravm {src} {target_dir_}/{dst_} {filtermod} 
                            '''
                            lines.append(scmd)

                self.lines2sh(build_name, lines)
                bfiles.append(fname2shname(build_name))

        if 'custombuilds' in self.spec:
            cbs = self.spec.custombuilds
            for cb in cbs:
                build_name = 'build_' + cb.name
                self.lines2sh(build_name, [cb.shell.strip()], None)
                bfiles.insert(0, fname2shname(build_name))

        lines = []
        for b_ in bfiles:
            lines.append("./" + b_)

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass

    def stage_41_build_go(self):
        '''
        Build / compile Go projects to executables
        '''
        if not self.gp:
            return

        tmpdir = os.path.join(self.curdir, "tmp/ta")
        bfiles = []

        # First pass
        module2build = {}
        standalone2build = []
        referenced_modules = set()

        for td_ in self.gp.projects():
            git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)
            os.chdir(self.curdir)
            if os.path.exists(path_to_dir_):
                os.chdir(path_to_dir_)
                path_to_dir__ = os.path.relpath(
                    path_to_dir_, start=self.curdir)
                outputname = os.path.split(path_to_dir_)[-1]
                if 'name' in td_:
                    outputname = td_.name
                target_dir = os.path.join(tmpdir, outputname + '.build')
                target_dir_ = os.path.relpath(target_dir, start=path_to_dir_)
                log_dir_ = os.path.relpath(self.curdir, start=path_to_dir_)
                lines = []
                build_name = 'build_' + outputname
                lines.append(fR"""
pushd {path_to_dir__}
{self.tb_mod} bash -c 'go mod download'
{self.tb_mod} bash -c 'CGO_ENABLED=0 go build -ldflags="-linkmode=internal -r" -o {target_dir_}/{outputname} 2>&1 > {log_dir_}/{build_name}.log'
popd
    """)
                self.fs.folders.append(target_dir)
                self.lines2sh(build_name, lines, None)
                bfiles.append(fname2shname(build_name))

        lines = []
        for b_ in bfiles:
            lines.append("./" + b_)

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_52_post_pack(self):
        '''
        Post pack processing
        '''
        if not 'post_pack' in self.spec:
            return
        
        lines = []
        lines.append(fR"""
{self.spec.post_pack} 
    """)

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def clear_shell_files(self):
        os.chdir(self.curdir)
        re_ = re.compile('(\d\d-|ta-).*\.(sh|spy)')
        for sh_ in Path(self.curdir).glob('*.*'):
            if re_.match(sh_.name):
                sh_.unlink()
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
        if src.endswith('.rpmmoved'):
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
                        os.symlink(linkto, dst)
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

        if 'grafana-cli' in f:
            wtf333 = 1

        if self.br.is_needed(f):
            return True

        # if self.br.is_need_exclude(f):
        #     return False

        # Этот файл надо специально готовить, чтобы сделать перемещаемым.
        if f.startswith("/lib64/ld-linux"):
            return False

        parts = list(PurePath(f).parts)
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
            if (parts[0] not in ["lib", "lib64", "libexec"]) and (parts != ['bin', 'bash', 'sbin']):
                return False
        parts.pop(0)

        if len(parts) > 0 and (parts[0] == "locale" or parts[0] == ".build-id"):
            return False

        # что не отфильтровалось — берем.
        return True

    def rpm_update_time(self):
        import time
        for rpmdbpath in ["/usr/lib/sysimage/rpm/rpmdb.sqlite"]:
            res = "".join(self.lines_from_cmd(['date', '-r', rpmdbpath]))
            return res
        return None

    # def dependencies(self, package_list, local=True):
    #     '''
    #     Генерируем список RPM-зависимостей для заданного списка пакетов.
    #     '''

    #     pl_ = self.packages2list(package_list)
    #     package_list_md5 = hashlib.md5(
    #         (self.rpm_update_time() + '\n' + '\n'.join(pl_)).encode('utf-8')).hexdigest()
    #     cache_filename = 'tmp/cache_' + package_list_md5 + '.list'
    #     if os.path.exists(cache_filename):
    #         with open(cache_filename, 'r', encoding='utf-8') as lf:
    #             ls_ = lf.read()
    #             list_ = ls_.split(',')
    #             return list_

    #     repoch = re.compile("\d+\:")

    #     def remove_epoch(package):
    #         package_ = repoch.sub('', package)
    #         return package_

    #     options_ = [
    #         # Фильтруем пакеты по 64битной архитектуре (ну или 32битной, если будем собирать там.),
    #         # хотя сейчас почти везде хардкодинг на 64битную архитектуру.
    #         '--archlist=noarch,{machine}'.format(machine=os.uname().machine),
    #         '--resolve',
    #         '--requires',
    #         '--recursive'
    #     ]
    #     if local:
    #         options_ += [
    #             '--cacheonly',
    #             '--installed',
    #         ]

    #     if 1:
    #         res = ''
    #         for try_ in range(3):
    #             try:
    #                 res = ",".join(self.lines_from_cmd(['repoquery', '-y'] + options_ + pl_))
    #                 break
    #             except subprocess.CalledProcessError:
    #                 #  died with <Signals.SIGSEGV: 11>.
    #                 time.sleep(2)
    #         # res = subprocess.check_output(['repoquery'] + options_  + ['--output', 'dot-tree'] + package_list,  universal_newlines=True)
    #         with open(os.path.join(self.start_dir, 'deps.txt'), 'w', encoding='utf-8') as lf:
    #             lf.write('\n -'.join(pl_))
    #             lf.write('\n----------------\n')
    #             lf.write(res)

    #     output = self.lines_from_cmd(['repoquery'] + options_ + pl_)
    #     output = [remove_epoch(x) for x in output if self.ps.is_package_needed(x)]
    #     packages_ = output + pl_
    #     with open(os.path.join(self.start_dir, 'selected-packages.txt'), 'w', encoding='utf-8') as lf:
    #         lf.write('\n- '.join(packages_))

    #     packages_set_ = set()
    #     for package_ in packages_:
    #         purepackage = package_.split('.', 1)[0]
    #         if len(purepackage) < len(package_):
    #             purepackage = purepackage.rsplit('-', 1)[0]
    #         packages_set_.add(purepackage)

    #     rows_ = []
    #     for package_ in sorted(packages_set_):
    #         res_ = list(p_ for p_ in self.installed_packages if p_.name==package_)
    #         if len(res_) == 0:
    #             continue
    #         name_ = res_[0].name
    #         version_ = res_[0].version
    #         rows_.append([name_, version_])
    #         pass

    #     write_doc_table('doc-rpm-packages.htm', ['Packages', 'Version'], rows_)

    #     with open(cache_filename, 'w', encoding='utf-8') as lf:
    #         lf.write(','.join(packages_))

    #     return packages_

    # def generate_files_from_pips(self, pips):
    #     '''
    #     Для заданного списка PIP-пакетов, возвращаем список файлов в этих пакетах, которые нужны нам.
    #     '''
    #     file_list = []
    #     pips_ = [p.split('==')[0] for p in pips]
    #     import pkg_resources
    #     for dist in pkg_resources.working_set:
    #         if dist.key in pips_:
    #             if dist.has_metadata('RECORD'):
    #                 lines = dist.get_metadata_lines('RECORD')
    #                 paths = [line.split(',')[0] for line in lines]
    #                 paths = [os.path.join(dist.location, p) for p in paths]
    #                 file_list.extend(paths)

    #     pass
    #     res_ = [x for x in file_list if self.should_copy(x)]
    #     return res_
    #     pass

    def prefix_args_for_toolbox(self):
        args_ = []
        if self.toolbox_mode:
            args_ += ['toolbox', 'run', '--container', self.environ_name]
        return  args_   

    def lines_from_cmd(self, args):
        args_ = self.prefix_args_for_toolbox() + args
        lines = subprocess.check_output(args_, universal_newlines=True).splitlines()
        return lines

    def toolbox_path(self, path):
        if not self.toolbox_mode:
            return path
        
        if not path.startswith(os.path.sep):
            return path

        if path.startswith('/home'):
            return path

        if not self.container_path or not any(self.container_path.iterdir()):
            if not  self.container_info:
                inspect_file = Path(self.curdir) / 'tmp/container.json'
                # if not inspect_file.exists():
                os.system(f'podman inspect {self.environ_name} > {inspect_file}')
                assert(inspect_file.exists())    
                self.container_info = json.loads(open(inspect_file).read())
            self.container_path = Path('/tmp') / 'overlay' / self.environ_name                
            self.container_path.mkdir(exist_ok=True, parents=True)
            upper = Path(self.container_info[0]["GraphDriver"]["Data"]["UpperDir"])
            lower = Path(self.container_info[0]["GraphDriver"]["Data"]["LowerDir"])
            work  = Path(self.container_info[0]["GraphDriver"]["Data"]["WorkDir"])
            scmd = f'sudo mount -t overlay -o lowerdir={lower},upperdir={upper},workdir={work} overlay {self.container_path}'
            os.system(scmd)
            assert(any(self.container_path.iterdir()))

            assert(self.container_path.exists())


        res = str(self.container_path / path[1:])
        return res

    def generate_file_list_from_packages(self, packages):
        '''
        Для заданного списка RPM-файлов, возвращаем список файлов в этих пакетах, которые нужны нам.
        '''

        package_list_md5 = hashlib.md5(
            (self.rpm_update_time() + '\n' + '\n'.join(packages)).encode('utf-8')).hexdigest()
        cache_filename = 'tmp/cachefilelist_' + package_list_md5 + '.list'
        if os.path.exists(cache_filename):
            with open(cache_filename, 'r', encoding='utf-8') as lf:
                ls_ = lf.read()
                list_ = ls_.split('\n')
                return list_

        exclusions = []
        for package_ in packages:
            if 'grafana' in package_:
                wtf=1
            exclusions += self.lines_from_cmd(['rpm', '-qd', package_])

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

        candidates = self.lines_from_cmd(['repoquery',
                                              '-y',
                                              '--installed',
                                              '--archlist=x86_64,noarch',
                                              '--cacheonly',
                                              '--list'] + packages)

        res_ = [x for x in set(candidates) - set(exclusions)
                if self.should_copy(x)]

        with open(cache_filename, 'w', encoding='utf-8') as lf:
            lf.write('\n'.join(res_))

        return res_

    def add(self, what_, to_=None, recursive=True):
        what = self.toolbox_path(what_)
        if not os.path.exists(what):
            what = what_

        try:
            if not to_:
                to_ = what
                if to_.startswith('/'):
                    to_ = to_[1:]

            dir_, _ = os.path.split(to_)
            Path(os.path.join(self.root_dir, dir_)).mkdir(
                parents=True, exist_ok=True)
            # ar.add(f)
            if os.path.isdir(what):
                pass
            else:
                self.mycopy(what, os.path.join(self.root_dir, to_))
            return to_
        except Exception as ex_:
            print("Troubles on adding", to_, "<-", what)
            pass
            # raise ex_
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

    def fix_elf(self, path, libpath=None):
        '''
        Patch ELF file
        '''
        # key_ = hashfile(path, hexdigest=True)
        # cached_path = self.common_cache_dir / key_
        # if cached_path.exists():
        #     return cached_path.as_posix()

        # patched_elf = cached_path.as_posix()
        fd_, patched_elf = mkstemp(dir=self.patching_dir)
        shutil.copy2(path, patched_elf)
        
        orig_perm = stat.S_IMODE(os.lstat(path).st_mode)
        os.chmod(patched_elf, orig_perm | stat.S_IWUSR)         

        if libpath:
            try:
                subprocess.check_call(['patchelf',
                                    '--set-rpath',
                                    libpath,
                                    patched_elf])
            except Exception as ex_:
                print("Cannot patch ", path)
                pass

        os.close(fd_)
        os.chmod(patched_elf, orig_perm)

        self.optional_patch_binary(patched_elf)
        # shutil.move(patched_elf, cached_path)
        # self.cache.put_file(key_, open(patched_elf, 'rb'))        
        return patched_elf


    def process_binary(self, binpath):
        '''
        Фиксим бинарник.
        '''
        tb_binpath = self.toolbox_path(binpath)

        for wtf_ in ['libldap']:
            if wtf_ in binpath:
                return

        # m = magic.detect_from_filename(binpath)
        m = fucking_magic(tb_binpath)
        if m in ['inode/symlink', 'text/plain']:
            return

        if not 'ELF' in m:
            return

        pyname = os.path.basename(binpath)
        try:
            patched_binary = self.fix_elf(tb_binpath, '$ORIGIN/../lib64/')
        except Exception as ex_:
            print("Mime type ", m)
            print("Cannot fix", binpath)
            raise ex_

        if not self.interpreter:
            self.interpreter = subprocess.check_output(['patchelf', '--print-interpreter', patched_binary], universal_newlines=True).splitlines()[0]
            patched_interpreter = self.fix_elf(os.path.realpath(self.interpreter))
            tb_patched_interpreter = self.toolbox_path(patched_interpreter)
            self.add(patched_interpreter, self.out_interpreter)
            self.bin_files.add( self.out_interpreter )
        # except Exception as ex_:
        #     print('Cannot get interpreter for binary', binpath)
        #     # raise ex_
        # pass

        new_path_for_binary = os.path.join("pbin", pyname)
        self.add(patched_binary, new_path_for_binary)
        self.bin_files.add( new_path_for_binary )
        self.bin_files_sources[new_path_for_binary] = binpath
        if binpath.startswith('/'):
            dfsdsdf = 1
        os.remove(patched_binary)
        return new_path_for_binary

    def fix_sharedlib(self, binpath, targetpath):
        relpath = os.path.join(os.path.relpath("lib64", targetpath), "lib64")
        patched_binary = self.fix_elf(binpath, '$ORIGIN/' + relpath)
        self.add(patched_binary, targetpath)
        self.bin_files.add( targetpath )
        os.remove(patched_binary)
        pass

    def optional_patch_binary(self, f):
        if self.optional_bin_patcher:
            try:
                res = subprocess.check_output(
                    [self.optional_bin_patcher, f], universal_newlines=True)
            except Exception as ex_:
                print("Cannot optionally patch ", f) 
                raise Exception("Cannot patch!111")    
            wtf = 1

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

    def git_sync(self):
        '''
         Performing lazy git sync all project folders
         * get last git commit message (usially link to issue)
         * commit with same message
         * pull-merge (without rebase)
         * push to same branch
        '''
        curdir = os.getcwd()
        args = self.args
        in_src = os.path.relpath(self.src_dir, start=self.curdir)
        # for td_ in self.projects() + self.spec.templates_dirs:
        #     git_url, git_branch, path_to_dir_, _ = self.explode_pp_node(td_)

        for git_url, git_branch, path_to_dir_ in self.get_all_sources():
            os.chdir(curdir)
            if os.path.exists(path_to_dir_):
                os.chdir(path_to_dir_)
                print(f'''\nSyncing project "{path_to_dir_}"''')
                last_commit_message = subprocess.check_output(
                    "git log -1 --pretty=%B", shell=True).decode("utf-8")
                last_commit_message = last_commit_message.strip('"')
                last_commit_message = last_commit_message.strip("'")
                if not last_commit_message.startswith("Merge branch"):
                    os.system(f'''git commit -am "{last_commit_message}" ''')
                os.system(f'''git pull --rebase=false ''')
                if 'out' in self.args.git_sync:
                    os.system(f'''git push origin ''')
                os.chdir(self.curdir)
        pass

    def stage_23_checkout_sources(self):
        '''
            Checking out sources. We should have authorization to check them out.
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
                scmd = f'''
git --git-dir=/dev/null clone  {git_url} {newpath}
pushd {newpath}
git checkout {git_branch}
git config core.fileMode false
git config core.autocrlf input
git lfs install
git lfs pull
popd
''' 
                lines.append(scmd)

                lines2.append(f'''
pushd "{path_to_dir}"
git config core.fileMode false
git config core.autocrlf input
git pull
python -m pipenv run python -m pip uninstall  {probably_package_name} -y
python -m pipenv run python setup.py develop
popd
''' )

                # Fucking https://www.virtualbox.org/ticket/19086 + https://www.virtualbox.org/ticket/8761
                lines.append(f"""
if [ -d "{newpath}" ]; then
  echo 2 > /proc/sys/vm/drop_caches || true
  find  "{path_to_dir}" -type f -delete || true;
  find  "{path_to_dir}" -type f -exec rm -rf {{}} \ || true;
  rm -rf "{path_to_dir}" || true
  mv "{newpath}" "{path_to_dir}" || true
  rm -rf "{newpath}"
fi
""")

        lines.append(f"""
# We need to update all shell files after checkout.
#terrarium_assembler "{self.args.specfile}"
""")

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
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
        ext_compiled_tar_path = os.path.relpath(self.ext_compiled_tar_path, self.curdir)
        
        scmd = f' -m pip install {target} --no-index --no-cache-dir --use-deprecated=legacy-resolver --find-links="{ext_whl_path}" --find-links"{ext_compiled_tar_path}" --find-links="{our_whl_path}"  --force-reinstall --no-deps --ignore-installed '
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
        if not self.pp.terra.pip and not self.pp.terra.projects:
            return

        # Пока хардкодим вставку нашего питон-пипа. потом конечно надо бы избавится.
        root_dir = self.root_dir
        os.chdir(self.curdir)

        pipdir = ''
        for pdir in ('github-belonesox-pip', 'pip'):
            pipdir = os.path.join('in', 'src', pdir)
            if os.path.exists(pipdir):
                break

        if not pipdir:
            return        
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
        ext_compiled_tar_path = os.path.relpath(self.ext_compiled_tar_path, self.curdir)

        findlinks_mod = f''' --find-links="{our_whl_path}" --find-links="{ext_whl_path}" --find-links="{ext_compiled_tar_path}"  ''' 


        # os.system(f'''{self.root_dir}/ebin/python3 -m pip install {pip_args_} --find-links="{our_whl_path}" --find-links="{ext_whl_path}"''')

        scmd = f'''
{self.root_dir}/ebin/python3 -m pip install setuptools {findlinks_mod} --force-reinstall --ignore-installed --no-warn-script-location
        '''
        self.cmd(scmd)

        if self.args.debug:
            scmd = f'''
{self.root_dir}/ebin/python3 -m pip install pip {findlinks_mod} --force-reinstall --ignore-installed --no-warn-script-location
            '''
        else:
            scmd = f'''
{self.root_dir}/ebin/python3 -m pip install {pip_args_} {findlinks_mod} --force-reinstall --ignore-installed --no-warn-script-location
            '''

        os.chdir(self.curdir)
        self.cmd(scmd)
        # не спрашивайте. Теоретически, должно ставится за прошлый раз, но иногда нет.
        self.cmd(scmd)

        # if self.tvars.fc_version == '32':
        #     os.system(
        #         f"rm -f {root_dir}/local/lib/python3.8/site-packages/typing.*")

        if self.pp.terra.projects:
            nodes_ = self.pp.terra.projects
            if self.args.debug:
                nodes_ += (self.pp.build.projects or [])
            for td_ in nodes_:
                git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(
                    td_)

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
                # --no-deps
                self.cmd(
                    f"{root_dir}/ebin/python3 setup.py install --single-version-externally-managed  {release_mod} --root / --force  ")

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

    def stage_00_init_box_and_repos(self):
        '''
        Create building container/box and install RPM repositories
        '''
        root_dir = self.root_dir
        args = self.args
        packages = []
        lines = [self.toolbox_create_line()]

#         lines.append([f'''
# x="$(readlink -f "$0")"
# d="$(dirname "$x")"

# {self.tb_mod} echo "[ta]
# name=TA
# baseurl=file:///$d/{self.rpmrepo_path}/
# enabled=0
# gpgcheck=0
# repo_gpgcheck=0 " > /etc/yum.repos.d/ta.repo
# '''])

        for rp_ in self.ps.repos or []:
            if rp_.lower().endswith('.gpg'):
                lines.append(f'{self.tb_mod} sudo rpm --import {rp_} ')
            # elif rp_.endswith('.rpm'):
            #     lines.append(f'{self.tb_mod} sudo dnf install --nogpgcheck {rp_} -y ')
            else:
                lines.append(f'{self.tb_mod} sudo dnf config-manager --add-repo {rp_} -y ')
                prp_ = rp_
                if '://' in prp_:
                    prp_ = prp_.split('://')[1]
                prp_ = prp_.replace('/', '_')
                lines.append(
                    f'{self.tb_mod} sudo dnf config-manager --save --setopt={prp_}.gpgcheck=0 -y')
            pass

        lines.append('')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass

    def stage_02_download_base_packages(self):
        '''
        Download base RPM packages.
        '''
        root_dir = self.root_dir
        args = self.args
        packages = []
        lines = []

        lines_src = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)

        pls_ = [p for p in self.minimal_packages]
        for rp_ in self.ps.repos or []:
            if rp_.endswith('.rpm'):
                pls_.append(rp_)

        # packages = " ".join(self.dependencies(pls_, local=False) + purls_)
        packages = " ".join(pls_)
        scmd = f'''dnf download --skip-broken --downloaddir {self.base_rpms_path} --arch=x86_64  --arch=x86_64 --arch=noarch --alldeps --resolve  {packages} -y '''
        lines.append(f'''
{bashash_ok_folders_strings(self.base_rpms_path, [], [scmd],
        f"Looks required base RPMs already downloaded"
        )}
rm -rf '{self.base_rpms_path}'
{self.tb_mod} {scmd}
{save_state_hash(self.base_rpms_path)}
''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


    def stage_06_download_rpm_packages(self):
        '''
        Download RPM packages.
        '''
        root_dir = self.root_dir
        args = self.args
        packages = []
        lines = []

        in_bin = os.path.relpath(self.in_bin, start=self.curdir)

        # scmd = f"rm -rf '{in_bin}/rpms'"
        # lines.append(scmd)
        # scmd = "sudo yum-config-manager --enable remi"
        # lines.append(scmd)
        pls_ = [p for p in self.need_packages +
                self.ps.build + self.ps.terra if isinstance(p, str)]
        purls_ = [p.url for p in self.need_packages +
                  self.ps.build + self.ps.terra if not isinstance(p, str)]

        # packages = " ".join(self.dependencies(pls_, local=False) + purls_)
        packages = " ".join(pls_ + purls_)
        scmd = f'''dnf download --skip-broken --downloaddir {self.rpms_path} --arch=x86_64  --arch=x86_64 --arch=noarch --alldeps --resolve  {packages} -y '''
        #str(self.ps.remove_from_download)
        lines.append(f'''
{bashash_ok_folders_strings(self.rpms_path, [], [scmd],
        f"Looks required RPMs already downloaded"
        )}
# rm -rf '{self.rpms_path}'
{self.tb_mod} {scmd}
{self.create_repo_cmd}
{save_state_hash(self.rpms_path)}
''')
        # for pack_ in self.ps.remove_from_download or []:
        #     scmd = f'rm -f {self.rpms_path}/{pack_}* '
        #     lines.append(scmd)

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


    def stage_08_audit_download_srpms(self):
        '''
        Download Source RPM packages.
        '''
        lines = []
        packages = " ".join(self.packages_to_rebuild)
        scmd = f'''dnf download --skip-broken --downloaddir {self.srpms_path} --arch=x86_64  --arch=noarch --source  {packages} -y '''
        lines.append(f'''
{bashash_ok_folders_strings(self.srpms_path, [], [scmd],
        f"Looks required SRPMs already downloaded"
        )}
rm -rf '{self.srpms_path}'
{self.tb_mod} {scmd}
{self.create_repo_cmd}
{save_state_hash(self.srpms_path)}
''')

        # for pack_ in self.ps.exclude_prefix or []:
        #     scmd = f'rm -f {self.srpms_path}/{pack_}* '
        #     lines.append(scmd)


        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)

    def stage_09_audit_download_build_deps_rpms1(self):
        '''
        Download Build Deps for SRPM packages.
        '''
        lines = []
        lines_src = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)
        pls_ = [p for p in self.ps.terra if isinstance(p, str)]

        remove_unwanted = []
        # for pack_ in self.ps.remove_from_download or []:
        #     scmd = f'rm -f {self.build_deps_rpms}/{pack_}* '
        #     remove_unwanted.append(scmd)
        #remove_unwanted_mod = '\n'.join(remove_unwanted)

        # conflicting_686_packages = 'bash gobject-introspection-devel mpdecimal-devel uid_wrapper pkgconf-pkg-config libdb-devel-static pybind11-devel flexiblas-devel unixODBC-devel'.split()
        # conflicting_packages = 'bash gobject-introspection-devel mpdecimal-devel uid_wrapper pkgconf-pkg-config libdb-devel-static pybind11-devel flexiblas-devel unixODBC-devel'.split()
        # filter_egrep_686 = ' '.join([f''' | egrep -v "{p}.*.i686" ''' for p in conflicting_686_packages])
# SRPMS=`find . -wholename "./{self.rpmbuild_path}/*/SRPMS/*.{self.disttag}.src.rpm"`        
# {filter_egrep_686}
        state_dir = self.rpmbuild_path + '/build_deps1'
        lines.append(f'''
{bashash_ok_folders_strings(state_dir, [self.srpms_path], [str(self.ps.remove_from_download)],
        f"Looks required RPMs for building SRPMs already downloaded"
        )}
x="$(readlink -f "$0")"
d="$(dirname "$x")"
# SRPMS=`find . -wholename "./{self.rpmbuild_path}/*/SRPMS/*.{self.disttag}.src.rpm"`        
SRPMS=`find . -wholename "./{self.srpms_path}/*.src.rpm"`        
#{self.tb_mod} dnf download --exclude 'fedora-release-*' --skip-broken --downloaddir {self.rpms_path} --arch=x86_64  --arch=x86_64 --arch=noarch --alldeps --resolve  $SRPMS -y 
{self.tb_mod} sudo dnf builddep --exclude 'fedora-release-*' --skip-broken --downloadonly --downloaddir {self.rpms_path} $SRPMS -y 
# SRC_DEPS_PACKAGES=`{self.tb_mod} sudo dnf repoquery -y --resolve --recursive --requires $SRPMS | grep -v "fedora-release" `
# SRC_DEPS_PACKAGES_MAIN=`echo $SRC_DEPS_PACKAGES | tr ' ' '\\n' | grep -v i686 | tr '\\n' ' '`
# SRC_DEPS_PACKAGES_ADD=`echo $SRC_DEPS_PACKAGES | tr ' ' '\\n' | grep i686 | tr '\\n' ' '`
# echo $SRC_DEPS_PACKAGES_MAIN > tmp/src_deps_packages_main.txt
# echo $SRC_DEPS_PACKAGES_ADD > tmp/src_deps_packages_add.txt
# echo $SRC_DEPS_PACKAGES > {self.src_deps_packages}
# echo $SRC_DEPS_PACKAGES_ADD > {self.src_deps_packages_add}
# echo $SRC_DEPS_PACKAGES_MAIN > {self.src_deps_packages_main}
# {self.tb_mod} dnf download --exclude 'fedora-release-*' --downloaddir {self.rpms_path} --arch=x86_64 --arch=i686 --arch=noarch  -y  $SRC_DEPS_PACKAGES 
{self.create_repo_cmd}
{save_state_hash(state_dir)}
''')
# rm -rf '{self.build_deps_rpms}'
# egrep "noarch|x86_64"
# {self.tb_mod} dnf download --exclude 'fedora-release-*' --downloaddir {self.build_deps_rpms} --arch=x86_64  --arch=noarch  -y  $SRC_DEPS_PACKAGES

# SRC_DEPS_PACKAGES2=`{self.tb_mod} sudo dnf repoquery -y --resolve --recursive --requires {self.srpms_path}/*.src.rpm | egrep "noarch|x86_64" | grep -v "fedora-release" `
# echo $SRC_DEPS_PACKAGES2 > SRC_DEPS_PACKAGES2


# SRC_PACKAGES=`{self.tb_mod} sudo dnf repoquery -y --resolve --requires --exactdeps --whatrequires {self.srpms_path}/*.src.rpm | egrep "noarch|x86_64" | grep -v "fedora-release" `
# {self.tb_mod} dnf download --exclude 'fedora-release-*' --downloaddir {self.build_deps_rpms} --arch=x86_64  --arch=noarch  -y  $SRC_PACKAGES

# {self.tb_mod} dnf download --exclude 'fedora-release-*' --downloaddir {self.build_deps_rpms} --skip-broken --arch=x86_64  --arch=noarch  -y  $FILTERED_BUILD_REQUIRES
# SPECS=`find {self.rpmbuild_path} -wholename "*SPECS/*.spec"`        
# ALL_BUILD_REQUIRES=""
# for SPEC in `echo $SPECS`
# do
#     echo $SPEC
#     REQUIRES=`{self.tb_mod} rpmspec -q --buildrequires $SPEC | tr '\\n' ' ' `
#     ALL_BUILD_REQUIRES="$ALL_BUILD_REQUIRES $REQUIRES"
# done        
# echo $ALL_BUILD_REQUIRES > ALL_BUILD_REQUIRES
# RESOLVED_REQUIRES=`{self.tb_mod} dnf repoquery -y --archlist=x86_64,noarch $ALL_BUILD_REQUIRES`
# echo $RESOLVED_REQUIRES > RESOLVED_REQUIRES
# FILTERED_BUILD_REQUIRES=`echo $RESOLVED_REQUIRES | tr ' ' '\\n' | grep -v 'fedora-release' | tr '\\n' ' ' `
# echo $FILTERED_BUILD_REQUIRES > FILTERED_BUILD_REQUIRES


        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)

    def stage_13_audit_download_build_deps_rpms2(self):
        '''
        Download Build Deps for SRPM packages.
        '''
        lines = []
        lines_src = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)
        pls_ = [p for p in self.ps.terra if isinstance(p, str)]

        state_dir = self.rpmbuild_path + '/build_deps2'

        lines.append(f'''
{bashash_ok_folders_strings(state_dir, [self.srpms_path], [str(self.ps.remove_from_download)],
        f"Looks required RPMs for building SRPMs already downloaded"
        )}
x="$(readlink -f "$0")"
d="$(dirname "$x")"
SPECS=`find in/bin/rpmbuild -wholename "*SPECS/*.spec"`        
for SPEC in `echo $SPECS`
do
    BASEDIR=`dirname $SPEC`/..
    echo $BASEDIR
    {self.tb_mod} sudo dnf builddep --exclude 'fedora-release-*' --define "_topdir $d/$BASEDIR" --skip-broken --downloadonly --downloaddir {self.rpms_path} $SPEC -y 
done        
{self.create_repo_cmd}
{save_state_hash(state_dir)}
''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)

    def stage_12_audit_unpack_srpms(self):
        '''
        Unpack SRPM packages.
        '''
        lines = []
        # scmd = f'''find {self.srpms_path} -name "*.src.rpm" | xargs -t sh -c 'rpmbuild --rebuild --nocheck --nodeps --nodebuginfo --define "_topdir $d/${{1##*/}}-rpmbuild" $1' '''
# import os
# curdir = os.path.abspath(os.getcwd())        
# src_rpms = `find {self.srpms_path} -name "*.src.rpm"
# for src_rpm_path in src_rpms:
#     src_rpm = os.path.basename(src_rpm_path)
#     scmd = f"""rpmbuild --rebuild --nocheck --nodeps --nodebuginfo --define "_topdir {{curdir}}/{{src_rpm}}-rpmbuild" {{src_rpm_path}}"""
#     print(scmd)
#     res = `{{scmd}}`
#     print(res)

        lines.append(f'''
{bashash_ok_folders_strings(self.rpmbuild_path, [self.srpms_path], [],
         f"Looks all SRPMs already prepared for build"
        )}
x="$(readlink -f "$0")"
d="$(dirname "$x")"
#rm -rf {self.rpmbuild_path}/*
SRPMS=`find {self.srpms_path} -name "*.src.rpm"`        
for SRPM in `echo $SRPMS`
do
    echo $SRPM
    BASEDIR=`basename $SRPM`-rpmbuild
    {self.tb_mod} rpmbuild -rp --rebuild  --define "_topdir $d/{self.rpmbuild_path}/$BASEDIR" $SRPM
done        
{save_state_hash(self.rpmbuild_path)}
''')

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


    def stage_16_audit_build_specs_to_rpms(self):
        '''
        Rebuild SRPM packages.
        '''
        lines = []
# HOME=$d/tmp
        rebuild_mod = ' --without '.join([''] + self.ps.rebuild_disable_features)

        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"
chmod u+w {self.rpmbuild_path} -R
SPECS=`find {self.rpmbuild_path} -wholename "*SPECS/*.spec"`        
for SPEC in `echo $SPECS`
do
    echo $SPEC
    BASEDIR=`dirname $SPEC`/..
    {bashash_ok_folders_strings("$d/$BASEDIR/BUILD", ["$d/$BASEDIR/SPECS", "$d/$BASEDIR/SOURCES"], [self.disttag, rebuild_mod], f"Looks all here already build RPMs from $BASEDIR", cont=True)}
    echo -e "\\n\\n\\n ****** Build $SPEC ****** \\n\\n"
    {self.tb_mod} rpmbuild -bb --noclean --nocheck --nodeps  {rebuild_mod} --define "_topdir $d/$BASEDIR" --define 'dist %{{!?distprefix0:%{{?distprefix}}}}%{{expand:%{{lua:for i=0,9999 do print("%{{?distprefix" .. i .."}}") end}}}}.{self.disttag}'  $SPEC
    {self.tb_mod} find $d/$BASEDIR -wholename "$d/$BASEDIR*/RPMS/*/*.rpm" | xargs -i{{}} cp {{}} {self.rebuilded_rpms_path}/ 
    {save_state_hash("$d/$BASEDIR/BUILD")}
done        
{self.create_rebuilded_repo_cmd}
''')

    # {self.tb_mod} find $d/$BASEDIR -wholename "$d/$BASEDIR*/RPMS/*/*.rpm" -delete
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)

# --nodebuginfo

    def stage_17_audit_install_rebuilded_srpms(self):
        '''
        Install rebuild SRPM packages.
        '''
        lines = []
        packages = " ".join(self.packages_to_rebuild)
# {self.tb_mod} sudo dnf remove -y --skip-broken {packages}
#{self.rebuilded_rpms_path}/
        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"
#RPMS=`find . -wholename "./{self.rpmbuild_path}/*/RPMS/*.{self.disttag}.*.rpm"`        
{self.tb_mod} sudo rm /etc/dnf/protected.d/systemd.conf
{self.tb_mod} sudo dnf remove -y "*.i686"
RPMS=`ls {self.rebuilded_rpms_path}/*.rpm`        
#for RPM in `echo $RPMS`
#do
#{self.tb_mod} sudo rpm -ivh --force --nodeps $RPMS
{self.tb_mod} sudo dnf install --refresh --allowerasing --skip-broken --disablerepo="*" --enablerepo="tar" -y $RPMS
#done
#{self.tb_mod} sudo dnf install --refresh --disablerepo="*" --enablerepo="tar" -y {packages}
''')
# {self.tb_mod} sudo rpm install -ivh --excludedocs $RPMS  
#--disablerepo="*"
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)

    def stage_19_save_file_rpmpackage_info(self):
        '''
        Install rebuild SRPM packages.
        '''
        lines = []
        lines.append(f'''
{self.tb_mod} rpm -qa --queryformat "[%{{=NAME}}{ROW_SPLIT}%{{=VERSION}}{ROW_SPLIT}%{{=RELEASE}}{ROW_SPLIT}%{{=BUILDTIME}}{ROW_SPLIT}%{{=BUILDHOST}}{ROW_SPLIT}%{{FILENAMES}}\\n]"  > {self.file_package_list_from_rpms}
{self.tb_mod} sudo repoquery -y --installed --archlist=x86_64,noarch --queryformat "%{{name}}" --resolve --recursive --cacheonly --requires {self.terra_package_names} > {self.terra_rpms_closure}
''')
# {self.tb_mod} sudo repoquery -y --installed --archlist=x86_64,noarch --cacheonly --list {self.terra_package_names} > {self.file_list_from_terra_rpms}
# {self.tb_mod} sudo repoquery -y --installed --archlist=x86_64,noarch --resolve --recursive --cacheonly --requires --list {self.terra_package_names} > {self.file_list_from_deps_rpms}
# {self.tb_mod} cat {self.file_list_from_terra_rpms} {self.file_list_from_deps_rpms} > {self.file_list_from_rpms}


# {self.tb_mod} sudo rpm install -ivh --excludedocs $RPMS
# toolbox run -c linux_distro-deploy-for-audit sudo repoquery -y --installed --archlist=x86_64,noarch --resolve --recursive --cacheonly --requires --list onnxruntime python3-gobject-base python3-shapely python3-cups python3-pytest libX11-devel libXrandr-devel cups-filters nss nss-util poppler-utils tesseract tesseract-langpack-rus tesseract-script-cyrillic libwnck3 bash clickhouse-client zbar-devel gtk2-devel > tmp/file-list-from-deps-rpms.txt


        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


    def stage_49_save_sofiles(self):
        '''
        Save information about all SO-files in .venv
        '''
        lines = []
        lines.append(f'''
find .venv -name "*.so*"  > {self.so_files_from_venv}
find {self.pip_source_dir} -name "*.so*"  > {self.so_files_from_rebuilded_pips}
find {self.src_dir} -name "*.so*"  > {self.so_files_from_our_packages}
''')

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


#     def stage_12_audit_build_srpms(self):
#         '''
#         Rebuild SRPM packages to SRPM
#         '''
#         lines = []
# # HOME=$d/tmp
#         lines.append(f'''
# x="$(readlink -f "$0")"
# d="$(dirname "$x")"
# SPECS=`find {self.rpmbuild_path} -wholename "*SPECS/*.spec"`        
# for SPEC in `echo $SPECS`
# do
#     echo $SPEC
#     BASEDIR=`dirname $SPEC`/..
#     {bashash_ok_folders_strings("$d/$BASEDIR/SRPMS", ["$d/$BASEDIR/SPECS", "$d/$BASEDIR/SOURCES"], [], f"Looks all here already build SRPMS from $BASEDIR", cont=True)}
#     {self.tb_mod} rpmbuild -bs --nocheck --nodeps --nodebuginfo --without docs --without doc_pdf --without doc --without tests --define "_topdir $d/$BASEDIR" --define 'dist %{{!?distprefix0:%{{?distprefix}}}}%{{expand:%{{lua:for i=0,9999 do print("%{{?distprefix" .. i .."}}") end}}}}.{self.disttag}'  $SPEC
#     {save_state_hash("$d/$BASEDIR/SRPMS")}
# done        
# ''')

#         mn_ = get_method_name()
#         self.lines2sh(mn_, lines, mn_)



    def stage_03_install_base_rpms(self):
        '''
        Install downloaded base RPM packages
        '''
        # --disablerepo="*" ???? WTF!!!!
        lines = [
            f"""
{self.tb_mod} sudo dnf install  --nodocs --nogpgcheck --skip-broken {self.base_rpms_path}/*.rpm -y --allowerasing
{self.create_repo_cmd}
{self.tb_mod} createrepo {self.tarrepo_path}
{self.tb_mod} sudo bash -c 'x="$(readlink -f "$0")"; d="$(dirname "$x")"; echo -e "[ta]\\nname=TA\\nbaseurl=file:///$d/{self.rpmrepo_path}/\\nenabled=0\\ngpgcheck=0\\nrepo_gpgcheck=0\\n" > /etc/yum.repos.d/ta.repo'
{self.tb_mod} sudo bash -c 'x="$(readlink -f "$0")"; d="$(dirname "$x")"; echo -e "[tar]\\nname=TAR\\nbaseurl=file:///$d/{self.tarrepo_path}/\\nenabled=0\\ngpgcheck=0\\nrepo_gpgcheck=0\\n" > /etc/yum.repos.d/tar.repo'
""" 
        ]
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass
# {self.tb_mod} sudo dnf config-manager --add-repo file://$d/{self.rpmrepo_path}/ -y 


    def stage_07_install_rpms(self):
        '''
        Install downloaded RPM packages
        '''
        packages = " ".join(self.ps.build + self.need_packages + self.ps.terra)
        lines = [
            f"""
{self.tb_mod} sudo dnf install --refresh --nodocs --nogpgcheck --disablerepo="*" --enablerepo="ta" --skip-broken -y --allowerasing {packages}
{self.tb_mod} sudo dnf repoquery -y --installed --archlist=x86_64,noarch --cacheonly --list {self.terra_package_names} > {self.file_list_from_terra_rpms}
{self.tb_mod} sudo dnf repoquery -y --installed --archlist=x86_64,noarch --resolve --recursive --cacheonly --requires --list {self.terra_package_names} > {self.file_list_from_deps_rpms}
{self.tb_mod} cat {self.file_list_from_terra_rpms} {self.file_list_from_deps_rpms} > {self.file_list_from_rpms}
""" 
#{self.tb_mod} sudo repoquery -y --installed --archlist=x86_64,noarch --docfiles --resolve --recursive --cacheonly --requires --list {terra_package_names} > {self.doc_list_from_deps_rpms}
#{self.tb_mod} sudo repoquery -y --installed --archlist=x86_64,noarch --docfiles --cacheonly --list {terra_package_names} > {self.doc_list_from_terra_rpms}
#{self.tb_mod} cat {self.doc_list_from_terra_rpms} {self.doc_list_from_deps_rpms} > {self.doc_list_from_rpms}
# self.file_list_from_rpms}
        ]
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_14_audit_install_build_deps_rpms2(self):
        '''
        Install downloaded RPM packages for building SRPMS
        '''
        lines = [
            f"""
x="$(readlink -f "$0")"
d="$(dirname "$x")"
{self.tb_mod} sudo dnf --refresh --disablerepo="*" --enablerepo="ta" update -y 
SPECS=`find in/bin/rpmbuild -wholename "*SPECS/*.spec"`        
for SPEC in `echo $SPECS`
do
    BASEDIR=`dirname $SPEC`/..
    echo $BASEDIR
    {self.tb_mod} sudo dnf builddep --exclude 'fedora-release-*' --define "_topdir $d/$BASEDIR" --skip-broken $SPEC -y 
done        
#{self.tb_mod} sudo dnf builddep --nodocs --refresh --disablerepo="*" --enablerepo="ta" --nogpgcheck -y --allowerasing $SPECS
""" 
        ]
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass

    def stage_10_audit_install_build_deps_rpms1(self):
        '''
        Install downloaded RPM packages for building SRPMS
        '''


        lines = [
            f"""

SRPMS=`find . -wholename "./{self.srpms_path}/*.src.rpm"`        
{self.tb_mod} sudo dnf builddep --nodocs --refresh --disablerepo="*" --enablerepo="ta" --nogpgcheck -y --allowerasing $SRPMS
""" 
        ]
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_24_build_wheels(self):
        '''
            Compile wheels for our python sources
        '''
        os.chdir(self.curdir)
        bindir_ = os.path.abspath(self.in_bin)
        lines = []
        in_bin = os.path.relpath(self.in_bin, start=self.curdir)
        wheelpath = os.path.join(self.in_bin, "ourwheel")
        relwheelpath = os.path.relpath(wheelpath, start=self.curdir)
        lines.append(fR'''
{bashash_ok_folders_strings(self.our_whl_path, [self.src_dir], [],
f"Looks like sources not changed, not need to rebuild WHLs for our sources"
)}
rm -f {self.our_whl_path}/*
''')
        for td_ in self.pp.projects():
            # , local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in (self.pp.terra if self.pp.terra else [])]:
            git_url, git_branch, path_to_dir_, setup_path = self.explode_pp_node(
                td_)
            path_to_dir = os.path.relpath(path_to_dir_, start=self.curdir)
            relwheelpath = os.path.relpath(wheelpath, start=path_to_dir_)
            # scmd = "pushd %s" % (path_to_dir)
            # lines.append(scmd)
            scmd = f"""
{self.tb_mod} python -m pipenv run sh -c "pushd {path_to_dir};python3 setup.py clean --all;python3 setup.py bdist_wheel -d {relwheelpath};popd"
"""
            lines.append(scmd)
            pass

        lines.append(save_state_hash(self.our_whl_path))
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)


    def stage_22_init_python_env(self):
        '''
        Create build environment with some bootstrapping
        '''
        os.chdir(self.curdir)

        lines = []
        scmd = f'''
{self.tb_mod} python -m pipenv --rm || true
{self.tb_mod} rm -f Pipfile*
{self.tb_mod} python -m pipenv install --python {self.tvars.python_version_1}.{self.tvars.python_version_2}
{self.tb_mod} python -m pipenv run python -m pip install {self.base_whl_path}/*.whl --force-reinstall --ignore-installed  --no-cache-dir --no-index
'''
#{self.tb_mod} touch Pipfile

        lines.append(scmd)
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass

    def stage_27_install_wheels(self):
        '''
        Install our and external Python wheels
        '''
        os.chdir(self.curdir)
        lines = []

        our_whl_path = os.path.relpath(self.our_whl_path, self.curdir)
        ext_whl_path = os.path.relpath(self.ext_whl_path, self.curdir)
        ext_compiled_tar_path = os.path.relpath(self.ext_compiled_tar_path, self.curdir)

        scmd = f'''
{bashash_ok_folders_strings('.venv', [self.our_whl_path, self.ext_whl_path, ext_compiled_tar_path, self.base_whl_path], [],
        f"Looks like dont need to update .venv"
        )}

{self.tb_mod} pipenv --rm || true
{self.tb_mod} pipenv install --python {self.tvars.python_version_1}.{self.tvars.python_version_2}
{self.tb_mod} pipenv run python -m pip install `ls ./{our_whl_path}/*.whl` `ls ./{ext_whl_path}/*.whl` `ls ./{ext_compiled_tar_path}/*.whl` --find-links="{our_whl_path}" --find-links="{ext_compiled_tar_path}" --find-links="{ext_whl_path}"  --force-reinstall --ignore-installed  --no-cache-dir --no-index
{self.tb_mod} pipenv run python -m pip list > {self.pip_list}
{self.tb_mod} pipenv run python -m pip list --format json > {self.pip_list_json}
{self.tb_mod} pipenv run pip-audit -o tmp/pip-audit-report.md -f markdown || true
{self.tb_mod} pipenv run pipdeptree --graph-output dot > {self.pipdeptree_graph_dot}
{self.tb_mod} pandoc -w mediawiki tmp/pip-audit-report.md -o tmp/pip-audit-report.wiki
{self.tb_mod} bash -c "(echo '<graph>'; cat {self.pipdeptree_graph_dot}; echo '</graph>') > {self.pipdeptree_graph_mw}"

{save_state_hash('.venv')}
'''

        lines.append(scmd)   # --no-cache-dir

        for scmd_ in self.pp.shell_commands or []:
            lines.append(scmd_)

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_30_install_rebuilded_whls(self):
        '''
        Install our and external Python wheels
        '''
        os.chdir(self.curdir)
        lines = []

        # pps = " ".join([''] + [p.replace('-','_') for p in self.pp.rebuild])
        pps = self.python_rebuild_profiles.get_list_of_pip_packages()
        if not pps.strip():
            return 

        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"
PIP_SOURCE_DIR={self.pip_source_dir}
mkdir -p $PIP_SOURCE_DIR

{self.tb_mod} pipenv run python -m pip install --force-reinstall `ls {self.rebuilded_whl_path}/*.whl`  --find-links="{self.our_whl_path}" --find-links="{self.ext_compiled_tar_path}" --find-links="{self.ext_whl_path}"  --force-reinstall --ignore-installed  --no-cache-dir --no-index

for PP in {pps}
do
    echo $PP

    {self.tb_mod} rm -rf .venv/lib64/python3.10/site-packages/$PP.libs
    {self.tb_mod} ln -s /usr/lib64 .venv/lib64/python3.10/site-packages/$PP.libs
done        
''')

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
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
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(
                td_)
            if not os.path.exists(setup_path):
                continue
            os.chdir(setup_path)

            is_python_package = False
            for file_ in ['setup.py', 'pyproject.toml']:
                if os.path.exists(file_):
                    is_python_package = True
                    break

            if is_python_package:
                pip_targets.append(os.path.relpath(
                    setup_path, start=self.curdir))

            reqs_path = 'requirements.txt'
            for reqs_ in glob.glob(f'**/{reqs_path}', recursive=True):
                if 'tests/' in reqs_:
                    continue
                if 'django-q' in setup_path:
                    rewqwerew = 1
                with open(reqs_, 'r', encoding='utf-8') as lf:
                    reqs__ = lf.read()
                    if not '--hash=sha256' in reqs__:
                        # very strange requirements.txt, we cannot download it.
                        pip_reqs.append(os.path.join(os.path.relpath(
                            setup_path, start=self.curdir), reqs_))
                    else:
                        print(f'«--hash=sha256» in {setup_path}/{reqs_}')

            pass

        return pip_targets, pip_reqs

    def pip_args_from_sources(self, terra=False, ignore_fixed=False):
        pip_targets, pip_reqs = self.get_pip_targets_and_reqs_from_sources(
            terra=terra)
        # pip_targets += self.pp.pip()

        pip_targets_ = " ".join([r for r in pip_targets if not ignore_fixed or '==' not in r])
        pip_reqs_ = " ".join([f" -r {r} " for r in pip_reqs])
        return f" {pip_reqs_} {pip_targets_} "


    def base_wheels_string(self):
        pip_targets, _ = self.get_pip_targets_and_reqs_from_sources(terra=False)
        pip_targets_ = " ".join([r for r in pip_targets if '==' in r])
        return pip_targets_

    def stage_21_download_base_wheels(self):
        '''
        Consistent downloading only python packages with fixed versions.
        They should be downloaded before building our packages and creating pipenv environment.
        '''
        os.chdir(self.curdir)
        os.chdir(self.out_dir)

        root_dir = self.root_dir
        args = self.args

        bin_dir = os.path.relpath(self.in_bin, start=self.curdir)

        lines = []
# x="$(readlink -f "$0")"
# d="$(dirname "$x")"

        bws =  self.base_wheels_string()

        # pipenv environment does not exists we using regular python to download base packages.
        scmd = f"python -m pip download  {bws} --dest {self.base_whl_path} "
        lines.append(f'''
{bashash_ok_folders_strings(self.base_whl_path, [], [bws],
        f"Looks required base wheels already downloaded"
        )}
rm -f {self.base_whl_path}/*
{self.tb_mod} {scmd}
{save_state_hash(self.base_whl_path)}
''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass

    def stage_28_audit_download_pip_sources(self):
        '''
        Download PIP sources.
        '''
        os.chdir(self.curdir)

        lines = []
        # if not self.ps.rebuild:
        #     return 
        # pps = " ".join([''] + self.pp.rebuild)
        pps = self.python_rebuild_profiles.get_list_of_pip_packages()
        if not pps.strip():
            return 


        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"
PIP_SOURCE_DIR={self.pip_source_dir}
mkdir -p $PIP_SOURCE_DIR
for PP in {pps}
do
    echo $PP
    VERSION=`cat tmp/pip-list.json | jq -j "map(select(.name==\\"$PP\\")) | .[0].version"`
    FILENAME=$PP-$VERSION
    if [ ! -d "$PIP_SOURCE_DIR/$FILENAME" ]; then
        echo **$FILENAME--
        URL=`curl -s https://pypi.org/pypi/$PP/json | jq -r '.releases[][] ' | jq "select((.filename|test(\\"$FILENAME\\")) and (.packagetype==\\"sdist\\") and (.python_version==\\"source\\"))" | jq -j '.url'`
        echo $URL
        wget --secure-protocol=TLSv1_2 -c -P $PIP_SOURCE_DIR/ $URL
        if [ -f "$PIP_SOURCE_DIR/$FILENAME.tar.gz" ]; then
            tar xf $PIP_SOURCE_DIR/$FILENAME.tar.gz -C $PIP_SOURCE_DIR
        fi
        if [ -f "$PIP_SOURCE_DIR/$FILENAME.zip" ]; then
            unzip -d $PIP_SOURCE_DIR $PIP_SOURCE_DIR/$FILENAME.zip
        fi
    fi    
done        
        ''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_29_audit_build_pip_sources(self):
        '''
        Download PIP sources.
        '''
        os.chdir(self.curdir)

        lines = []
        # if not self.ps.rebuild:
        #     return 
        # pps = " ".join([''] + self.pp.rebuild)

        pps = self.python_rebuild_profiles.get_list_of_pip_packages()
        if not pps.strip():
            return 

        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"
PIP_SOURCE_DIR={self.pip_source_dir}
mkdir -p $PIP_SOURCE_DIR
        ''')

        for pp, command in self.python_rebuild_profiles.get_commands_to_build_packages():
            lines.append(f'''
PP={pp}
VERSION=`cat tmp/pip-list.json | jq -j "map(select(.name==\\"$PP\\")) | .[0].version"`
FILENAME=$PP-$VERSION
PPDIR=$PIP_SOURCE_DIR/$FILENAME
{self.tb_mod} bash -c "cd $PPDIR; {command}" 
{self.tb_mod} find $PPDIR -name "*.whl" | xargs -i{{}} cp {{}} {self.rebuilded_whl_path}/ 
        ''')

        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_25_download_wheels(self):
        '''
        Consistent downloading all needed pip wheel packages
        '''
        os.chdir(self.curdir)
        # os.chdir(self.out_dir)

        root_dir = self.root_dir
        args = self.args

        bin_dir = os.path.relpath(self.in_bin, start=self.curdir)

        lines = []
        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"

''')

        pip_args_ = self.pip_args_from_sources()
        remove_pips = self.pp.remove_from_download or []
        remove_pips_str = " ".join(remove_pips)

        scmd = f"python -m pipenv run python -m pip download wheel {pip_args_} --dest {self.ext_whl_path} --find-links='{self.our_whl_path}' --find-links='{self.base_whl_path}'  "
        scmd_srcs = f"{self.tb_mod} python -m pipenv run python -m pip download --no-build-isolation {self.base_wheels_string()} {pip_args_} --dest {self.ext_pip_path} --find-links='{self.our_whl_path}' --find-links='{self.base_whl_path}' --no-binary :all: "
        lines.append(f'''
{bashash_ok_folders_strings(self.ext_whl_path, [self.src_dir], [scmd, remove_pips_str],
        f"Looks required RPMs already downloaded"
        )}
               
rm -f {self.ext_whl_path}/*
{self.tb_mod} {scmd}
''')

        for py_ in remove_pips:
            scmd = f'rm -f {self.ext_whl_path}/{py_}-*'
            lines.append(scmd)
        lines.append(f'''
{self.tb_mod} python -c "import os; whls = [d.split('.')[0]+'*' for d in os.listdir('{bin_dir}/ourwheel')]; os.system('cd {bin_dir}/extwheel; rm -f ' + ' '.join(whls))"
{save_state_hash(self.ext_whl_path)}
''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        # self.lines2sh("12-download-pip-sources", [scmd_srcs], "download-pip-sources")
        pass

    def stage_26_compile_pip_tars(self):
        '''
        Compile TAR python packages for which not exists WHL
        '''
        os.chdir(self.curdir)
        # os.chdir(self.out_dir)

        root_dir = self.root_dir
        args = self.args

        bin_dir = os.path.relpath(self.in_bin, start=self.curdir)

        lines = []
        lines.append(f'''
x="$(readlink -f "$0")"
d="$(dirname "$x")"

''')

        pip_args_ = self.pip_args_from_sources()

        lines.append(f'''
{bashash_ok_folders_strings(self.ext_compiled_tar_path, [self.ext_whl_path], [],
        f"Looks like python tars already compiled"
        )}
               
rm -f {self.ext_compiled_tar_path}/*
{self.tb_mod} bash -c "find {self.ext_whl_path} -name '*.tar.*' | xargs -i[] -t python -m pipenv run python -m pip wheel [] --no-deps --wheel-dir {self.ext_compiled_tar_path}"
{save_state_hash(self.ext_compiled_tar_path)}
''')
        mn_ = get_method_name()
        self.lines2sh(mn_, lines, mn_)
        pass


    def stage_90_audit_analyse(self):
        '''
        Analyse strace file to calculate unused files.
        '''
        if not self.build_mode:
            mn_ = get_method_name()
            lines = [
                f'''
{sys.executable} {sys.argv[0]} "{self.args.specfile}" --stage-audit-analyse                
                ''']
            self.lines2sh(mn_, lines, mn_)
            return


        if not self.args.stage_audit_analyse:
            return

        spec = self.spec
        abs_path_to_out_dir = os.path.abspath(self.out_dir)

        def cloc_for_files(clocname, filetemplate):
            cloc_csv = f'tmp/{clocname}.csv'
            if not os.path.exists(cloc_csv):
                if shutil.which('cloc'):
                    os.system(f'cloc {filetemplate} --csv  --report-file={cloc_csv} --3')
            if os.path.exists(cloc_csv):
                table_csv = []
                with open(cloc_csv, newline='') as csvfile:
                    csv_r = csv.reader(csvfile, delimiter=',', quotechar='|')
                    for row in list(csv_r)[1:]:
                        row[-1] = int(float(row[-1]))
                        table_csv.append(row)

                table_csv[-1][-2], table_csv[-1][-1] = table_csv[-1][-1], table_csv[-1][-2]
                write_doc_table(f'tmp/{clocname}.htm', ['Файлов', 'Язык', 'Пустых', 'Комментариев', 'Строчек кода', 'Мощность языка', 'COCOMO строк'],
                                table_csv)

        # cloc ./in/bin/rpmbuild/*/BUILD/   ./in/src/
        cloc_for_files('our-cloc', './in/src/')
        cloc_for_files('deps-cloc', './in/bin/rpmbuild/*/BUILD/')

        lastdirs = os.path.sep.join(
            abs_path_to_out_dir.split(os.path.sep)[-2:])

        trace_file = None
        if not 'tracefile' in spec.tests:
            print('You should specify tests→tracefile')
            return

        tracefiles = []
        if isinstance(spec.tests.tracefile, str):
            tracefiles.append(spec.tests.tracefile)

        if isinstance(spec.tests.tracefile, list):
            tracefiles.extend(spec.tests.tracefile)

        for i_ in range(len(tracefiles)):
            tracefiles[i_] = str(Path(tracefiles[i_]).resolve())
            tracefiles[i_] = os.path.expandvars(tracefiles[i_])

        used_files = set()
        for trace_file_glob in tracefiles:
            print(f'Looking strace files in {trace_file_glob}')
            for trace_file in glob.glob(trace_file_glob):
                re_file = re.compile(
                    r'''.*\([^"]*.\"(?P<filename>[^"]+)\".*''')
                for linenum, line in enumerate(open(trace_file, 'r', encoding='utf-8').readlines()):
                    m_ = re_file.match(line)
                    if m_:
                        fname = m_.group('filename')
                        # if 'lib64/girepository-1.0/GdkPixbuf-2.0.typelib' in fname:
                        #     wtf = 1
                        # Heuristic to process strace files from Vagrant virtualboxes
                        fname = fname.replace('/vagrant', self.curdir)
                        # Heuristic to process strace files from remote VM, mounted by sshmnt
                        fname = re.sub(
                            fr'''/mnt/.*{lastdirs}''', abs_path_to_out_dir, fname)
                        fname = re.sub(self.spec.install_dir,
                                       abs_path_to_out_dir, fname)
                        if os.path.isabs(fname):
                            fname = os.path.abspath(fname)
                            if fname.startswith(abs_path_to_out_dir):
                                if os.path.islink(fname):
                                    link_ = os.readlink(fname)
                                    fname = os.path.abspath(os.path.join(
                                        os.path.split(fname)[0], link_))
                                used_files.add(os.path.abspath(fname))

        file_package_list, file2package = self.load_file_package_list_from_rpms()
        file_source, _ = self.load_files_source()

        # terra_packages = set([p.strip('\n') for p in open(self.terra_rpms_closure).readlines()] + self.ps.terra + self.ps.rebuild)
        # pps = self.python_rebuild_profiles.get_list_of_pip_packages().strip().split()

        # terra_packages = set([p.strip('\n') for p in open(self.terra_rpms_closure).readlines()] + self.ps.terra)
        file_source_from_packages = [r for r in file_source if r.source_type=='package']
        packages_from_build = set([r.source for r in file_source_from_packages])
        not_used_packages = set()
        for p_ in packages_from_build:
            if p_ == 'glibc':
                wtf = 1
            # files_in_package = set([r.filename for r in file_package_list if r.package==p_])
            used_files_from_thepackage = set([os.path.join(self.curdir, self.out_dir) + '/' + r.relname for r in file_source_from_packages if r.source==p_])
            if not (used_files_from_thepackage & used_files):
                not_used_packages.add(p_)
            pass

        with open('likely-not-used-packages.txt', 'w') as lf:
            lf.write('\n - '.join([''] + sorted(list(not_used_packages))))

        with open('used-packages.txt', 'w') as lf:
            lf.write('\n - '.join([''] + sorted(list(packages_from_build - not_used_packages))))

        with open('used-files.txt', 'w') as lf:
            lf.write('\n - '.join([''] + sorted(list(used_files))))

        lines = []
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

        top10 = sorted(existing_files.items(), key=lambda x: -x[1])[:4000]
        for f, s in top10:
            rel_f = f.replace(abs_path_to_out_dir, '')
            ignore_ = False
            for re_ in self.br.ignore_re:
                if re_.match(f):
                    ignore_ = True
                    break
            if ignore_:
                continue
            unban_ = False
            for unban in [  # '/usr/lib64/python3.9','/lib64/python3.9'
            ]:
                if unban in rel_f:
                    unban_ = True
                    breakpoint
            if unban_:
                continue
            # f_ = re.escape(rel_f)
            f_ = rel_f
            for rex_ in [
                # act\.so\.4\.0\.1 → act\.so\.\d\.\d\.\d
                r"\.so\.[\d]+\.[\d]+\.[\d]+",
                r"\.so\.[\d]+\.[\d]+",
                r"\.so\.[\d]+",
                r"[\d]+\.[\d]+\.so",
                r"[\d]+\.so",
                r"-[\d]+\.[\d]+-[\d]+",
                r"c\+\+",
            ]:
                def replacement_f(mobj):
                    return rex_
                f_ = re.sub(rex_, replacement_f, f_)
            if f_ == rel_f:
                f_ = re.escape(rel_f)

            lines.append(f'      - .*{f_} # \t {s} \t {rel_f}')

        with open('recommend-for-exclude', 'w') as lf:
            lf.write('\n'.join(lines))
        # print("\n".join([f'{f}: \t {s}' for f,s in top10]))

        pass

    # def pack_me(self):
    #     if self.args.stage_pack_me:
    #         return

    #     time_prefix = datetime.datetime.now().replace(
    #         microsecond=0).isoformat().replace(':', '-')
    #     parentdir, curname = os.path.split(self.curdir)
    #     disabled_suffix = curname + '.tar.bz2'

    #     banned_ext = ['.old', '.iso', '.lock',
    #                   disabled_suffix, '.dblite', '.tmp', '.log']
    #     banned_start = ['tmp']
    #     banned_mid = ['/out', '/wtf', '/ourwheel/', '/.vagrant', '/.git', '/.vscode', '/key/',
    #                   '/tmp/', '/src.', '/bin.',  '/cache_', 'cachefilelist_', '/tmp', '/.image', '/!']

    #     # there are regularly some files unaccessable for reading.
    #     self.cmd('sudo chmod a+r /usr/lib/cups -R')
    #     self.cmd('systemd-tmpfiles --remove dnf.conf')

    #     def filter_(tarinfo):
    #         for s in banned_ext:
    #             if tarinfo.name.endswith(s):
    #                 print(tarinfo.name)
    #                 return None

    #         for s in banned_start:
    #             if tarinfo.name.startswith(s):
    #                 print(tarinfo.name)
    #                 return None

    #         for s in banned_mid:
    #             if s in tarinfo.name:
    #                 print(tarinfo.name)
    #                 return None

    #         return tarinfo

    #     tbzname = os.path.join(self.curdir,
    #                            "%(time_prefix)s-%(curname)s.tar.bz2" % vars())
    #     tar = tarfile.open(tbzname, "w:bz2")
    #     tar.add(self.curdir, "./sources-for-audit",
    #             recursive=True, filter=filter_)
    #     tar.close()

    def load_file_package_list_from_rpms(self):
        file_package_list = []
        file2package = {}
        with open(self.file_package_list_from_rpms, 'r') as lf:
            for i, line in enumerate(lf.readlines()):
                line = line.strip('\n')
                pfr = PackageFileRow(*line.split(ROW_SPLIT))
                file_package_list.append(pfr)
                file2package[pfr.filename] = pfr
        return file_package_list, file2package

    def load_files_source(self):
        alist = []
        key2row = {}
        with open(self.files_source_path, 'r') as lf:
            for i, line in enumerate(lf.readlines()):
                line = line.strip('\n')
                terms = [p.strip() for p in line.split(ROW_SPLIT)]
                row = FileInBuild(*terms)
                alist.append(row)
                key2row[row.relname] = row
        return alist, key2row


    def stage_50_pack(self):
        '''
        Packing portable environment
        '''
        if not self.build_mode:
            lines = [
                f'''
{sys.executable} {sys.argv[0]} --stage-pack "{self.args.specfile}"
                ''' ]
            mn_ = get_method_name()
            self.lines2sh(mn_, lines, mn_)
            return

        if not self.args.stage_pack:
            return

        args = self.args
        spec = self.spec
        # root_dir = os.path.realpath(self.out_dir)
        root_dir = self.root_dir = expandpath(self.out_dir)

        t.tic()

        file_package_list, file2rpmpackage = self.load_file_package_list_from_rpms()
        sofile2rpmfile = {}
        for f_ in file2rpmpackage:
            if '.so' in f_:
                sofile2rpmfile[os.path.split(f_)[-1]] = f_

        so_files_from_venv_filename2path = {}
        so_files_from_venv_path2package = {}
        so_files_from_venv_set = {}
        with open(self.so_files_from_venv, 'r') as lf:
            for i, line in enumerate(lf.readlines()):
                line = line.strip('\n')
                fname = os.path.split(line)[-1]
                so_files_from_venv_filename2path[fname] = line
                package_ = 'unknown'
                if 'site-packages' in line:
                    package_ = line.split('site-packages')[-1].split(os.path.sep)[1]
                so_files_from_venv_path2package[line] = package_


        so_files_rpips_filename2path = {}
        so_files_rpips_path2package = {}
        split_ = os.path.split(self.pip_source_dir)[-1]
        with open(self.so_files_from_rebuilded_pips, 'r') as lf:
            for i, line in enumerate(lf.readlines()):
                line = line.strip('\n')
                fname = os.path.split(line)[-1]
                so_files_rpips_filename2path[fname] = line
                package_name = line.split(split_)[1].split(os.path.sep)[1]
                so_files_rpips_path2package[line] = package_name

        so_files_from_src_filename2path = {}
        so_files_from_src_path2folder = {}
        split_ = self.src_dir
        with open(self.so_files_from_our_packages, 'r') as lf:
            for i, line in enumerate(lf.readlines()):
                line = line.strip('\n')
                fname = os.path.split(line)[-1]
                so_files_from_src_filename2path[fname] = line
                folder_name = line.split(split_)[1].split(os.path.sep)[1]
                so_files_from_src_path2folder[line] = folder_name



        def install_templates(root_dir, args):
            if 'copy_folders' in self.spec:
                for it_ in self.spec.copy_folders or []:
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
                if from_.strip():
                    scmd = f'rsync -rav {from_}/ {to_}'
                    print(scmd)
                    os.system(scmd)

                wtfff = 1

            for td_ in spec.templates_dirs:
                git_url, git_branch, path_to_dir, _ = self.explode_pp_node(td_)
                if 'subdir' in td_:
                    path_to_dir = os.path.join(path_to_dir, td_.subdir)

                file_loader = FileSystemLoader(path_to_dir)
                env = Environment(loader=file_loader, keep_trailing_newline=True)
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
                        fname_ = os.path.join(dirpath, filename)
                        if 'python' in fname_:
                            wtf = 1
                        out_fname_ = os.path.join(root_dir, dirpath, filename)
                        out_fname_ = Template(out_fname_).render(self.tvars)
                        # Path(out_fname_).parent.mkdir(exist_ok=True)

                        plain = False
                        try:
                            if 'users.xml' in fname_:
                                dfdsfdsf = 1
                            m = fucking_magic(fname_)
                            for t_ in ['ASCII text', 'UTF8 text', 'Unicode text', 'UTF-8 text']:
                                if t_ in m:
                                    plain = True
                                    break
                        except Exception:
                            pass
                        print(f"Processing template «{fname_}» type «{m}»...")
                        if os.path.islink(fname_):
                            linkto = os.readlink(fname_)
                            os.symlink(linkto, out_fname_)
                        else:
                            processed_ = False
                            if fname_.endswith('.copy-file'):
                                if 'error' in fname_:
                                    wtf = 4
                                out_fname_ = os.path.splitext(out_fname_)[0]
                                path_ = self.toolbox_path(open(fname_).read().strip())
                                if not os.path.isabs(path_):
                                    path_ = os.path.join(self.curdir, path_)
                                if path_.strip() and os.path.isdir(path_):
                                    # shutil.copytree(path_, out_fname_)
                                    scmd = f'rsync -rav --exclude ".git" {path_}/ {out_fname_}'
                                    self.cmd(scmd)
                                else:
                                    shutil.copy2(path_, out_fname_)
                                processed_ = True    
                            elif plain or fname_.endswith('.nj2'):
                                try:
                                    if fname_.endswith('compton.conf'):
                                        wtf = 1
                                    template = env.get_template(fname_)
                                    output = template.render(self.tvars)
                                    try:
                                        with open(out_fname_, 'a', encoding='utf-8') as lf_:
                                            pass
                                    except PermissionError as ex_:
                                        scmd = f'chmod u+w "{out_fname_}"'
                                        os.system(scmd)
                                    with open(out_fname_, 'w', encoding='utf-8') as lf_:
                                        lf_.write(output)
                                    processed_ = True        
                                except jinja2.exceptions.TemplateError as ex_:         
                                    print(f'''{fname_} looks not Jinja template''')
                            if not processed_:        
                                shutil.copy2(fname_, out_fname_)
                            if not os.path.isdir(out_fname_):
                                shutil.copymode(fname_, out_fname_)

            # from ctypes.util import _findLib_ld
            # libc_path = _findLib_ld('c')
            # libc_path = "/lib64/libc-2.31.so"  # temp hack #wtf!!!!

            # from ctypes.util import _findSoname_ldconfig
            # libc_path = "/lib64/" + _findSoname_ldconfig('c')
            # shutil.copy2(
            #     libc_path, f'{root_dir}/lib64/libc.so', follow_symlinks=True)

            ebin_ = os.path.join(root_dir, 'ebin')
            self.cmd(f'chmod a+x {ebin_}/*')

            print("Install templates takes")
            t.toc()

        try:
            json_ = json.loads(open(self.pip_list_json).read())
            rows_ = []
            for r_ in json_:
                rows_.append([r_['name'], r_['version']])

            write_doc_table('doc-python-packages.htm', ['Package', 'Version'], sorted(rows_))
        except Exception as ex_:
            print(ex_)
            pass

        # self.remove_exclusions()


        # install_templates(root_dir, args)

        packages_to_deploy = []
        pips_to_deploy = []
        if self.args.debug:
            packages_to_deploy += self.ps.terra + self.ps.build
            pips_to_deploy = self.pp.pip()
        else:
            packages_to_deploy = self.ps.terra
            pips_to_deploy = self.pp.terra.pip or []

        # fs_ = self.generate_files_from_pips(pips_to_deploy)

        # packages_ = []
        # for p_ in (Path(self.in_bin) / rpms).glob('*.rpm'):
        #     try:
        #         vp_ = version_utils.rpm.package(p_)
        #     except:
        #         pass    

        # file_list = None
        # if Path(self.file_list_from_rpms).exists(): 
        # # and Path(self.doc_list_from_rpms).exists():
        #     all_list = open(self.file_list_from_rpms).readlines()
        #     # doc_list = open(self.doc_list_from_rpms).readlines()
        #     file_list = [x.strip() for x in set(all_list) if x.strip() and self.should_copy(x.strip())]
        #     #- set(doc_list)
        # else:    
        #     assert(False)
        #     # deps_packages = self.dependencies(packages_to_deploy)
        #     # file_list = self.generate_file_list_from_packages(deps_packages)


        # file_list.extend(fs_)

        os.system('echo 2 > /proc/sys/vm/drop_caches ')
        user_ = os.getlogin()            
        scmd = f'sudo chown {user_} {root_dir} -R '
        self.cmd(scmd)
        old_root_dir = root_dir + ".old"
        if os.path.exists(old_root_dir):
            scmd = f'sudo chown {user_} {old_root_dir} -R '
            self.cmd(scmd)
            shutil.rmtree(old_root_dir, ignore_errors=True)
        if os.path.exists(old_root_dir):
            os.system("rm -rf " + old_root_dir)
        if os.path.exists(root_dir):
            shutil.move(root_dir, old_root_dir)

        mkdir_p(root_dir)

        def copy_file_to_environment(f):

            if not self.should_copy(f):
                assert(False)

            tf = self.toolbox_path(f)
            if os.path.isdir(tf):
                return None

            if self.br.is_need_patch(f):
                return self.process_binary(f)
            
            if self.br.is_just_copy(f):
                return self.add(f)
            elif self.args.debug and f.startswith("/usr/include"):
                return self.add(f)
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
                    return None

                # copy file instead of link unless we link to the current directory.
                # links to the current directory are usually safe, but because we are manipulating
                # the directory structure, very likely links that transverse paths will break.
                # os.path.islink(f) and os.readlink(f) != os.path.basename(os.readlink(f)):
                #     rp_ = os.path.realpath(f)
                #     if os.path.exists(rp_):
                #         add(os.path.realpath(f), libfile)
                if 1:
                    if not os.path.exists(tf) and os.path.splitext(f)[1] not in ['.rpmmoved', '.debug']:
                        print("Missing %s" % f)
                        return
                        # # assert(False)
                    try:
                        m = fucking_magic(tf)
                    except Exception as ex_:
                        print("Cannot detect Magic for ", tf)
                        raise ex_
                    if m.startswith('ELF') and 'shared' in m:
                        # startswith('application/x-sharedlib') or m.startswith('application/x-pie-executable'):
                        try:
                            self.fix_sharedlib(tf, libfile)
                            self.bin_files_sources[libfile] = f
                            return libfile
                        except:
                            print('Cannot optionally patch', tf)    
                            assert(False)
                    else:
                        # in case this is a directory that is listed, we don't want to include everything that is in that directory
                        # for instance, the python3 package will own site-packages, but other packages that we are not packaging could have
                        # filled it with stuff.
                        return self.add(f, libfile, recursive=False)
                        # shutil.copy2(f, os.path.join(root_dir, libfile))
                        # add(f, arcname=libfile, recursive=False)
            pass

        self.cmd(f'{self.tb_mod} sudo chmod a+r /usr/lib/cups -R')

        file_source_table = {}        

        terra_closure_packages = [p.strip('\n') for p in open(self.terra_rpms_closure).readlines()] + self.ps.terra
        for fpl_ in file_package_list:
            if fpl_.package == 'glibc':
                wtf = 1
            if 'libc.so' in fpl_.filename:    
                wtf = 1
            if fpl_.package in terra_closure_packages and not fpl_.package in self.ps.terra_exclude:
                ok = True
                for prefix in self.ps.exclude_prefix:
                    if fpl_.package.startswith(prefix):
                        ok = False
                    break
                if ok:    
                    f = fpl_.filename
                    if not self.should_copy(f):
                        continue
                    relpath = copy_file_to_environment(f)
                    if relpath:
                        file_source_table[relpath] = FileInBuild(relpath, 'package', fpl_.package, f)

        if self.fs:
            for folder_ in self.fs.folders:
                nuitka_report = {}
                map2source = {}
                if '.dist' in folder_:
                    bfolder_ = folder_.replace('.dist', '.build')
                    report_xml = Path(bfolder_) / 'report.xml'
                    if report_xml.exists():
                        import xmltodict
                        with open(report_xml, 'r', encoding='utf8') as lf:
                            nuitka_report = xmltodict.parse(lf.read())['nuitka-compilation-report']
                            for ie_ in nuitka_report['included_extension'] + nuitka_report['included_dll']:
                                sp_ = ie_['@source_path']
                                dp_ = ie_['@dest_path']
                                spr_ = Path(sp_.replace('${sys.prefix}', '.venv').replace('${sys.real_prefix}', '')).resolve()
                                if '.venv/lib64' in spr_.as_posix():
                                    wtf = 1
                                assert(spr_.exists())
                                map2source[dp_] = spr_.as_posix()
                for dirpath, dirnames, filenames in os.walk(folder_):
                    for filename in filenames:
                        f = os.path.join(dirpath, filename)
                        if 'Shapely.libs' in f:
                            wtf  = 1
                        sfilename = filename
                        rf = os.path.relpath(f, start=folder_)
                        if rf in map2source:
                            if '_random.so' in f:
                                wtf  = 1
                            f = map2source[rf]
                            if '.venv/lib64' in f:
                                wtf = 1
                            sfilename = os.path.split(f)[-1]
                        if 'dm_ort.cpython-310-x86_64-linux-gnu.so' in f:
                            wrtf=1
                        if sfilename in so_files_from_src_filename2path:
                            f = so_files_from_src_filename2path[sfilename]
                        elif sfilename in so_files_rpips_filename2path:
                            f = so_files_rpips_filename2path[sfilename]
                        # elif sfilename in so_files_from_venv_filename2path:
                        #     f = so_files_from_venv_filename2path[sfilename]
                        #     # changin path to 
                        #     wrtf=1
                        # elif filename in sofile2rpmfile:    
                        #     f = sofile2rpmfile[filename]
                        #     tf = self.toolbox_path(f)
                        #     ptf = Path(tf)
                        #     if ptf.is_symlink():
                        #         liblink = os.path.join(root_dir, 'lib64', filename)
                        #         if not os.path.exists(liblink):
                        #             os.symlink(Path(tf).resolve().name, liblink)
                        #     libname = Path(tf).resolve().name
                        #     filename = libname
                        #     f = sofile2rpmfile[libname]
                        if self.br.is_need_patch(f):
                            relname = self.process_binary(f)
                            if os.path.isabs(relname):
                                wtf = 1
                            file_source_table[relname] = FileInBuild(relname, 'folder', folder_, f)
                            continue
                        m = ''
                        try:
                            tf = self.toolbox_path(f)
                            m = fucking_magic(tf)
                        except Exception as ex_:
                            print("Cannot detect Magic for ", f)
                            raise ex_
                        if m.startswith('ELF') and 'shared' in m:
                            # startswith('application/x-sharedlib') or m.startswith('application/x-pie-executable'):
                            if '_random.so' in filename:
                                wtf = 1
                            if '.libs' in rf:
                                wtf = 1
                            relname = 'pbin/' + filename
                            if '/' in rf and not '..' in rf:
                                relname = 'pbin/' + rf
                            elif filename.startswith('lib'):
                                 relname = 'lib64/' + filename
                            # if Path(tf).is_absolute():
                            #     relname = f'lib64/' + filename
                            # elif self.src_dir in f:
                            #     relname = 'lib64/' + filename
                            # elif f.startswith('.venv/lib'):
                            #     relname = 'lib64/' + filename
                            # elif filename.startswith('lib') and dirpath == folder_:
                            #     relname = f.replace(folder_, 'lib64')
                            # else:
                            #     relname = f.replace(folder_, 'pbin')    
                            if relname not in file_source_table:
                                type_ = 'folder'
                                what_ = folder_ 
                                source_ = f
                                if filename in sofile2rpmfile:
                                    type_ = 'package'
                                    source_ = sofile2rpmfile[filename]
                                    package_ = file2rpmpackage[source_]
                                    file_source_table[relpath] = FileInBuild(relpath, 'package', package_.package, source_)
                                elif f in so_files_from_src_path2folder:
                                    type_ = 'our_package'
                                    what_ = so_files_from_src_path2folder[f]
                                elif f in so_files_rpips_path2package:
                                    type_ = 'rebuilded_python_package'
                                    what_ = so_files_rpips_path2package[f]
                                elif f in so_files_from_venv_path2package:
                                    type_ = 'python_package'
                                    what_ = so_files_from_venv_path2package[f]
                                file_source_table[relname] = FileInBuild(relname, type_, what_, source_)
                                self.bin_files_sources[relname] = source_
                                self.fix_sharedlib(tf, relname)
                        else:
                            relname = f.replace(folder_, 'pbin')
                            self.add(f, relname, recursive=False)
                pass

        scmd = f"""
{self.tb_mod} python3 -c "from ctypes.util import _findSoname_ldconfig;print(_findSoname_ldconfig('c'))"      
"""            
        libc_name = subprocess.check_output(scmd, shell=True).decode('utf-8').strip()
        self.cmd(f"ln -s {libc_name} {self.out_dir}/libc.so")

        install_templates(root_dir, args)
        # self.install_terra_pythons()
        # install_templates(root_dir, args)
        # self.install_terra_pythons()

        # if self.args.debug:
        #     self.overwrite_mode = True
        #     for f in file_list:
        #         copy_file_to_environment(f)

        os.chdir(root_dir)
        # if [_ for _ in Path(f'{root_dir}/pbin/').glob('python3.*')]:
        #     scmd = "%(root_dir)s/ebin/python3 -m compileall -b . " % vars()
        #     self.cmd(scmd)

        # if 0 and not self.args.debug:
        #     # Remove source files.
        #     scmd = "shopt -s globstar; rm  **/*.py; rm  -r **/__pycache__"
        #     print(scmd)
        #     os.system(scmd)
        #     pass
        # size_ = sum(file.stat().st_size for file in Path(self.root_dir).rglob('*'))
        # Postprocessing, removing not needed files after installing python modules, etc
        def remove_exclusions():
            '''
            Postprocessing, removing not needed files after installing python modules, etc
            '''
            # for path in Path(self.root_dir).rglob('*'):
            for fib in list(file_source_table.keys()):
                # rp_ = str(path.absolute())
                rp_ = fib
                if self.br.is_need_exclude(rp_):
                    # rel_file = os.path.relpath(rp_, start = os.getcwd())
                    rel_file = rp_
                    del file_source_table[fib]
                    if rel_file in self.bin_files_sources:
                        del self.bin_files_sources[ rel_file ]
                    Path(rel_file).unlink(missing_ok=True)

            # killing broken links
            for path in Path(self.root_dir).rglob('*'):
                rp_ = str(path.absolute())
                if os.path.islink(rp_) and not os.path.exists(rp_):
                    path.unlink(missing_ok=True)
            pass

        remove_exclusions()

        with open(f'{self.curdir}/obsoletes_excludes.txt', 'wt', encoding='utf-8') as lf:
            lf.write('obsoletes excludes \n')
            for re_, cnt_ in self.br.need_exclude_re.items():
                if cnt_ == 0:
                    pat_ = re_.pattern
                    lf.write(f'   {pat_} \n')

        bf_ = [os.path.abspath(f) for f in self.bin_files if os.path.isabs(f)] + [os.path.join(root_dir, f) for f in self.bin_files if not os.path.isabs(f)]

        with open(Path(self.curdir) / self.bin_files_name, 'wt') as lf:
                for i, it in enumerate(split_seq([f for f in sorted(bf_) if os.path.exists(f)], 100)):
                    with open(Path(self.curdir) / (self.bin_files_name + f'.chunk{i:02}'), 'wt') as lc:
                        lc.write("\n".join(it))
                    lf.write("\n".join(it))

        size_ = folder_size(self.root_dir, follow_symlinks=False)

        print("Size ", size_/1024/1024, 'Mb')

        rpm_packages_recommended_for_rebuild = set()
        python_packages_recommended_for_rebuild = set()
        lines = []
        for file_, source_ in self.bin_files_sources.items():
            if 'dm_ort.cpython-310-x86_64-linux-gnu.so' in source_:
                wtf = 1
            if source_ in so_files_from_src_filename2path:
                pp_ = so_files_from_src_filename2path[source_]
                lines.append(f'{file_} <- {source_} from our project {pp_}')
            elif source_ in file2rpmpackage:
                pfr = file2rpmpackage[source_]
                if '.' in pfr.release and self.disttag in pfr.release.split('.'):
                    lines.append(f'{file_} <- {source_} from rebuilded RPM package {pfr.package}')
                else:
                    if 'pcre' in pfr.package:
                        wtf = 1
                    lines.append(f'{file_} <- {source_} from package {pfr.package}!')
                    rpm_packages_recommended_for_rebuild.add(pfr.package)
            elif source_ in so_files_rpips_path2package:
                pp_ = so_files_rpips_path2package[source_]
                lines.append(f'{file_} <- {source_} from rebuilded RPM package {pp_}')
            elif source_ in so_files_from_venv_path2package:
                pp_ = so_files_from_venv_path2package[source_]
                lines.append(f'{file_} <- {source_} from python package {pp_}!')
                python_packages_recommended_for_rebuild.add(pp_)
            else:
                lines.append(f'{file_} -< {source_} not known!!!')

        lines.append(f'\nRPM packages recommended for rebuild')
        lines.append('\n - '.join([''] + sorted(rpm_packages_recommended_for_rebuild)))

        lines.append(f'\nPython packages recommended for rebuild')
        lines.append('\n - '.join([''] + sorted(python_packages_recommended_for_rebuild)))

        with open(os.path.join(self.curdir, 'binary-files-report.txt'), 'w') as lf:
            lf.write('\n'.join(lines))

        lines = []
        for relpath, fpl_ in file_source_table.items():
            lines.append(f'{fpl_.relname} {ROW_SPLIT} {fpl_.source_type} {ROW_SPLIT} {fpl_.source} {ROW_SPLIT} {fpl_.source_path} ')

        with open(os.path.join(self.curdir, self.files_source_path), 'w') as lf:
            lf.write('\n'.join(lines))

        unique_packages = set([row.source for row in file_source_table.values() if row.source_type == 'package'])
        not_need_packages_to_rebuild = []
        for p in self.packages_to_rebuild:
            if p not in unique_packages:
                not_need_packages_to_rebuild.append(p)

        with open(os.path.join(self.curdir, 'not-need-packages-in-terra.txt'), 'w') as lf:
            lf.write('\n - '.join([''] + not_need_packages_to_rebuild))


    def get_version(self):
        os.chdir(self.curdir)
        version_ = get_git_version()
        if 'version' in self.spec:
            versions_ = self.spec.version
            if version.parse(versions_) > version.parse(version_):
                version_ = versions_
        return version_        


    def stage_59_make_packages(self):
        '''
        Make DEB/RPM/ISO packages
        '''
        if not self.build_mode:
            lines = [
                f'''
{sys.executable} {sys.argv[0]} {self.args.specfile} --stage-make-packages
                ''']
            mn_ = get_method_name()
            self.lines2sh(mn_, lines, mn_)
            return

        if not self.args.stage_make_packages:
            return
        
        os.chdir(self.curdir)

        root_dir = os.path.realpath(self.out_dir)
        user_ = os.getlogin()            
        scmd = f'sudo chown {user_} {root_dir} -R '
        self.cmd(scmd)

        label = 'disk'
        if 'label' in self.spec:
            label = self.spec.label

        git_version = self.get_version()

        nfpm_dir = os.path.join(self.curdir, 'tmp/nfpm')
        mkdir_p(nfpm_dir)

        current_time = datetime.datetime.now().replace(microsecond=0)
        time_ = current_time.isoformat().replace(':', '').replace('-', '').replace('T', '')
        deployname = f"{label.lower()}-{git_version}-{time_}" 

        prev_release_time = current_time + relativedelta(months=-1)
        old_changelogs = sorted([f for f in (Path(self.curdir) / self.changelogdir).glob(f'*.txt') if f.is_file() and not f.is_symlink()], key=os.path.getmtime)

        for changelog_ in reversed(sorted(old_changelogs)):
            tformat_ = '%Y%m%d%H%M%S'
            timedt_ = '-'.join(changelog_.name.split('-')[2:])[:len(tformat_)]
            prev_release_time = datetime.datetime.strptime(timedt_, tformat_)
            break

        since_time_ = prev_release_time.isoformat()
        gitlogcmd_ = f'git log --since="{since_time_}" --pretty --name-status '

        lines_ = []
        for git_url, git_branch, path_to_dir_ in self.get_all_sources():
            os.chdir(self.curdir)
            if os.path.exists(path_to_dir_):
                os.chdir(path_to_dir_)
                with suppress(Exception):
                    change_ = subprocess.check_output(
                        gitlogcmd_, shell=True).decode('utf-8').strip()
                    if change_:
                        lines_.append(
                            f'----\n Changelog for {path_to_dir_} ({git_url} / {git_branch})')
                        lines_.append(change_)
        pass

        changelogfilename = (Path(self.curdir) / self.changelogdir) / f'{deployname}.changelog.txt'
        open(changelogfilename, 'w', encoding='utf-8').write('\n'.join(lines_))

        isofilename = f"{deployname}.iso"
        chp_ = os.path.join(root_dir, 'isodistr.txt')
        open(chp_, 'w', encoding='utf-8').write(isofilename)

        package_modes = self.package_modes
        # if self.args.stage_make_packages:
        #     package_modes = self.args.stage_make_packages.split(',')

        os.chdir(nfpm_dir)
        install_mod = ''            
        if 'post_installer' in self.spec:
            with open(os.path.join(nfpm_dir, 'postinstall.sh'), 'w', encoding='utf-8') as lf:
                lf.write(f'''
#!/bin/bash
{self.spec.post_installer}
            '''.strip())
            install_mod ="""
      postinstall: ./postinstall.sh
"""

        remove_mod = ''            
        if 'pre_remove' in self.spec:
            with open(os.path.join(nfpm_dir, 'pre_remove.sh'), 'w', encoding='utf-8') as lf:
                lf.write(f'''
#!/bin/bash
{self.spec.pre_remove}
            '''.strip())
                remove_mod ="""
      preremove: ./pre_remove.sh
"""

        with open(os.path.join(nfpm_dir, 'nfpm.yaml'), 'w', encoding='utf-8') as lf:
            lf.write(f'''
name: "{self.spec.label.lower()}"
arch: "amd64"
platform: "linux"
version: "v{git_version}-{time_}"
section: "default"
priority: "extra"
maintainer: "{self.spec.maintainer}"
description: "{self.spec.description} "
vendor: "{self.spec.vendor} "
homepage: "{self.spec.homepage} "
license: "{self.spec.license}"
contents:
- src: ../../out
  dst: "{self.spec.install_dir}"
overrides:
  rpm:
    scripts:
{install_mod}
{remove_mod}      
  deb:
    scripts:
{install_mod}
{remove_mod}      
''')
        for packagetype in ['rpm', 'deb']:
            if not packagetype in package_modes:
                continue
            pkgdir = '../../' + self.out_dir + '.'+ packagetype
            mkdir_p(pkgdir)
            scmd = f'''
{self.tb_mod} nfpm pkg --packager {packagetype} --target {pkgdir}        
    '''.strip()
            os.chdir(nfpm_dir)
            self.cmd(scmd)

            package_dir = f'out.{packagetype}'
            os.chdir(self.curdir)
            os.chdir(package_dir)
            paths = sorted([f for f in Path('').glob(f'*.{packagetype}') if f.is_file() and not f.is_symlink()], key=os.path.getmtime)
            fname_ = paths[-1]
            scmd = f'''ln -sf {fname_} last.{packagetype}'''
            self.cmd(scmd)
        pass

        for packagetype in ['iso']:
            if not packagetype in package_modes:
                continue

            os.chdir(self.curdir)

            isodir = self.out_dir + '.iso'
            mkdir_p(isodir)

            installscript = "install-me.sh" % vars()
            os.chdir(self.curdir)
            installscriptpath = os.path.abspath(
                os.path.join("tmp/", installscript))
            if os.path.exists(installscriptpath):
                os.unlink(installscriptpath)

            pmode = ''
            if shutil.which('pbzip2'):
                pmode = ' --threads 8 --pbzip2 '
            os.chdir(self.curdir)
            self.cmd(f'chmod a+x {root_dir}/install-me')


            res_ = list(p for p in self.installed_packages if p.name=='makeself')
            add_opts = ''
            if len(res_) >= 0:
                version_ = res_[0].version
                if version.parse(version_) >= version.parse("2.4.5"):
                    add_opts = ' --tar-format posix '

                path_to_dir = Path(__file__).parent
                makeself_header_template_path = path_to_dir / "ta-makeself-header.sh"
                assert(makeself_header_template_path.exists())
                makeself_header_template = ''

                file_loader = FileSystemLoader(path_to_dir)
                env = Environment(loader=file_loader)
                env.trim_blocks = True
                env.lstrip_blocks = True
                env.rstrip_blocks = True

                # makeself_header = makeself_header_template.format(vars())
                template = env.get_template(makeself_header_template_path.name)
                makeself_header = template.render(self.tvars)

                makeself_header_path = 'tmp/makeself-header.sh'
                with open(makeself_header_path, 'w', encoding='utf-8') as lf:
                    lf.write(makeself_header)

                scmd = (f'''
{self.tb_mod} makeself.sh {pmode} {add_opts} --header {makeself_header_path} --target "{self.spec.install_dir}" --tar-extra "--xattrs --xattrs-include=*" --untar-extra " --xattrs --xattrs-include=*"  --needroot {root_dir} {installscriptpath} "Installation" {self.spec.install_dir}/install-me
        ''' % vars()).replace('\n', ' ').strip()
                if not self.cmd(scmd) == 0:
                    print(f'« {scmd} » failed!')    
                    return
            os.chdir(self.curdir)
            scmd = f'''{self.tb_mod} mkisofs -r -J -o  {isofilename}  {installscriptpath}'''
            self.cmd(scmd)
            scmd = f'''{self.tb_mod} md5sum {isofilename}'''
            os.chdir(self.curdir)
            md5s_ = subprocess.check_output(
                scmd, shell=True).decode('utf-8').strip().split()[0]
            lines_.insert(0, f';MD5: {md5s_}')
            open(f'{isodir}/{deployname}.md5', 'w', encoding='utf-8').write(md5s_)

            os.chdir(isodir)
            scmd = f'''ln -sf {isofilename} last.iso'''
            self.cmd(scmd)
            os.chdir(self.curdir)


    def process(self):
        '''
        Основная процедура генерации переносимого питон окружения.
        '''

        args = self.args
        spec = self.spec

        if self.args.folder_command:
            self.folder_command()
            return

        if self.args.git_sync:
            self.git_sync()
            return

        self.lines2sh("94-install-last-nuitka", [
            f'''
export PIPENV_VENV_IN_PROJECT=1
{self.tb_mod} python -m pipenv run pip install -e "git+https://github.com/Nuitka/Nuitka.git@develop#egg=nuitka"            
            '''])

        self.build_mode = False
        self.clear_shell_files()
        for stage_ in self.stage_methods:
            stage_()

        self.build_mode = True
        for stage_ in self.stage_methods:
            stage_()

#         self.lines2sh("91-pack-debug", [
#             f'''
# sudo chmod a+rx /usr/lib/cups -R
# terrarium_assembler --debug --stage-pack "{self.args.specfile}"
#             '''])
