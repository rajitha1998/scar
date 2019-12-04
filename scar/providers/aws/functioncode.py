# Copyright (C) GRyCAP - I3M - UPV
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Module with methods and classes to create the function deployment package."""

from typing import Dict
from zipfile import ZipFile
from scar.providers.aws.udocker import Udocker
from scar.providers.aws.validators import AWSValidator
from scar.exceptions import exception
import scar.logger as logger
from scar.http.request import get_file
from scar.utils import FileUtils, GitHubUtils, \
                       GITHUB_USER, GITHUB_SUPERVISOR_PROJECT


def create_function_config(resources_info):
    function_cfg = {'storage_providers': FileUtils.load_tmp_config_file().get('storage_providers', {})}
    function_cfg.update(resources_info.get('lambda'))
    return function_cfg

class FunctionPackager():
    """Class to manage the deployment package creation."""

    def __init__(self, resources_info: Dict):
        self.resources_info = resources_info
        # Temporal folder to store the supervisor and udocker files
        self._tmp_payload_folder = FileUtils.create_tmp_dir()
        # Path where the supervisor is downloaded
        self._supervisor_path = FileUtils.create_tmp_dir()
        self._supervisor_zip_path = FileUtils.join_paths(self._supervisor_path.name, 'faas.zip')

    @exception(logger)
    def create_zip(self, lambda_payload_path: str) -> None:
        """Creates the lambda function deployment package."""
        self._download_faas_supervisor_zip()
        self._extract_handler_code()
        self._copy_function_configuration()
        self._manage_udocker_images()
        self._add_init_script()
        self._add_extra_payload()
        self._zip_scar_folder(lambda_payload_path)
        self._check_code_size()

    def _download_faas_supervisor_zip(self) -> None:
        supervisor_zip_url = GitHubUtils.get_source_code_url(
            GITHUB_USER,
            GITHUB_SUPERVISOR_PROJECT,
            self.resources_info.get('lambda').get('supervisor').get('version'))
        with open(self._supervisor_zip_path, "wb") as thezip:
            thezip.write(get_file(supervisor_zip_url))

    def _extract_handler_code(self) -> None:
        function_handler_dest = FileUtils.join_paths(self._tmp_payload_folder.name, f"{self.resources_info.get('lambda').get('name')}.py")
        file_path = ""
        with ZipFile(self._supervisor_zip_path) as thezip:
            for file in thezip.namelist():
                if file.endswith("function_handler.py"):
                    file_path = FileUtils.join_paths(FileUtils.get_tmp_dir(), file)
                    # Extracts the complete folder structure and the file (cannot avoid)
                    thezip.extract(file, FileUtils.get_tmp_dir())
                    break
        if file_path:
            # Copy only the handler to the payload folder
            FileUtils.copy_file(file_path, function_handler_dest)

    def _copy_function_configuration(self):
        cfg_file_path = FileUtils.join_paths(self._tmp_payload_folder.name, "function_config.yaml")
        function_cfg = create_function_config(self.resources_info)
        FileUtils.write_yaml(cfg_file_path, function_cfg)

    def _manage_udocker_images(self):
        if self.resources_info.get('lambda').get('deployment').get('bucket', False) and \
           self.resources_info.get('lambda').get('container').get('image', False):
            Udocker(self.resources_info, self._tmp_payload_folder.name, self._supervisor_zip_path).download_udocker_image()
        if self.resources_info.get('lambda').get('image_file'):
            Udocker(self.resources_info, self._tmp_payload_folder.name, self._supervisor_zip_path).prepare_udocker_image()
            del(self.resources_info['lambda']['image_file'])

    def _add_init_script(self) -> None:
        """Copy the init script defined by the user to the payload folder."""
        if self.resources_info.get('lambda').get('init_script', False):
            init_script_path = self.resources_info.get('lambda').get('init_script')
            FileUtils.copy_file(init_script_path,
                                FileUtils.join_paths(self._tmp_payload_folder.name,
                                                     FileUtils.get_file_name(init_script_path)))
            del(self.resources_info['lambda']['init_script'])

    def _add_extra_payload(self) -> None:
        if self.resources_info.get('lambda').get('extra_payload', False):
            payload_path = self.resources_info.get('lambda').get('extra_payload')
            logger.info(f"Adding extra payload '{payload_path}'")
            if FileUtils.is_file(payload_path):
                FileUtils.copy_file(self.resources_info.get('lambda').get('extra_payload'),
                                    self._tmp_payload_folder.name)
            else:
                FileUtils.copy_dir(self.resources_info.get('lambda').get('extra_payload'),
                                   self._tmp_payload_folder.name)
            del(self.resources_info['lambda']['extra_payload'])

    def _zip_scar_folder(self, lambda_payload_path: str) -> None:
        """Zips the tmp folder with all the function's files and
        save it in the expected path of the payload."""
        FileUtils.zip_folder(lambda_payload_path,
                             self._tmp_payload_folder.name,
                             "Creating function package")

    def _check_code_size(self):
        # Check if the code size fits within the AWS limits
        if self.resources_info.get('lambda').get('deployment').get('bucket', False):
            AWSValidator.validate_s3_code_size(self._tmp_payload_folder.name,
                                               self.resources_info.get('lambda').get('deployment').get('max_s3_payload_size'))
        else:
            AWSValidator.validate_function_code_size(self._tmp_payload_folder.name,
                                                     self.resources_info.get('lambda').get('deployment').get('max_payload_size'))
