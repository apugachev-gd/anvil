# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright (C) 2012 Yahoo! Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# R0902: Too many instance attributes
# R0921: Abstract class not referenced
#pylint: disable=R0902,R0921

import pkg_resources

from anvil import colorizer
from anvil import exceptions as exc
from anvil import log as logging
from anvil import shell as sh
from anvil import utils

LOG = logging.getLogger(__name__)


class InstallHelper(object):
    """Run pre and post install for a single package.
    """
    def __init__(self, distro):
        self.distro = distro

    def pre_install(self, pkg, params=None):
        cmds = pkg.get('pre-install')
        if cmds:
            LOG.info("Running pre-install commands for package %s.", colorizer.quote(pkg['name']))
            utils.execute_template(*cmds, params=params)

    def post_install(self, pkg, params=None):
        cmds = pkg.get('post-install')
        if cmds:
            LOG.info("Running post-install commands for package %s.", colorizer.quote(pkg['name']))
            utils.execute_template(*cmds, params=params)


OPENSTACK_PACKAGES = set([
    "cinder",
    "glance",
    "horizon",
    "keystone",
    "nova",
    "oslo.config",
    "quantum",
    "swift",
    "python-cinderclient",
    "python-glanceclient",
    "python-keystoneclient",
    "python-novaclient",
    "python-quantumclient",
    "python-swiftclient",
])


