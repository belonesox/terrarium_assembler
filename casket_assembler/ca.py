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
import dataclasses as dc

from .utils import *
from .nuitkaflags import *

from pytictoc import TicToc
t = TicToc()

# import dataclasses as dc
@dc.dataclass
class BinRegexps:
    '''
    Binary regexps. 
    '''
    need_patch: list #bins that neeed to be patched.   
    just_copy:  list #bins that just need to be copied.

    def __post_init__(self):
        self.just_copy_re = []
        for res_ in self.just_copy:
            re_ = re.compile(res_ + '$')
            self.just_copy_re.append(re_) 

        self.need_patch_re = []
        for res_ in self.need_patch:
            re_ = re.compile(res_ + '$')
            self.need_patch_re.append(re_) 

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

    def is_needed(self, f):
        return self.is_just_copy(f) or self.is_need_patch(f)
    
    pass


@dc.dataclass
class PythonPackages:
    pip: list
    build: list
    casket: list

@dc.dataclass
class PackagesSpec:
    '''
    Packages Spec.
    '''
    build:   list
    casket:  list 
    exclude_prefix: list 
    exclude_suffix: list

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




class CasketAssembler:
    '''
    Генерация переносимой сборки бинарных линукс-файлов (в частности питон)
    '''
    
    def __init__(self):
        self.curdir = os.getcwd()

        ap = argparse.ArgumentParser(description='Create a portable linux folder-application')
        ap.add_argument('--output', required=True, help='Destination directory')
        ap.add_argument('--release', default=False, action='store_true', help='Release version')
        ap.add_argument('--docs', default=False, action='store_true', help='Output documentation version')
        ap.add_argument('--stage-checkout', default=False, action='store_true', help='Stage for checkout sources')
        ap.add_argument('--stage-download', default=False, action='store_true', help='Stage for download binary artifacts')
        ap.add_argument('--stage-setupsystem', default=False, action='store_true', help='Stage for setup local OS')
        ap.add_argument('--stage-build-nuitka', default=False, action='store_true', help='Compile Nuitka packages')
        ap.add_argument('--specfile', required=True, help='Specification File')
        
        self.args = args = ap.parse_args()
    
        specfile_  = expandpath(args.specfile)
        os.environ['CASKET_SPECDIR'] = os.path.split(specfile_)[0]
        self.spec = spec = yaml_load(specfile_)    
        self.root_dir = expandpath(args.output)

        self.start_dir = os.getcwd()
         
        self.tvars = edict() 
        self.tvars.python_version_1, self.tvars.python_version_2 = sys.version_info[:2]
        self.tvars.py_ext = ".py"
        if self.args.release:
            self.tvars.py_ext = ".pyc"
        self.tvars.release = self.args.release

        need_patch = just_copy = None    
        if 'bin_regexps' in spec:
            br_ = spec.bin_regexps
            if "need_patch" in br_:
                need_patch = br_.need_patch
            if "just_copy" in br_:
                just_copy = br_.just_copy

        self.br = BinRegexps(
            need_patch=need_patch,
            just_copy=just_copy
        )

        self.need_packages = ['patchelf', 'ccache', 'gcc', 'gcc-c++', 'gcc-gfortran', 'chrpath']

        nflags_ = {}
        if 'nuitka' in spec:
            nflags_ = spec.nuitka

        self.nuitkas = NuitkaFlags(**nflags_)
        self.ps = PackagesSpec(**spec.packages)
        self.pp = PythonPackages(**spec.python_packages)
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

        os.environ['PATH']="/usr/lib64/ccache:" + os.environ['PATH']
        pass

    def lines2sh(self, name, lines):
        import stat
        os.chdir(self.curdir)
        fname = name + '.sh'
        with open(os.path.join(fname), 'w', encoding="utf-8") as lf:
            lf.write("#!/bin/sh\n#Generated %s \n" % name)
            lf.write("\n".join(lines))

        st = os.stat(fname)
        os.chmod(fname, st.st_mode | stat.S_IEXEC)
        pass  


    def build_nuitkas(self):
        if not self.nuitkas:
            return
        # if not "builds" in self.nuitkas:
        #     return
        # out_dir = os.path.join(self.out_dir)
        for target_ in self.nuitkas.builds:
            # outputname = target_.utility
            # if "outputname" in target_:
            #     outputname = target_.outputname
            nflags = self.nuitkas.get_flags(self.out_dir)
            target_dir = os.path.join(self.out_dir, target_.utility+'.dist')
            src = os.path.join(self.src_dir, target_.folder, target_.utility) + '.py'
            lines = []
            lines.append("""
export PATH="/usr/lib64/ccache:$PATH"
""" % vars(self))
            lines.append(R"""
python3 -m nuitka --jobs=2 %s %s 
""" % (nflags, src))
            self.fs.folders.append(target_dir)
            build_name = 'build_' + target_.utility
            self.lines2sh(build_name, lines)
            if self.args.stage_build_nuitka:
                os.system("./" + build_name + '.sh') #, shell=True
        pass


    def mycopy(self, src, dst):
        '''
            Адаптивная процедура копирования в подкаталоги окружения — симлинки релятивизируются 
            и остаются симлинками.
        '''
        if os.path.exists(dst):
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
        
        if f == "": 
            return False
    
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
    
        if not parts:
            return False
    
        if (parts[0] not in ["lib", "lib64"]) and (parts != ['bin', 'bash']):
            return False
        parts.pop(0)
    
        if len(parts) > 0 and (parts[0] == "locale" or parts[0] == ".build-id"):
            return False
    
        # что не отфильтровалось — берем.
        return True


    def dependencies(self, package_list):
        '''
        Генерируем список RPM-зависимостей для заданного списка пакетов.
        '''
        options_ = [
            # Фильтруем пакеты по 64битной архитектуре (ну или 32битной, если будем собирать там.),
            # хотя сейчас почти везде хардкодинг на 64битную архитектуру.
            '--archlist=noarch,{machine}'.format(machine=os.uname().machine),
                    '--cacheonly', 
                    '--installed',
                    '--resolve',
                    '--requires',
                    '--recursive'
            ]
    
        if 1:
            # res = subprocess.check_output(['repoquery'] + options_  + ['--tree', '--whatrequires'] + package_list,  universal_newlines=True)
            res = subprocess.check_output(['repoquery'] + options_  + package_list,  universal_newlines=True)
            # res = subprocess.check_output(['repoquery'] + options_  + ['--output', 'dot-tree'] + package_list,  universal_newlines=True)
            with open(os.path.join(self.start_dir, 'deps.txt'), 'w', encoding='utf-8') as lf:
                lf.write('\n -'.join(package_list))
                lf.write('\n----------------\n')
                lf.write(res)
    
        output  = subprocess.check_output(['repoquery'] + options_ + package_list,  universal_newlines=True).splitlines()
        output = [x for x in output if self.ps.is_package_needed(x)]
        packages_ = output + package_list
        with open(os.path.join(self.start_dir, 'selected-packages.txt'), 'w', encoding='utf-8') as lf:
            lf.write('\n- '.join(packages_))

        with open(os.path.join(self.start_dir, 'selected-packages.rst'), 'w', encoding='utf-8') as lf:
            lf.write("""
+-------------------------------+-----------------+
| Development tools             | Version         |
+===============================+=================+
| Visual Studio Code            |  1.47.0         |
+-------------------------------+-----------------+""")            
            packages_set_ = set()
            for package_ in packages_:
                purepackage = package_.split('.', 1)[0]
                if len(purepackage) < len(package_):
                    purepackage  = purepackage.rsplit('-', 1)[0]
                packages_set_.add(purepackage)

            for package_ in sorted(packages_set_):
                res_ = list(self.installed_packages.filter(name=package_))
                if len(res_)==0:
                    continue
                name_ = res_[0].name
                version_ = res_[0].version
                wtf = 1
                lf.write("""
|%(name_)-31s|%(version_)17s|
+-------------------------------+-----------------+""" % vars())            
                pass


        return packages_ 

    def generate_file_list(self, packages):
        '''
        Для заданного списка RPM-файлов, возвращаем список файлов в этих пакетах, которые нужны нам.
        '''
    
        exclusions = []
        for package_ in packages:
            exclusions += subprocess.check_output(['rpm', '-qd', package_], universal_newlines=True).splitlines()
    
        # we don't want to use --list the first time: For one, we want to be able to filter
        # out some packages with files
        # we don't want to copy
        # Second, repoquery --list do not include the actual package files when used with --resolve and --recursive (only its dependencies').
        # So we need a separate step in which all packages are added together.
    
        for package_ in packages:
            files = subprocess.check_output(['repoquery',
                                         '--installed',
                                         '--cacheonly',
                                         '--list' ] + [package_], universal_newlines=True).splitlines()
            for file in files:
                if 'i686' in file:
                    assert(True)
            
        
        candidates = subprocess.check_output(['repoquery',
                                     '--installed',
                                     '--cacheonly',
                                     '--list' ] + packages, universal_newlines=True).splitlines()
    
        # candidates = subprocess.check_output(executables, universal_newlines=True).splitlines()
    
        pass
        res_ = [x for x in set(candidates) - set(exclusions) if self.should_copy(x)]
        return res_

    def add(self, what, to_=None, recursive=True):
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
                if not os.path.exists(os.path.join(self.root_dir, to_)):
                    shutil.copytree(what, os.path.join(self.root_dir, to_), symlinks=True, copy_function=self.mycopy)
                    #, exist_ok=True)
            else:
                self.mycopy(what, os.path.join(self.root_dir, to_))
            pass
        except Exception as ex_:
            print("Troubles on adding", to_ , "<-", what)
            raise ex_
            pass


    def process_binary(self, binpath):
        '''
        Фиксим бинарник.
        '''
        for wtf_ in ['libldap']:
            if wtf_ in binpath:
                return
        
        m = magic.detect_from_filename(binpath)
        if m.mime_type in ['inode/symlink', 'text/plain']:
            return
        
        # if m.mime_type not in ['application/x-sharedlib', 'application/x-executable']
        if not 'application' in m.mime_type:
            return
    
        pyname = os.path.basename(binpath)
        try:
            patched_binary = fix_binary(binpath, '$ORIGIN/../lib64/')
        except Exception as ex_:
            print("Mime type ", m.mime_type)
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

    def checkout_sources(self):
        '''
            Just checking out sources.
            This stage should be done when we have authorization to check them out.
        '''
        if not self.pp:
            return

        root_dir = self.root_dir
        args = self.args

        for td_, local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in self.pp.casket]:
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)
            git2dir(git_url, git_branch, path_to_dir)        
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

    def install_localpythons(self):
        if not self.pp:
            return

        root_dir = self.root_dir
        args = self.args
        t.tic()

        for td_, local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in self.pp.casket]:
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)

    
            os.chdir(setup_path)
            make_setup_if_not_exists()
            release_mod = ''
            # if self.args.release:
            #     release_mod = ' --exclude-source-files '
            scmd = "%(root_dir)s/ebin/python3 setup.py install --single-version-externally-managed  %(release_mod)s --root /   " % vars()
            if local_:
                scmd = "sudo python3 setup.py install --single-version-externally-managed  %(release_mod)s --root /   " % vars()
            print(scmd)
            os.system(scmd)
        print("Install localpythons takes")
        t.toc()
        pass



    def install_packages(self):
        root_dir = self.root_dir
        args = self.args
        packages = []
        
        import dnf
        base = dnf.Base()
        # base.read_all_repos()
        base.fill_sack()
        q_ = base.sack.query()
        self.installed_packages = q_.installed()
    
        t.tic() 
        scmd = "sudo yum-config-manager --enable remi"
        os.system(scmd)
        for package in self.need_packages + self.ps.build + self.ps.casket:
            # потом написать идемпотентность, проверки на установленность, пока пусть долго, по одному ставит
            package_name = None
            if isinstance(package, str):
                package_name = package
    
                ok_ = list(self.installed_packages.filter(name=package_name))
                if not ok_:
                    scmd = 'sudo dnf install -y "%(package_name)s" ' % vars()
                    os.system(scmd)
                else:
                    print('Package ' + package_name + ' already installed!')    
                    pass
            else:
                package_name = package.name
                package_url = package.url
                ok_ = list(self.installed_packages.filter(name=package_name))
                if not ok_:
                    scmd = 'sudo dnf install -y "%(package_url)s" ' % vars()
                    os.system(scmd)
                pass
            if package_name:
                packages.append(package_name)
        print("Install packages takes")
        t.toc()
        pass

    def build_wheels(self):
        os.chdir(self.curdir)
        bindir_ = os.path.abspath(self.in_bin)
        lines = []
        lines.append(R"rm -rf %(in_bin)s/ourwheel/*.*" % vars(self))
        for td_, local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in self.pp.casket]:
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)
            scmd = "pushd %s" % (path_to_dir)
            lines.append(scmd)
            scmd = "python3 setup.py bdist_wheel -d %(in_bin)s/ourwheel " % vars(self)
            lines.append(scmd)
            lines.append('popd')
            pass
        batfile = "04-build-wheels"
        self.lines2sh(batfile, lines)
        # os.system(batfile+'.sh')
        # os.chdir(self.curdir)
        # os.chdir(self.output_dir)
        pass

    def download(self):
        os.chdir(self.curdir)
        os.chdir(self.out_dir)

        if not self.pp:
            return

        root_dir = self.root_dir
        args = self.args

        lines = []

        for td_ in self.pp.pip:
            scmd = "python3 -m pip download %s --dest %s/extwheel " % (td_, self.in_bin)
            lines.append(scmd)                

        for td_, local_ in [ (x, True) for x in self.pp.build ] + [(x, False) for x in self.pp.casket]:
            git_url, git_branch, path_to_dir, setup_path = self.explode_pp_node(td_)
            os.chdir(setup_path)
            scmd = "python3 -m pip download %s --dest %s/extwheel " % (
                setup_path, self.in_bin)
            lines.append(scmd)                
            pass

        cmd_name = "02-download-wheels"
        self.lines2sh(cmd_name, lines)

        if self.args.stage_download:
            os.chdir(self.curdir)
            os.system(cmd_name + ".sh", shell=True)

        pass    


    def process(self):
        '''
        Основная процедура генерации переносимого питон окружения.
        '''
        
        args = self.args
        spec = self.spec
        root_dir = self.root_dir 
        
        def install_templates(root_dir, args):
            from jinja2 import Environment, FileSystemLoader, Template
            t.tic()
    
            for td_ in spec.templates_dirs:
                git_url = None
                subdir = ""
                if type(td_) == type(""):
                    git_url = td_
                else:
                    git_url = td_.url
                    subdir = td_.subdir
    
                path_to_dir = os.path.join(expandpath(git_url), subdir)
                print("*"*20 + path_to_dir)
                if not os.path.exists(path_to_dir):
                    import tempfile
                    tmpdir_ = tempfile.mkdtemp('pypg')
                    path_to_dir = os.path.join(tmpdir_, 'adir')
        
                    os.chdir(tmpdir_)
                    #todo: выяснить, почему --git-dir не работает.
                    scmd = 'git --git-dir=/dev/null clone --depth=1 %(git_url)s %(path_to_dir)s ' % vars()
                    os.system(scmd)
                    path_to_dir = os.path.join(tmpdir_, 'adir', subdir)
                
                file_loader = FileSystemLoader(path_to_dir)
                env = Environment(loader=file_loader)
                env.trim_blocks = True
                env.lstrip_blocks = True
                env.rstrip_blocks = True            
                
                print(path_to_dir)
                os.chdir(path_to_dir)
                for dirpath, dirnames, filenames in os.walk('.'):
                    for dir_ in dirnames:
                        out_dir = os.path.join(root_dir, dirpath, dir_)
                        print(out_dir)
                        mkdir_p(out_dir)
                        # if not os.path.exists(out_dir):
                        #     os.mkdir(out_dir)
                        
                    for filename in filenames:
                        fname_  = os.path.join(dirpath, filename)
                        if '_add_newdocs.py' in fname_:
                            wtf=1
                        out_fname_ = os.path.join(root_dir, dirpath, filename)
                        out_fname_ = Template(out_fname_ ).render(self.tvars)                    
                        
                        plain = False
                        try:
                            m = magic.detect_from_filename(fname_)
                            if 'text' in m.mime_type:
                                plain = True
                        except Exception:
                            pass
                        if plain:
                            template = env.get_template(fname_)
                            output = template.render(self.tvars)                    
                            with open(out_fname_, 'w', encoding='utf-8') as lf_:
                                lf_.write(output)
                        else:
                            shutil.copy2(fname_, out_fname_)
                        shutil.copymode(fname_, out_fname_)                        
            
            print("Install templates takes")
            t.toc()
            pass
    
    
        if self.args.stage_checkout:
            self.checkout_sources()
        #install_templates(root_dir, args)

        self.download()
        self.build_wheels()
        return

        if self.args.stage_setupsystem:
            self.install_packages()

        # if self.args.stage_build_nuitka:
        self.install_localpythons()
        self.build_nuitkas()
            # return



    
        file_list = self.generate_file_list(self.dependencies(self.ps.casket))

        if os.path.exists(root_dir + ".old"):
            shutil.rmtree(root_dir + ".old", ignore_errors=True)
        if os.path.exists(root_dir + ".old"):
            os.system("rm -rf " + root_dir + ".old")
        if os.path.exists(root_dir):
            shutil.move(root_dir, root_dir + ".old")

        mkdir_p(root_dir)    
        # self.add("/usr/sbin/sash", 'ebin/sash')
        
        def copy_file_to_environment(f):
            if not self.should_copy(f):
                return
            
            if self.br.is_need_patch(f):  
                self.process_binary(f)
                self.add(f)
            elif self.br.is_just_copy(f):
                self.add(f)
            else:
                libfile = f
                # python tends to install in both /usr/lib and /usr/lib64, which doesn't mean it is
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
                        m = magic.detect_from_filename(f)
                    except Exception as ex_:
                        print("Cannot detect Magic for ", f)    
                        raise ex_
                    if m and (m.mime_type.startswith('application/x-sharedlib') or m.mime_type.startswith('application/x-pie-executable')):
                        self.fix_sharedlib(f, libfile)
                    else:
                        # in case this is a directory that is listed, we don't want to include everything that is in that directory
                        # for instance, the python3 package will own site-packages, but other packages that we are not packaging could have
                        # filled it with stuff.
                        self.add(f, libfile, recursive=False)
                        # shutil.copy2(f, os.path.join(root_dir, libfile))
                        # add(f, arcname=libfile, recursive=False)  
    
    
        for f in file_list:
            copy_file_to_environment(f)

        if self.fs:    
            for folder_ in self.fs.folders:
                for dirpath, dirnames, filenames in os.walk(folder_):
                    for filename in filenames:
                        f = os.path.join(dirpath, filename)
                        if self.br.is_need_patch(f):  
                            self.process_binary(f)
                            continue
                        libfile = os.path.join(self.root_dir, f.replace(folder_, 'pbin'))
                        self.add(f, libfile, recursive=False)
                pass

    
        install_templates(root_dir, args)
        self.install_localpythons()
        install_templates(root_dir, args)
        self.install_localpythons()
    
        os.chdir(root_dir)
        scmd = "%(root_dir)s/ebin/python3 -m compileall -b . " % vars()
        print(scmd)
        os.system(scmd)
    
        if self.args.release:
            # Remove source files.
            scmd = "shopt -s globstar; rm  **/*.py; rm  -r **/__pycache__"
            print(scmd)
            os.system(scmd)
            pass
        
        # size_ = sum(file.stat().st_size for file in pathlib.Path(self.root_dir).rglob('*'))
        size_  = folder_size(self.root_dir, follow_symlinks=False)
        print("Size ", size_/1024/1024, 'Mb')
    
        pass

