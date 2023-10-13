import tempfile
from typing import Optional
import snakemake.common.tests
import snakemake.settings
from snakemake_interface_executor_plugins import ExecutorSettingsBase

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

    def get_deployment_settings(
        self, deployment_method=frozenset()
    ) -> snakemake.settings.DeploymentSettings:
        return snakemake.settings.DeploymentSettings(
            deployment_method=deployment_method,
            default_storage_provider_auto_deploy=True,
        )

    def get_remote_execution_settings(
        self,
    ) -> snakemake.settings.RemoteExecutionSettings:
        return snakemake.settings.RemoteExecutionSettings(
            seconds_between_status_checks=0,
            envvars=self.get_envvars(),
            # TODO remove once we have switched to stable snakemake for dev
            container_image="snakemake/snakemake:latest",
        )
