import tempfile
from typing import Optional
import snakemake.common.tests
from snakemake_interface_executor_plugins import ExecutorSettingsBase

from snakemake_executor_plugin_kubernetes import ExecutorSettings


BUCKET_NAME = "snakemake-testing-kubernetes-%s-bucket" % next(tempfile._get_candidate_names())



class TestWorkflows(snakemake.common.tests.TestWorkflowsMinioPlayStorageBase):
    __test__ = True

    def get_executor(self) -> str:
        return "kubernetes"

    def get_executor_settings(self) -> Optional[ExecutorSettingsBase]:
        return ExecutorSettings()
