import tempfile
from typing import Optional
import snakemake.common.tests
import snakemake.settings.types
from snakemake_interface_executor_plugins.settings import ExecutorSettingsBase

from snakemake_executor_plugin_kubernetes import ExecutorSettings


BUCKET_NAME = "snakemake-testing-kubernetes-%s-bucket" % next(
    tempfile._get_candidate_names()
)


class TestWorkflows(snakemake.common.tests.TestWorkflowsMinioPlayStorageBase):
    __test__ = True

    def get_executor(self) -> str:
        return "kubernetes"

    def get_executor_settings(self) -> Optional[ExecutorSettingsBase]:
        return ExecutorSettings()

    def get_assume_shared_fs(self) -> bool:
        return False

    def get_remote_execution_settings(
        self,
    ) -> snakemake.settings.types.RemoteExecutionSettings:
        return snakemake.settings.types.RemoteExecutionSettings(
            seconds_between_status_checks=10,
            envvars=self.get_envvars(),
            # TODO remove once we have switched to stable snakemake for dev
            container_image="snakemake/snakemake:latest",
        )