class DependencyHandler(object):
    """Basic class for handler of OpenStack dependencies.
    """
    MAX_PIP_DOWNLOAD_ATTEMPTS = 4
    multipip_executable = sh.which("multipip", ["tools/"])

    def __init__(self, distro, root_dir, instances):
        self.distro = distro
        self.root_dir = root_dir
        self.instances = instances

        self.deps_dir = sh.joinpths(self.root_dir, "deps")
        self.download_dir = sh.joinpths(self.deps_dir, "download")
        self.log_dir = sh.joinpths(self.deps_dir, "output")
        self.gathered_requires_filename = sh.joinpths(
            self.deps_dir, "pip-requires")
        self.forced_requires_filename = sh.joinpths(
            self.deps_dir, "forced-requires")
        self.pip_executable = str(self.distro.get_command_config('pip'))
        self.pips_to_install = []
        self.forced_packages = []
        # these packages conflict with our deps and must be removed
        self.nopackages = []
        self.package_dirs = self._get_package_dirs(instances)
        self.python_names = self._get_python_names(self.package_dirs)

    @staticmethod
    def _get_package_dirs(instances):
        package_dirs = []
        for inst in instances:
            app_dir = inst.get_option("app_dir")
            if sh.isfile(sh.joinpths(app_dir, "setup.py")):
                package_dirs.append(app_dir)
        return package_dirs

    @staticmethod
    def _get_python_names(package_dirs):
        python_names = []
        for pkg_dir in package_dirs:
            cmdline = ["python", "setup.py", "--name"]
            python_names.append(sh.execute(cmdline, cwd=pkg_dir)[0].
                                splitlines()[-1].strip())
        return python_names

    def package(self):
        requires_files = []
        extra_pips = []
        for inst in self.instances:
            try:
                requires_files.extend(inst.requires_files)
            except AttributeError:
                pass
            for pkg in inst.get_option("pips") or []:
                extra_pips.append(
                    "%s%s" % (pkg["name"], pkg.get("version", "")))
        requires_files = filter(sh.isfile, requires_files)
        self.gather_pips_to_install(requires_files, extra_pips)
        self.clean_pip_requires(requires_files)

    def install(self):
        self.nopackages = []
        for inst in self.instances:
            for pkg in inst.get_option("nopackages") or []:
                self.nopackages.append(pkg["name"])

    def uninstall(self):
        pass

    def clean_pip_requires(self, requires_files):
        # Fixup incompatible dependencies
        if not (requires_files and self.forced_packages):
            return
        utils.log_iterable(
            sorted(requires_files),
            logger=LOG,
            header="Adjusting %s pip 'requires' files" %
            (len(requires_files)))
        forced_by_key = dict((pkg.key, pkg) for pkg in self.forced_packages)
        for fn in requires_files:
            old_lines = sh.load_file(fn).splitlines()
            new_lines = []
            for line in old_lines:
                try:
                    req = pkg_resources.Requirement.parse(line)
                    new_lines.append(str(forced_by_key[req.key]))
                except:
                    # we don't force the package or it has a bad format
                    new_lines.append(line)
            contents = "# Cleaned on %s\n\n%s\n" % (
                utils.iso8601(), "\n".join(new_lines))
            sh.write_file_and_backup(fn, contents)

    def gather_pips_to_install(self, requires_files, extra_pips=None):
        """Analyze requires_files and extra_pips.

        Updates `self.forced_packages` and `self.pips_to_install`.
        Writes requirements to `self.gathered_requires_filename`.
        """
        extra_pips = extra_pips or []
        cmdline = [
            self.multipip_executable,
            "--skip-requirements-regex",
            "python.*client",
            "--pip",
            self.pip_executable
        ]
        cmdline = cmdline + extra_pips + ["-r"] + requires_files

        output = sh.execute(cmdline, check_exit_code=False)
        conflict_descr = output[1].strip()
        forced_keys = set()
        if conflict_descr:
            for line in conflict_descr.splitlines():
                LOG.warning(line)
                if line.endswith(": incompatible requirements"):
                    forced_keys.add(line.split(":", 1)[0].lower())
        self.pips_to_install = [
            pkg
            for pkg in utils.splitlines_not_empty(output[0])
            if pkg.lower() not in OPENSTACK_PACKAGES]
        sh.write_file(self.gathered_requires_filename,
                      "\n".join(self.pips_to_install))
        if not self.pips_to_install:
            LOG.error("No dependencies for OpenStack found."
                      "Something went wrong. Please check:")
            LOG.error("'%s'" % "' '".join(cmdline))
            raise RuntimeError("No dependencies for OpenStack found")

        utils.log_iterable(sorted(self.pips_to_install),
                           logger=LOG,
                           header="Full known python dependency list")
        self.forced_packages = []
        for pip in self.pips_to_install:
            req = pkg_resources.Requirement.parse(pip)
            if req.key in forced_keys:
                self.forced_packages.append(req)
        sh.write_file(self.forced_requires_filename,
                      "\n".join(str(req) for req in self.forced_packages))

    def filter_download_requires(self):
        if not self.python_names:
            return self.pips_to_install
        cmdline = [
            self.multipip_executable,
            "--pip", self.pip_executable,
        ] + self.pips_to_install + [
            "--ignore-packages",
        ] + self.python_names
        output = sh.execute(cmdline)
        pips_to_download = list(utils.splitlines_not_empty(output[0]))
        return pips_to_download

    def _try_download_dependencies(self, attempt, pips_to_download,
                                   pip_download_dir,
                                   pip_cache_dir,
                                   pip_build_dir):
        pips_to_download = [str(p) for p in pips_to_download]
        cmdline = [
            self.pip_executable,
            "install",
            "--download", pip_download_dir,
            "--download-cache", pip_cache_dir,
            "--build", pip_build_dir,
        ]
        cmdline.extend(sorted(pips_to_download))
        download_filename = "pip-download-attempt-%s.out"
        download_filename = download_filename % (attempt)
        out_filename = sh.joinpths(self.log_dir, download_filename)
        sh.execute_save_output(cmdline, out_filename=out_filename)

    def download_dependencies(self, clear_cache=False):
        """Download dependencies from `$deps_dir/download-requires`.

        :param clear_cache: clear `$deps_dir/cache` dir (pip can work incorrectly
            when it has a cache)
        """
        sh.deldir(self.download_dir)
        sh.mkdir(self.download_dir, recurse=True)
        download_requires_filename = sh.joinpths(self.deps_dir,
                                                 "download-requires")
        raw_pips_to_download = self.filter_download_requires()
        pips_to_download = [pkg_resources.Requirement.parse(str(p.strip()))
                            for p in raw_pips_to_download if p.strip()]
        sh.write_file(download_requires_filename,
                      "\n".join(str(req) for req in pips_to_download))
        if not pips_to_download:
            return []
        pip_dir = sh.joinpths(self.deps_dir, "pip")
        pip_download_dir = sh.joinpths(pip_dir, "download")
        pip_build_dir = sh.joinpths(pip_dir, "build")
        pip_cache_dir = sh.joinpths(pip_dir, "cache")
        if clear_cache:
            sh.deldir(pip_cache_dir)
        pip_failures = []
        how_many = len(pips_to_download)
        for attempt in xrange(self.MAX_PIP_DOWNLOAD_ATTEMPTS):
            # NOTE(aababilov): pip has issues with already downloaded files
            sh.deldir(pip_download_dir)
            sh.mkdir(pip_download_dir, recurse=True)
            sh.deldir(pip_build_dir)
            utils.log_iterable(sorted(raw_pips_to_download),
                               logger=LOG,
                               header=("Downloading %s python dependencies "
                                       "(attempt %s)" % (how_many, attempt)))
            failed = False
            try:
                self._try_download_dependencies(attempt, pips_to_download,
                                                pip_download_dir,
                                                pip_cache_dir, pip_build_dir)
                pip_failures = []
            except exc.ProcessExecutionError as e:
                LOG.exception("Failed downloading python dependencies")
                pip_failures.append(e)
                failed = True
            if not failed:
                break
        if pip_failures:
            raise pip_failures[-1]
        for filename in sh.listdir(pip_download_dir, files_only=True):
            sh.move(filename, self.download_dir)
        return sh.listdir(self.download_dir, files_only=True)
