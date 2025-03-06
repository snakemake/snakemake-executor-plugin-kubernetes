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
            "allowing it to utilise one entire node. N.B: the job itself would still "
            "see the original value, i.e. as the value substituted in {threads}."
        },
    )
    service_account_name: Optional[str] = field(
        default=None,
        metadata={
            "help": "This argument allows the use of customer service "
            "accounts for "
            "kubernetes pods. If specified, serviceAccountName will "
            "be added to the "
            "pod specs. This is e.g. needed when using workload "
            "identity which is enforced "
            "when using Google Cloud GKE Autopilot."
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


# Required:
# Specify common settings shared by various executors.
common_settings = CommonSettings(
    # define whether your executor plugin executes locally
    # or remotely. In virtually all cases, it will be remote execution
    # (cluster, cloud, etc.). Only Snakemake's standard execution
    # plugins (snakemake-executor-plugin-dryrun, snakemake-executor-plugin-local)
    # are expected to specify False here.
    non_local_exec=True,
    # Define whether your executor plugin implies that there is no shared
    # filesystem (True) or not (False).
    # This is e.g. the case for cloud execution.
    implies_no_shared_fs=True,
    job_deploy_sources=True,
    pass_default_storage_provider_args=True,
    pass_default_resources_args=True,
    pass_envvar_declarations_to_cmd=False,
    auto_deploy_default_storage_provider=True,
)


# Required:
# Implementation of your executor
class Executor(RemoteExecutor):
    def __post_init__(self):
        # Attempt loading kube_config or in-cluster config
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
        # Implement here how to run a job.
        # You can access the job's resources, etc.
        # via the job object.
        # After submitting the job, you have to call
        # self.report_job_submission(job_info).
        # with job_info being of type
        # snakemake_interface_executor_plugins.executors.base.SubmittedJobInfo.
        # Convert job.resources to a normal dict first (fix for membership checks).
        resources_dict = dict(job.resources)

        exec_job = self.format_job_exec(job)
        self.logger.debug(f"Executing job: {exec_job}")

        # Kubernetes silently does not submit a job if the name is too long
        # therefore, we ensure that it is not longer than snakejob+uuid.
        jobid = "snakejob-{}".format(
            get_uuid(f"{self.run_namespace}-{job.jobid}-{job.attempt}")
        )

        body = kubernetes.client.V1Pod()
        body.metadata = kubernetes.client.V1ObjectMeta(labels={"app": "snakemake"})
        body.metadata.name = jobid

        # Container setup
        container = kubernetes.client.V1Container(name=jobid)
        container.image = self.container_image
        container.command = shlex.split("/bin/sh")
        container.args = ["-c", exec_job]
        container.working_dir = "/workdir"
        container.volume_mounts = [
            kubernetes.client.V1VolumeMount(name="workdir", mount_path="/workdir"),
        ]

        # Volume mounts
        for pvc in self.persistent_volumes:
            container.volume_mounts.append(
                kubernetes.client.V1VolumeMount(name=pvc.name, mount_path=str(pvc.path))
            )

        # Node selector
        node_selector = {}
        if "machine_type" in resources_dict.keys():
            node_selector["node.kubernetes.io/instance-type"] = resources_dict[
                "machine_type"
            ]
            self.logger.debug(f"Set node selector for machine type: {node_selector}")

        # Initialize PodSpec
        body.spec = kubernetes.client.V1PodSpec(
            containers=[container], node_selector=node_selector, restart_policy="Never"
        )

        # Add toleration for GPU nodes if GPU is requested
        if "gpu" in resources_dict:
            # Manufacturer logic
            manufacturer = resources_dict.get("gpu_manufacturer", None)
            if not manufacturer:
                raise WorkflowError(
                    "GPU requested but no manufacturer set. "
                    "Use gpu_manufacturer='nvidia' or 'amd'."
                )
            manufacturer_lc = manufacturer.lower()
            if manufacturer_lc == "nvidia":
                # Toleration for nvidia.com/gpu
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
                self.logger.debug(
                    f"Added toleration for NVIDIA GPU: {body.spec.tolerations}"
                )

            elif manufacturer_lc == "amd":
                # Toleration for amd.com/gpu
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
                self.logger.debug(
                    f"Added toleration for AMD GPU: {body.spec.tolerations}"
                )

            else:
                raise WorkflowError(
                    f"Unsupported GPU manufacturer '{manufacturer}'. "
                    "Must be 'nvidia' or 'amd'."
                )

        # capabilities
        if (
            job.is_containerized
            and DeploymentMethod.APPTAINER
            in self.workflow.deployment_settings.deployment_method
        ):
            # TODO this should work, but it doesn't currently because of
            # missing loop devices
            # singularity inside docker requires SYS_ADMIN capabilities
            # see
            # https://groups.google.com/a/lbl.gov/forum/#!topic/singularity/e9mlDuzKowc
            # container.capabilities = kubernetes.client.V1Capabilities()
            # container.capabilities.add = ["SYS_ADMIN",
            #                               "DAC_OVERRIDE",
            #                               "SETUID",
            #                               "SETGID",
            #                               "SYS_CHROOT"]

            # Running in priviledged mode always works
            container.security_context = kubernetes.client.V1SecurityContext(
                privileged=True
            )

        # Add service account name if provided
        if self.k8s_service_account_name:
            body.spec.service_account_name = self.k8s_service_account_name
            self.logger.debug(
                f"Set service account name: {self.k8s_service_account_name}"
            )

        # Workdir volume
        workdir_volume = kubernetes.client.V1Volume(name="workdir")
        workdir_volume.empty_dir = kubernetes.client.V1EmptyDirVolumeSource()
        body.spec.volumes = [workdir_volume]

        for pvc in self.persistent_volumes:
            volume = kubernetes.client.V1Volume(name=pvc.name)
            volume.persistent_volume_claim = (
                kubernetes.client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc.name
                )
            )
            body.spec.volumes.append(volume)

        # Env vars
        container.env = []
        for key, e in self.secret_envvars.items():
            envvar = kubernetes.client.V1EnvVar(name=e)
            envvar.value_from = kubernetes.client.V1EnvVarSource()
            envvar.value_from.secret_key_ref = kubernetes.client.V1SecretKeySelector(
                key=key, name=self.run_namespace
            )
            container.env.append(envvar)

        # Request resources
        self.logger.debug(f"Job resources: {resources_dict}")
        container.resources = kubernetes.client.V1ResourceRequirements()
        container.resources.requests = {}

        scale_value = resources_dict.get("scale", 1)

        # Only create container.resources.limits if scale is False
        if not scale_value:
            container.resources.limits = {}
        # CPU and memory requests
        cores = resources_dict.get("_cores", 1)
        container.resources.requests["cpu"] = "{}m".format(
            int(cores * self.k8s_cpu_scalar * 1000)
        )

        if not scale_value:
            container.resources.limits["cpu"] = "{}m".format(int(cores * 1000))

        if "mem_mb" in resources_dict:
            mem_mb = resources_dict["mem_mb"]
            container.resources.requests["memory"] = "{}M".format(mem_mb)
            if not scale_value:
                container.resources.limits["memory"] = "{}M".format(mem_mb)
        # Disk
        if "disk_mb" in resources_dict:
            disk_mb = int(resources_dict.get("disk_mb", 1024))
            container.resources.requests["ephemeral-storage"] = f"{disk_mb}M"
            if not scale_value:
                container.resources.limits["ephemeral-storage"] = f"{disk_mb}M"

        # Request GPU resources if specified
        if "gpu" in resources_dict:
            gpu_count = str(resources_dict["gpu"])
            # For nvidia, K8s expects nvidia.com/gpu; for amd, we use amd.com/gpu.
            # But let's keep nvidia.com/gpu
            # for both if the cluster doesn't differentiate.
            # If your AMD plugin uses a different name, update accordingly:
            manufacturer = resources_dict.get("gpu_manufacturer", "").lower()
            if manufacturer == "nvidia":
                container.resources.requests["nvidia.com/gpu"] = gpu_count
                if not scale_value:
                    container.resources.limits["nvidia.com/gpu"] = gpu_count
                self.logger.debug(f"Requested NVIDIA GPU resources: {gpu_count}")
            elif manufacturer == "amd":
                container.resources.requests["amd.com/gpu"] = gpu_count
                if not scale_value:
                    container.resources.limits["amd.com/gpu"] = gpu_count
                self.logger.debug(f"Requested AMD GPU resources: {gpu_count}")
            else:
                # fallback if we never see a recognized manufacturer
                # (the code above raises an error first, so we might never get here)
                container.resources.requests["nvidia.com/gpu"] = gpu_count
                if not scale_value:
                    container.resources.limits["nvidia.com/gpu"] = gpu_count
        # Privileged mode
        if self.privileged:
            container.security_context = kubernetes.client.V1SecurityContext(
                privileged=True
            )
            self.logger.debug("Container set to run in privileged mode.")

        self.logger.debug(f"k8s pod resources: {container.resources}")

        # Assign the modified container back to the spec
        body.spec.containers = [container]

        # Serialize and log the pod specification
        import json

        self.logger.debug("Pod specification:")
        self.logger.debug(json.dumps(body.to_dict(), indent=2))

        # Try creating the pod with exception handling
        try:
            pod = self._kubernetes_retry(
                lambda: self.kubeapi.create_namespaced_pod(self.namespace, body)
            )
        except kubernetes.client.rest.ApiException as e:
            self.logger.error(f"Failed to create pod: {e}")
            raise WorkflowError(f"Failed to create pod: {e}")

        self.logger.info(
            "Get status with:\n"
            "kubectl describe pod {jobid}\n"
            "kubectl logs {jobid}".format(jobid=jobid)
        )

        self.report_job_submission(
            SubmittedJobInfo(job=job, external_jobid=jobid, aux={"pod": pod})
        )

    async def check_active_jobs(
        self, active_jobs: List[SubmittedJobInfo]
    ) -> Generator[SubmittedJobInfo, None, None]:
        # Check the status of active jobs.

        # You have to iterate over the given list active_jobs.
        # For jobs that have finished successfully, you have to call
        # self.report_job_success(job).
        # For jobs that have errored, you have to call
        # self.report_job_error(job).
        # Jobs that are still running have to be yielded.
        #
        # For queries to the remote middleware, please use
        # self.status_rate_limiter like this:
        #
        # async with self.status_rate_limiter:
        #    # query remote middleware here
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
                        # Jobid not found
                        # The job is likely already done and was deleted on
                        # the server.
                        j.callback(j.job)
                        continue
                    else:
                        self.logger.error(f"ApiException when checking pod status: {e}")
                        self.report_job_error(j, msg=str(e))
                        continue
                except WorkflowError as e:
                    self.logger.error(f"WorkflowError when checking pod status: {e}")
                    self.report_job_error(j, msg=str(e))
                    continue

                if res is None:
                    msg = (
                        "Unknown pod {jobid}. Has the pod been deleted manually?"
                    ).format(jobid=j.external_jobid)
                    self.logger.error(msg)
                    self.report_job_error(j, msg=msg)
                elif res.status.phase == "Failed":
                    msg = (
                        "For details, please issue:\n"
                        "kubectl describe pod {jobid}\n"
                        "kubectl logs {jobid}"
                    ).format(jobid=j.external_jobid)
                    # failed
                    kube_log_content = self.kubeapi.read_namespaced_pod_log(
                        name=j.external_jobid, namespace=self.namespace
                    )
                    kube_log = self.log_path / f"{j.external_jobid}.log"
                    with open(kube_log, "w") as f:
                        f.write(kube_log_content)
                    self.logger.error(f"Job {j.external_jobid} failed. {msg}")
                    self.report_job_error(j, msg=msg, aux_logs=[str(kube_log)])
                elif res.status.phase == "Succeeded":
                    # finished
                    self.logger.info(f"Job {j.external_jobid} succeeded.")
                    self.report_job_success(j)

                    self._kubernetes_retry(
                        lambda: self.safe_delete_pod(
                            j.external_jobid, ignore_not_found=True
                        )
                    )
                else:
                    # still active
                    self.logger.debug(f"Job {j.external_jobid} is still active.")
                    yield j

    def cancel_jobs(self, active_jobs: List[SubmittedJobInfo]):
        # Cancel all active jobs.
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
        secret.metadata = kubernetes.client.V1ObjectMeta()
        # create a random uuid
        secret.metadata.name = self.run_namespace
        secret.type = "Opaque"
        secret.data = {}

        for name, value in self.envvars().items():
            key = name.lower()
            secret.data[key] = base64.b64encode(value.encode()).decode()
            self.secret_envvars[key] = name

        # Test if the total size of the configMap exceeds 1MB
        config_map_size = sum(
            [len(base64.b64decode(v)) for k, v in secret.data.items()]
        )
        if config_map_size > 1048576:
            raise WorkflowError(
                "The total size of the included files and other Kubernetes secrets "
                f"is {config_map_size}, exceeding the 1MB limit.\n"
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
                    "[WARNING] 404 not found when trying to delete the pod: {jobid}\n"
                    "[WARNING] Ignore this error\n".format(jobid=jobid)
                )
            else:
                raise e

    def _reauthenticate_and_retry(self, func=None):
        import kubernetes

        # Unauthorized.
        # Reload config in order to ensure token is
        # refreshed. Then try again.
        self.logger.info("Trying to reauthenticate")
        kubernetes.config.load_kube_config()
        subprocess.run(["kubectl", "get", "nodes"])

        self.kubeapi = kubernetes.client.CoreV1Api()
        self.batchapi = kubernetes.client.BatchV1Api()

        try:
            self.register_secret()
        except kubernetes.client.rest.ApiException as e:
            if e.status == 409 and e.reason == "Conflict":
                self.logger.warning(
                    "409 conflict ApiException when registering secrets"
                )
                self.logger.warning(e)
            else:
                raise WorkflowError(
                    e,
                    "This is likely a bug in "
                    "https://github.com/kubernetes-client/python.",
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
                    # Unauthorized.
                    # Reload config in order to ensure token is
                    # refreshed. Then try again.
                    return self._reauthenticate_and_retry(func)
            # Handling timeout that may occur in case of GKE master upgrade
            except urllib3.exceptions.MaxRetryError:
                self.logger.warning(
                    "Request time out! "
                    "check your connection to Kubernetes master"
                    "Workflow will pause for 5 minutes to allow any update "
                    "operations to complete"
                )
                time.sleep(300)
                try:
                    return func()
                except Exception as e:
                    # Still can't reach the server after 5 minutes
                    raise WorkflowError(
                        e,
                        "Error 111 connection timeout, please check"
                        " that the k8s cluster master is reachable!",
                    )


UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/snakemake/snakemake-executor-plugin-kubernetes",
)


def get_uuid(name):
    return uuid.uuid5(UUID_NAMESPACE, name)
