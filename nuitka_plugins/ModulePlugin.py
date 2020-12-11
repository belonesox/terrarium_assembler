#     Copyright 2020, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" 
Standard plug-in to find data files.
"""

import os

from nuitka import Options
from nuitka.__past__ import basestring  # pylint: disable=I0021,redefined-builtin
from nuitka.containers.oset import OrderedSet
from nuitka.plugins.PluginBase import NuitkaPluginBase
from nuitka.utils.FileOperations import getFileList, listDir


# def _createEmptyDirText(filename):
#     # We create the same content all the time, pylint: disable=unused-argument
#     return ""


# def _createEmptyDirNone(filename):
#     # Returning None means no file creation should happen, pylint: disable=unused-argument
#     return None


# def remove_suffix(string, suffix):
#     """Remove 'suffix' from 'string'."""
#     # Special case: if suffix is empty, string[:0] returns ''. So, test
#     # for a non-empty suffix.
#     if suffix and string.endswith(suffix):
#         return string[: -len(suffix)]
#     else:
#         return string


# def get_package_paths(package):
#     """Return the path to the package.

#     Args:
#         package: (str) package name
#     Returns:
#         tuple: (prefix, prefix/package)
#     """
#     import pkgutil

#     loader = pkgutil.find_loader(package)
#     if not loader:
#         return "", ""

#     file_attr = loader.get_filename(package)
#     if not file_attr:
#         return "", ""

#     pkg_dir = os.path.dirname(file_attr)
#     pkg_base = remove_suffix(pkg_dir, package.replace(".", os.sep))

#     return pkg_base, pkg_dir


# def _getPackageFiles(module, packages, folders_only):
#     """Yield all (!) filenames in given package(s).

#     Notes:
#         This should be required in rare occasions only. The one example I know
#         is 'dns' when used by package 'eventlet'. Eventlet imports dns modules
#         only to replace them with 'green' (i.e. non-blocking) counterparts.
#     Args:
#         module: module object
#         packages: package name(s) - str or tuple
#         folders_only: (bool) indicate, whether just the folder structure should
#             be generated. In that case, an empty file named DUMMY will be
#             placed in each of these folders.
#     Yields:
#         Tuples of paths (source, dest), if folders_only is False,
#         else tuples (_createEmptyDirNone, dest).
#     """

#     # TODO: Maybe use isinstance(basestring) for this
#     if not hasattr(packages, "__getitem__"):  # so should be a string type
#         packages = (packages,)

#     file_list = []
#     item_set = OrderedSet()

#     file_dirs = []

#     for package in packages:
#         pkg_base, pkg_dir = get_package_paths(package)  # read package folders
#         if pkg_dir:
#             filename_start = len(pkg_base)  # position of package name in dir
#             # read out the filenames
#             pkg_files = getFileList(
#                 pkg_dir, ignore_dirs=("__pycache__",), ignore_suffixes=(".pyc",)
#             )
#             file_dirs.append(pkg_dir)
#             for f in pkg_files:
#                 file_list.append((filename_start, f))  # append to file list

#     if not file_list:  #  safeguard for unexpected cases
#         msg = "No files or folders found for '%s' in packages(s) '%r' (%r)." % (
#             module.getFullName(),
#             packages,
#             file_dirs,
#         )
#         NuitkaPluginDataFileCollector.warning(msg)

#     for filename_start, f in file_list:  # re-read the collected filenames
#         target = f[filename_start:]  # make part of name
#         if folders_only is False:  # normal case: indeed copy the files
#             item_set.add((f, target))
#         else:  # just create the empty folder structure
#             item_set.add((_createEmptyDirNone, target))

#     for f in item_set:
#         yield f


# def _getSubDirectoryFiles(module, subdirs, folders_only):
#     """Yield filenames in given subdirs of the module.

#     Notes:
#         All filenames in folders below one of the subdirs are recursively
#         retrieved and returned shortened to begin with the string of subdir.
#     Args:
#         module: module object
#         subdirs: sub folder name(s) - str or None or tuple
#         folders_only: (bool) indicate, whether just the folder structure should
#             be generated. In that case, an empty file named DUMMY will be
#             placed in each of these folders.
#     Yields:
#         Tuples of paths (source, dest) are yielded if folders_only is False,
#         else tuples (_createEmptyDirNone, dest) are yielded.
#     """
#     module_folder = module.getCompileTimeDirectory()
#     elements = module.getFullName().split(".")
#     filename_start = module_folder.find(elements[0])
#     file_list = []
#     item_set = OrderedSet()

#     if subdirs is None:
#         data_dirs = [module_folder]
#     elif isinstance(subdirs, basestring):
#         data_dirs = [os.path.join(module_folder, subdirs)]
#     else:
#         data_dirs = [os.path.join(module_folder, subdir) for subdir in subdirs]

