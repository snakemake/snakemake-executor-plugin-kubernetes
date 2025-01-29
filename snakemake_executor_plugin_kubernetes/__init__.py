import base64
from dataclasses import dataclass, field
from pathlib import Path
import shlex
import subprocess
import time
from typing import List, Generator, Optional, Self
import uuid

import kubernetes
import kubernetes.config
import kubernetes.client

from snakemake_interface_executor_plugins.executors.base import SubmittedJobInfo
from snakemake_interface_executor_plugins.executors.remote import RemoteExecutor
from snakemake_interface_executor_plugins.settings import (
    ExecutorSettingsBase,
    CommonSettings,
)
from snakemake_interface_executor_plugins.jobs import (
    JobExecutorInterface,
)
from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_executor_plugins.settings import DeploymentMethod


@dataclass
class PersistentVolume:
    name: str
    path: Path

    @classmethod
    def parse(cls, arg: str) -> Self:
        spec = arg.split(":")
        if len(spec) != 2:
            raise WorkflowError(
                f"Invalid persistent volume spec ({arg}), has to be <name>:<path>."
            )
        name, path = spec
        return cls(name=name, path=Path(path))

    def unparse(self) -> str:
        return f"{self.name}:{self.path}"


def parse_persistent_volumes(args: List[str]) -> List[PersistentVolume]:
    return [PersistentVolume.parse(arg) for arg in args]


def unparse_persistent_volumes(args: List[PersistentVolume]) -> List[str]:
    return [arg.unparse() for arg in args]


@dataclass
class ExecutorSettings(ExecutorSettingsBase):
    namespace: str = field(
        default="default", metadata={"help": "The namespace to use for submitted jobs."}
    )
    cpu_scalar: float = field(
        default=0.95,
        metadata={
            "help": "K8s reserves some proportion of available CPUs for its own use. "
            "So, where an underlying node may have 8 CPUs, only e.g. 7600 milliCPUs "
            "are allocatable to k8s pods (i.e. snakemake jobs). As 8 > 7.6, k8s can't "
            "find a node with enough CPU resource to run such jobs. This argument acts "
            "as a global scalar on each job's CPU request, so that e.g. a job whose "
            "rule definition asks for 8 CPUs will request 7600m CPUs from k8s, "
            "allowing it to utilize one entire node. Note that the job itself would "
            "still see the original value (substituted in {threads})."
        },
    )
    service_account_name: Optional[str] = field(
        default=None,
        metadata={
            "help": "Use a custom service account for kubernetes pods. "
            "If specified, serviceAccountName is added to the Pod. "
            "Needed in scenarios like GKE Workload Identity."
        },
    )
    privileged: Optional[bool] = field(
        default=False,
        metadata={"help": "Create privileged containers for jobs."},
    )
    persistent_volumes: List[PersistentVolume] = field(
        default_factory=list,
        metadata={
            "help": "Mount the given persistent volumes under the given paths in each "
            "job container (<name>:<path>). ",
            "parse_func": parse_persistent_volumes,
            "unparse_func": unparse_persistent_volumes,
            "nargs": "+",
        },
    )


# Common settings for the executor.
common_settings = CommonSettings(
    non_local_exec=True,
    implies_no_shared_fs=True,
    job_deploy_sources=True,
    pass_default_storage_provider_args=True,
    pass_default_resources_args=True,
    pass_envvar_declarations_to_cmd=False,
    auto_deploy_default_storage_provider=True,
)


