from typing import Optional
import snakemake.common.tests
from snakemake_interface_executor_plugins import ExecutorSettingsBase

from snakemake_executor_plugin_kubernetes import ExecutorSettings


class TestWorkflows(snakemake.common.tests.TestWorkflowsBase):
    __test__ = True

    def get_executor(self) -> str:
        return "kubernetes"

    def get_executor_settings(self) -> Optional[ExecutorSettingsBase]:
        return ExecutorSettings()

    def get_default_remote_provider(self) -> Optional[str]:
        # TODO determine what remote provide to use for the testing!
        return None

    def get_default_remote_prefix(self) -> Optional[str]:
        return None