#     # Gather the full file list, probably makes no sense to include bytecode files
#     file_list = sum(
#         (
#             getFileList(
#                 data_dir, ignore_dirs=("__pycache__",), ignore_suffixes=(".pyc",)
#             )
#             for data_dir in data_dirs
#         ),
#         [],
#     )

#     if not file_list:
#         msg = "No files or folders found for '%s' in subfolder(s) %r (%r)." % (
#             module.getFullName(),
#             subdirs,
#             data_dirs,
#         )
#         NuitkaPluginDataFileCollector.warning(msg)

#     for f in file_list:
#         target = f[filename_start:]
#         if folders_only is False:
#             item_set.add((f, target))
#         else:
#             item_set.add((_createEmptyDirNone, target))

#     for f in item_set:
#         yield f


# class NuitkaPluginDataFileCollector(NuitkaPluginBase):
#     plugin_name = "data-files"

#     known_data_files = {
#         # Key is the package name to trigger it
#         # Value is a tuple of 2 element tuples, thus trailing commas, where
#         # the target path can be specified (None is just default, i.e. the
#         # package directory) and the filename relative to the source package
#         # directory
#         "botocore": ((None, "cacert.pem"),),
#         "site": ((None, "orig-prefix.txt"),),
#         "nose.core": ((None, "usage.txt"),),
#         "scrapy": ((None, "VERSION"),),
#         "dask": (("", "dask.yaml"),),
#         "cairocffi": ((None, "VERSION"),),
#         "cairosvg": ((None, "VERSION"),),
#         "weasyprint": ((None, "VERSION"),),
#         "tinycss2": ((None, "VERSION"),),
#         "certifi": ((None, "cacert.pem"),),
#         "importlib_resources": ((None, "version.txt"),),
#         "moto": (
#             (None, "ec2/resources/instance_types.json"),
#             (None, "ec2/resources/amis.json"),
#         ),
#         "skimage": (
#             (None, "io/_plugins/fits_plugin.ini"),
#             (None, "io/_plugins/gdal_plugin.ini"),
#             (None, "io/_plugins/gtk_plugin.ini"),
#             (None, "io/_plugins/imageio_plugin.ini"),
#             (None, "io/_plugins/imread_plugin.ini"),
#             (None, "io/_plugins/matplotlib_plugin.ini"),
#             (None, "io/_plugins/pil_plugin.ini"),
#             (None, "io/_plugins/qt_plugin.ini"),
#             (None, "io/_plugins/simpleitk_plugin.ini"),
#             (None, "io/_plugins/tifffile_plugin.ini"),
#         ),
#         "skimage.feature._orb_descriptor_positions": (
#             ("skimage/feature", "orb_descriptor_positions.txt"),
#         ),
#     }

#     # data files to be copied are contained in subfolders named as the second item
#     # the 3rd item indicates whether to recreate toe folder structure only (True),
#     # or indeed also copy the files.
#     known_data_folders = {
#         "botocore": (_getSubDirectoryFiles, "data", False),
#         "boto3": (_getSubDirectoryFiles, "data", False),
#         "sklearn.datasets": (_getSubDirectoryFiles, ("data", "descr"), False),
#         "osgeo": (_getSubDirectoryFiles, "data", False),
#         "pyphen": (_getSubDirectoryFiles, "dictionaries", False),
#         "pendulum": (_getSubDirectoryFiles, "locales", True),  # folder structure only
#         "pytz": (_getSubDirectoryFiles, "zoneinfo", False),
#         "pytzdata": (_getSubDirectoryFiles, "zoneinfo", False),
#         "pywt": (_getSubDirectoryFiles, "data", False),
#         "skimage": (_getSubDirectoryFiles, "data", False),
#         "weasyprint": (_getSubDirectoryFiles, "css", False),
#         "xarray": (_getSubDirectoryFiles, "static", False),
#         "eventlet": (_getPackageFiles, ("dns",), False),  # copy other package source
#         "gooey": (_getSubDirectoryFiles, ("languages", "images"), False),
#     }

#     generated_data_files = {
#         "Cryptodome.Util._raw_api": (
#             ("Cryptodome/Util", ".keep_dir.txt", _createEmptyDirText),
#         ),
#         "Crypto.Util._raw_api": (
#             ("Crypto/Util", ".keep_dir.txt", _createEmptyDirText),
#         ),
#     }

#     @classmethod
#     def isRelevant(cls):
#         return Options.isStandaloneMode()

#     @staticmethod
#     def isAlwaysEnabled():
#         return True

#     def considerDataFiles(self, module):
#         module_name = module.getFullName()
#         module_folder = module.getCompileTimeDirectory()

#         if module_name in self.known_data_files:
#             for target_dir, filename in self.known_data_files[module_name]:
#                 source_path = os.path.join(module_folder, filename)