class Executor(RemoteExecutor):
    def __post_init__(self):
        try:
            kubernetes.config.load_kube_config()
        except kubernetes.config.config_exception.ConfigException:
            kubernetes.config.load_incluster_config()

        self.k8s_cpu_scalar = self.workflow.executor_settings.cpu_scalar
        self.k8s_service_account_name = (
            self.workflow.executor_settings.service_account_name
        )
        self.kubeapi = kubernetes.client.CoreV1Api()
        self.batchapi = kubernetes.client.BatchV1Api()
        self.namespace = self.workflow.executor_settings.namespace
        self.envvars = self.workflow.spawned_job_args_factory.envvars()
        self.secret_files = {}
        self.run_namespace = str(uuid.uuid4())
        self.secret_envvars = {}
        self.register_secret()
        self.log_path = self.workflow.persistence.aux_path / "kubernetes-logs"
        self.log_path.mkdir(exist_ok=True, parents=True)
        self.container_image = self.workflow.remote_execution_settings.container_image
        self.privileged = self.workflow.executor_settings.privileged
        self.persistent_volumes = self.workflow.executor_settings.persistent_volumes

        self.logger.info(f"Using {self.container_image} for Kubernetes jobs.")

    def run_job(self, job: JobExecutorInterface):
        exec_job = self.format_job_exec(job)
        self.logger.debug(f"Executing job: {exec_job}")

        # Generate a shortened unique job name.
        jobid = "snakejob-{}".format(
            get_uuid(f"{self.run_namespace}-{job.jobid}-{job.attempt}")
        )

        body = kubernetes.client.V1Pod()
        body.metadata = kubernetes.client.V1ObjectMeta(labels={"app": "snakemake"})
        body.metadata.name = jobid

        container = kubernetes.client.V1Container(name=jobid)
        container.image = self.container_image
        container.command = shlex.split("/bin/sh")
        container.args = ["-c", exec_job]
        container.working_dir = "/workdir"
        container.volume_mounts = [
            kubernetes.client.V1VolumeMount(name="workdir", mount_path="/workdir")
        ]

        # Mount persistent volumes (PVCs) into the container.
        for pvc in self.persistent_volumes:
            container.volume_mounts.append(
                kubernetes.client.V1VolumeMount(name=pvc.name, mount_path=str(pvc.path))
            )

        # Optional node selector for machine_type
        node_selector = {}
        if "machine_type" in job.resources:
            node_selector["node.kubernetes.io/instance-type"] = job.resources["machine_type"]

        body.spec = kubernetes.client.V1PodSpec(
            containers=[container],
            node_selector=node_selector,
            restart_policy="Never",
        )

        # Service account
        if self.k8s_service_account_name:
            body.spec.service_account_name = self.k8s_service_account_name

        # Define volumes
        workdir_volume = kubernetes.client.V1Volume(name="workdir")
        workdir_volume.empty_dir = kubernetes.client.V1EmptyDirVolumeSource()
        body.spec.volumes = [workdir_volume]

        for pvc in self.persistent_volumes:
            volume = kubernetes.client.V1Volume(name=pvc.name)
            volume.persistent_volume_claim = (
                kubernetes.client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc.name)
            )
            body.spec.volumes.append(volume)

        # Env vars from secret
        container.env = []
        for key, e in self.secret_envvars.items():
            envvar = kubernetes.client.V1EnvVar(name=e)
            envvar.value_from = kubernetes.client.V1EnvVarSource()
            envvar.value_from.secret_key_ref = kubernetes.client.V1SecretKeySelector(
                key=key, name=self.run_namespace
            )
            container.env.append(envvar)

        # Resource requests/limits
        container.resources = kubernetes.client.V1ResourceRequirements()
        container.resources.requests = {}
        container.resources.limits = {}

        # CPU
        cores = job.resources["_cores"]
        container.resources.requests["cpu"] = f"{int(cores * self.k8s_cpu_scalar * 1000)}m"
        container.resources.limits["cpu"] = f"{int(cores * 1000)}m"

        # Memory
        if "mem_mb" in job.resources:
            mem_mb = job.resources["mem_mb"]
            container.resources.requests["memory"] = f"{mem_mb}M"
            container.resources.limits["memory"] = f"{mem_mb}M"

        # Disk (ephemeral storage)
        if "disk_mb" in job.resources:
            disk_mb = int(job.resources["disk_mb"])
            container.resources.requests["ephemeral-storage"] = f"{disk_mb}M"
            container.resources.limits["ephemeral-storage"] = f"{disk_mb}M"

        # GPU support
        if "gpu" in job.resources:
            gpu_count = str(job.resources["gpu"])
            manufacturer = job.resources.get("manufacturer", None)
            if not manufacturer:
                raise WorkflowError(
                    "GPU requested but 'manufacturer' not provided. "
                    "Set job.resources['manufacturer'] = 'nvidia' or 'amd'."
                )

            manufacturer = manufacturer.lower()
            if manufacturer == "nvidia":
                container.resources.requests["nvidia.com/gpu"] = gpu_count
                container.resources.limits["nvidia.com/gpu"] = gpu_count
                # Example node label for nvidia GPU nodes
                node_selector["accelerator"] = "nvidia"

                # Add toleration for NVIDIA if the node is tainted
                if body.spec.tolerations is None:
                    body.spec.tolerations = []
                body.spec.tolerations.append(
                    kubernetes.client.V1Toleration(
                        key="nvidia.com/gpu",
                        operator="Equal",
                        value="present",
                        effect="NoSchedule",
                    )
                )
            elif manufacturer == "amd":
                container.resources.requests["amd.com/gpu"] = gpu_count
                container.resources.limits["amd.com/gpu"] = gpu_count
                # Example node label for amd GPU nodes
                node_selector["accelerator"] = "amd"

                # Add toleration for AMD if the node is tainted
                if body.spec.tolerations is None:
                    body.spec.tolerations = []
                body.spec.tolerations.append(
                    kubernetes.client.V1Toleration(
                        key="amd.com/gpu",
                        operator="Equal",
                        value="present",
                        effect="NoSchedule",
                    )
                )
            else:
                raise WorkflowError(
                    f"Unsupported GPU manufacturer '{manufacturer}'. "
                    "Must be 'nvidia' or 'amd'."
                )

        # Privileged containers
        if self.privileged:
            container.security_context = kubernetes.client.V1SecurityContext(
                privileged=True
            )

        self.logger.debug(f"Pod resources: {container.resources}")

        body.spec.containers = [container]

        # Create the pod
        pod = self._kubernetes_retry(
            lambda: self.kubeapi.create_namespaced_pod(self.namespace, body)
        )

        self.logger.info(
            "Get status with:\n"
            f"kubectl describe pod {jobid}\n"
            f"kubectl logs {jobid}"
        )

        self.report_job_submission(
            SubmittedJobInfo(job=job, external_jobid=jobid, aux={"pod": pod})
        )

    async def check_active_jobs(
        self, active_jobs: List[SubmittedJobInfo]
    ) -> Generator[SubmittedJobInfo, None, None]:
        self.logger.debug(f"Checking status of {len(active_jobs)} jobs")
        for j in active_jobs:
            async with self.status_rate_limiter:
                try:
                    res = self._kubernetes_retry(
                        lambda: self.kubeapi.read_namespaced_pod_status(
                            j.external_jobid, self.namespace
                        )
                    )
                except kubernetes.client.rest.ApiException as e:
                    if e.status == 404:
                        # Pod not found => job might have finished & been deleted
                        j.callback(j.job)
                        continue
                except WorkflowError as e:
                    self.report_job_error(j, msg=str(e))
                    continue

                if res is None:
                    msg = (
                        f"Unknown pod {j.external_jobid}. "
                        "Has the pod been deleted manually?"
                    )
                    self.report_job_error(j, msg=msg)
                elif res.status.phase == "Failed":
                    msg = (
                        "For details, please issue:\n"
                        f"kubectl describe pod {j.external_jobid}\n"
                        f"kubectl logs {j.external_jobid}"
                    )
                    kube_log_content = self.kubeapi.read_namespaced_pod_log(
                        name=j.external_jobid, namespace=self.namespace
                    )
                    kube_log = self.log_path / f"{j.external_jobid}.log"
                    with open(kube_log, "w") as f:
                        f.write(kube_log_content)
                    self.report_job_error(j, msg=msg, aux_logs=[str(kube_log)])
                elif res.status.phase == "Succeeded":
                    self.report_job_success(j)
                    self._kubernetes_retry(
                        lambda: self.safe_delete_pod(
                            j.external_jobid, ignore_not_found=True
                        )
                    )
                else:
                    # still running
                    yield j

    def cancel_jobs(self, active_jobs: List[SubmittedJobInfo]):
        for j in active_jobs:
            self._kubernetes_retry(
                lambda: self.safe_delete_pod(j.external_jobid, ignore_not_found=True)
            )

    def shutdown(self):
        self.unregister_secret()
        super().shutdown()

    def register_secret(self):
        import kubernetes.client

        secret = kubernetes.client.V1Secret()
        secret.metadata = kubernetes.client.V1ObjectMeta(name=self.run_namespace)
        secret.type = "Opaque"
        secret.data = {}

        for name, value in self.envvars.items():
            key = name.lower()
            secret.data[key] = base64.b64encode(value.encode()).decode()
            self.secret_envvars[key] = name

        config_map_size = sum(len(base64.b64decode(v)) for v in secret.data.values())
        if config_map_size > 1048576:
            raise WorkflowError(
                "The total size of files/envs in the Kubernetes secret exceeds 1MB."
            )

        self.kubeapi.create_namespaced_secret(self.namespace, secret)

    def unregister_secret(self):
        import kubernetes.client

        self._kubernetes_retry(
            lambda: self.kubeapi.delete_namespaced_secret(
                self.run_namespace,
                self.namespace,
                body=kubernetes.client.V1DeleteOptions(),
            )
        )

    def safe_delete_pod(self, jobid, ignore_not_found=True):
        import kubernetes.client

        body = kubernetes.client.V1DeleteOptions()
        try:
            self.kubeapi.delete_namespaced_pod(jobid, self.namespace, body=body)
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404 and ignore_not_found:
                self.logger.warning(
                    f"[WARNING] 404 not found when trying to delete pod {jobid}. "
                    "Ignoring error."
                )
            else:
                raise e

    def _reauthenticate_and_retry(self, func=None):
        import kubernetes

        self.logger.info("Trying to reauthenticate")
        kubernetes.config.load_kube_config()
        subprocess.run(["kubectl", "get", "nodes"])

        self.kubeapi = kubernetes.client.CoreV1Api()
        self.batchapi = kubernetes.client.BatchV1Api()

        try:
            self.register_secret()
        except kubernetes.client.rest.ApiException as e:
            if e.status == 409 and e.reason == "Conflict":
                self.logger.warning("409 conflict when registering secrets.")
                self.logger.warning(e)
            else:
                raise WorkflowError(
                    e, "Error re-registering secret. Possibly a k8s-client bug."
                )

        if func:
            return func()

    def _kubernetes_retry(self, func):
        import kubernetes
        import urllib3

        with self.lock:
            try:
                return func()
            except kubernetes.client.rest.ApiException as e:
                if e.status == 401:
                    return self._reauthenticate_and_retry(func)
            except urllib3.exceptions.MaxRetryError:
                self.logger.warning(
                    "Request timeout! Checking connection to Kubernetes master. "
                    "Pausing for 5 minutes to allow updates."
                )
                time.sleep(300)
                try:
                    return func()
                except Exception as e:
                    raise WorkflowError(
                        e,
                        "Still cannot reach cluster master after 5 minutes. "
                        "Check connectivity!",
                    )


UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/snakemake/snakemake-executor-plugin-kubernetes",
)


def get_uuid(name):
    return uuid.uuid5(UUID_NAMESPACE, name)