#                 if os.path.isfile(source_path):
#                     if target_dir is None:
#                         target_dir = module_name.replace(".", os.path.sep)

#                     yield (
#                         source_path,
#                         os.path.normpath(os.path.join(target_dir, filename)),
#                     )

#         if module_name in self.known_data_folders:
#             func, subdir, folders_only = self.known_data_folders[module_name]
#             for item in func(module, subdir, folders_only):
#                 yield item

#         if module_name in self.generated_data_files:
#             for target_dir, filename, func in self.generated_data_files[module_name]:
#                 if target_dir is None:
#                     target_dir = module_name.replace(".", os.path.sep)

#                 yield (func, os.path.normpath(os.path.join(target_dir, filename)))

#         if module_name == "lib2to3.pgen2":
#             for source_path, filename in listDir(os.path.join(module_folder, "..")):
#                 if not filename.endswith(".pickle"):
#                     continue

#                 yield (source_path, os.path.normpath(os.path.join("lib2to3", filename)))




class ModulePlugin(NuitkaPluginBase):
    """
        This class represents the main logic of the plugin.
    """

    plugin_name = "dm-module"  # Nuitka knows us by this name
    plugin_desc = "Required for compiling fully incapsulated modules with a lot of datafiles"

    def __init__(self):
        self.matplotlib = include_matplotlib
        self.scipy = include_scipy

        self.enabled_plugins = None  # list of active standard plugins
        self.numpy_copied = False  # indicator: numpy files copied
        self.scipy_copied = True  # indicator: scipy files copied
        if self.scipy:
            self.scipy_copied = False

        self.mpl_data_copied = True  # indicator: matplotlib data copied
        if self.matplotlib:
            self.mpl_data_copied = False

    @classmethod
    def isRelevant(cls):
        """Check whether plugin might be required.

        Returns:
            True if this is a standalone compilation.
        """
        return True

#     @classmethod
#     def addPluginCommandLineOptions(cls, group):
#         group.add_option(
#             "--include-shlibs",
#             action="store_false",
#             dest="include_shlibs",
#             default=True,
#             help="""\
# Should inclide shared libs for module.                
# Default is %default.""",
#         )
#         pass

    def onModuleEncounter(self, module_filename, module_name, module_kind):
        if module_kind == "shlib":
             return True, "Shared library for inclusion."
        return None             

#     def considerExtraDlls(self, dist_dir, module):
#         """Copy extra shared libraries or data for this installation.

#         Args:
#             dist_dir: the name of the program's dist folder
#             module: module object
#         Returns:
#             empty tuple
#         """
#         full_name = module.getFullName()
#         elements = full_name.split(".")

#         if not self.numpy_copied and full_name == "numpy":
#             self.numpy_copied = True
#             binaries = getNumpyCoreBinaries(module)

#             for f in binaries:
#                 bin_file, idx = f  # (filename, pos. prefix + 1)
#                 back_end = bin_file[idx:]
#                 tar_file = os.path.join(dist_dir, back_end)
#                 makePath(  # create any missing intermediate folders
#                     os.path.dirname(tar_file)
#                 )
#                 shutil.copyfile(bin_file, tar_file)

#             bin_total = len(binaries)  # anything there at all?
#             if bin_total > 0:
#                 msg = "Copied %i %s from 'numpy' installation." % (
#                     bin_total,
#                     "file" if bin_total < 2 else "files",
#                 )
#                 self.info(msg)

#         if os.name == "nt" and not self.scipy_copied and full_name == "scipy":
#             # TODO: We are not getting called twice, are we?
#             assert not self.scipy_copied
#             self.scipy_copied = True

#             bin_total = 0
#             for entry_point in self._getScipyCoreBinaries(
#                 scipy_dir=module.getCompileTimeDirectory()
#             ):
#                 yield entry_point
#                 bin_total += 1

#             if bin_total > 0:
#                 msg = "Copied %i %s from 'scipy' installation." % (
#                     bin_total,
#                     "file" if bin_total < 2 else "files",
#                 )
#                 self.info(msg)

#         if not self.mpl_data_copied and "matplotlib" in elements:
#             self.mpl_data_copied = True
#             copyMplDataFiles(module, dist_dir)
#             self.info("Copied 'matplotlib/mpl-data'.")

#     @staticmethod
#     def _getScipyCoreBinaries(scipy_dir):
#         """Return binaries from the extra-dlls folder (Windows only)."""

#         for dll_dir_name in ("extra_dll", ".libs"):
#             dll_dir_path = os.path.join(scipy_dir, dll_dir_name)

#             if os.path.isdir(dll_dir_path):
#                 for source_path, source_filename in listDir(dll_dir_path):
#                     if source_filename.lower().endswith(".dll"):
#                         yield makeDllEntryPoint(
#                             source_path=source_path,
#                             dest_path=os.path.join(
#                                 "scipy", dll_dir_name, source_filename
#                             ),
#                             package_name="scipy",
#                         )

#     def onModuleEncounter(self, module_filename, module_name, module_kind):
#         # pylint: disable=too-many-branches,too-many-return-statements
#         if not self.scipy and module_name.hasOneOfNamespaces(
#             "scipy", "sklearn", "skimage"
#         ):
#             return False, "Omit unneeded components"

#         if not self.matplotlib and module_name.hasOneOfNamespaces(
#             "matplotlib", "skimage"
#         ):
#             return False, "Omit unneeded components"

#         if module_name == "scipy.sparse.csgraph._validation":
#             return True, "Replicate implicit import"

#         if self.matplotlib and module_name.hasNamespace("mpl_toolkits"):
#             return True, "Needed by matplotlib"

#         if module_name in ("cv2", "cv2.cv2", "cv2.data"):
#             return True, "Needed for OpenCV"

#         sklearn_mods = [
#             "sklearn.utils.sparsetools._graph_validation",
#             "sklearn.utils.sparsetools._graph_tools",
#             "sklearn.utils.lgamma",
#             "sklearn.utils.weight_vector",
#             "sklearn.utils._unittest_backport",
#             "sklearn.externals.joblib.externals.cloudpickle.dumps",
#             "sklearn.externals.joblib.externals.loky.backend.managers",
#         ]

#         if isWin32Windows():
#             sklearn_mods.extend(
#                 [
#                     "sklearn.externals.joblib.externals.loky.backend.synchronize",
#                     "sklearn.externals.joblib.externals.loky.backend._win_wait",
#                     "sklearn.externals.joblib.externals.loky.backend._win_reduction",
#                     "sklearn.externals.joblib.externals.loky.backend.popen_loky_win32",
#                 ]
#             )
#         else:
#             sklearn_mods.extend(
#                 [
#                     "sklearn.externals.joblib.externals.loky.backend.synchronize",
#                     "sklearn.externals.joblib.externals.loky.backend.compat_posix",
#                     "sklearn.externals.joblib.externals.loky.backend._posix_reduction",
#                     "sklearn.externals.joblib.externals.loky.backend.popen_loky_posix",
#                 ]
#             )

#         if self.scipy and module_name in sklearn_mods:
#             return True, "Needed by sklearn"

#         # some special handling for matplotlib:
#         # depending on whether 'tk-inter' resp. 'qt-plugins' are enabled,
#         # matplotlib backends are included.
#         if self.matplotlib:
#             if hasActivePlugin("tk-inter"):
#                 if module_name in (
#                     "matplotlib.backends.backend_tk",
#                     "matplotlib.backends.backend_tkagg",
#                     "matplotlib.backend.tkagg",
#                 ):
#                     return True, "Needed for tkinter backend"

#             if hasActivePlugin("qt-plugins"):
#                 if module_name.startswith("matplotlib.backends.backend_qt"):
#                     return True, "Needed for Qt backend"

#             if module_name == "matplotlib.backends.backend_agg":
#                 return True, "Needed as standard backend"

#     def createPreModuleLoadCode(self, module):
#         """Method called when a module is being imported.

#         Notes:
#             If full name equals "matplotlib" we insert code to set the
#             environment variable that Debian versions of matplotlib
#             use.

#         Args:
#             module: the module object
#         Returns:
#             Code to insert and descriptive text (tuple), or (None, None).
#         """

#         if not self.matplotlib or module.getFullName() != "matplotlib":
#             return None, None  # not for us

#         code = """\
# import os
# os.environ["MATPLOTLIBDATA"] = os.path.join(__nuitka_binary_dir, "mpl-data")
# """
#         return (
#             code,
#             "Setting 'MATPLOTLIBDATA' environment variable for matplotlib to find package data.",
#         )


# class NumpyPluginDetector(NuitkaPluginBase):
#     """Only used if plugin is NOT activated.

#     Notes:
#         We are given the chance to issue a warning if we think we may be required.
#     """

#     detector_for = NumpyPlugin

#     @classmethod
#     def isRelevant(cls):
#         """Check whether plugin might be required.

#         Returns:
#             True if this is a standalone compilation.
#         """
#         return Options.isStandaloneMode()

#     def onModuleDiscovered(self, module):
#         """This method checks whether numpy is required.

#         Notes:
#             For this we check whether its first name part is numpy relevant.
#         Args:
#             module: the module object
#         Returns:
#             None
#         """
#         module_name = module.getFullName()
#         if module_name.hasOneOfNamespaces(
#             "numpy", "scipy", "skimage", "pandas", "matplotlib", "sklearn"
#         ):
#             self.warnUnusedPlugin(
#                 "Numpy support for at least '%s'."
#                 % module_name.getTopLevelPackageName()
#             )
