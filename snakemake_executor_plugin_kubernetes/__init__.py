import base64
from dataclasses import dataclass, field
import os
import shlex
import subprocess
import time
from typing import List, Generator, Optional
import uuid
from snakemake_interface_executor_plugins.executors.base import SubmittedJobInfo
from snakemake_interface_executor_plugins.executors.remote import RemoteExecutor
from snakemake_interface_executor_plugins import ExecutorSettingsBase, CommonSettings
from snakemake_interface_executor_plugins.workflow import WorkflowExecutorInterface
from snakemake_interface_executor_plugins.logging import LoggerExecutorInterface
from snakemake_interface_executor_plugins.jobs import (
    ExecutorJobInterface,
)
from snakemake_interface_common.exceptions import WorkflowError


# Optional:
# define additional settings for your executor
# They will occur in the Snakemake CLI as --<executor-name>-<param-name>
# Omit this class if you don't need any.
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
)


# Required:
# Implementation of your executor
class Executor(RemoteExecutor):
    def __init__(
        self,
        workflow: WorkflowExecutorInterface,
        logger: LoggerExecutorInterface,
    ):
        super().__init__(
            workflow,
            logger,
            # configure behavior of RemoteExecutor below
            # whether arguments for setting the remote provider shall  be passed to jobs
            pass_default_remote_provider_args=True,
            # whether arguments for setting default resources shall be passed to jobs
            pass_default_resources_args=True,
            # whether environment variables shall be passed to jobs
            pass_envvar_declarations_to_cmd=False,
        )
        try:
            from kubernetes import config
        except ImportError:
            raise WorkflowError(
                "The Python 3 package 'kubernetes' "
                "must be installed to use Kubernetes"
            )
        config.load_kube_config()

        import kubernetes

        self.k8s_cpu_scalar = self.workflow.executor_settings.cpu_scalar
        self.k8s_service_account_name = (
            self.workflow.executor_settings.service_account_name
        )
        self.kubeapi = kubernetes.client.CoreV1Api()
        self.batchapi = kubernetes.client.BatchV1Api()
        self.namespace = self.workflow.executor_settings.namespace
        self.envvars = workflow.envvars
        self.secret_files = {}
        self.run_namespace = str(uuid.uuid4())
        self.secret_envvars = {}
        self.register_secret()
        self.container_image = self.workflow.remote_execution_settings.container_image
        logger.info(f"Using {self.container_image} for Kubernetes jobs.")

    def run_job(self, job: ExecutorJobInterface):
        # Implement here how to run a job.
        # You can access the job's resources, etc.
        # via the job object.
        # After submitting the job, you have to call
        # self.report_job_submission(job_info).
        # with job_info being of type
        # snakemake_interface_executor_plugins.executors.base.SubmittedJobInfo.

        import kubernetes.client

        exec_job = self.format_job_exec(job)

        # Kubernetes silently does not submit a job if the name is too long
        # therefore, we ensure that it is not longer than snakejob+uuid.
        jobid = "snakejob-{}".format(
            get_uuid(f"{self.run_namespace}-{job.jobid}-{job.attempt}")
        )

        body = kubernetes.client.V1Pod()
        body.metadata = kubernetes.client.V1ObjectMeta(labels={"app": "snakemake"})

        body.metadata.name = jobid

        # container
        container = kubernetes.client.V1Container(name=jobid)
        container.image = self.container_image
        container.command = shlex.split("/bin/sh")
        container.args = ["-c", exec_job]
        container.working_dir = "/workdir"
        container.volume_mounts = [
            kubernetes.client.V1VolumeMount(name="workdir", mount_path="/workdir"),
            kubernetes.client.V1VolumeMount(name="source", mount_path="/source"),
        ]

        node_selector = {}
        if "machine_type" in job.resources.keys():
            # Kubernetes labels a node by its instance type using this node_label.
            node_selector["node.kubernetes.io/instance-type"] = job.resources[
                "machine_type"
            ]

        body.spec = kubernetes.client.V1PodSpec(
            containers=[container], node_selector=node_selector
        )
        # Add service account name if provided
        if self.k8s_service_account_name:
            body.spec.service_account_name = self.k8s_service_account_name

        # fail on first error
        body.spec.restart_policy = "Never"

        # source files as a secret volume
        # we copy these files to the workdir before executing Snakemake
        too_large = [
            path
            for path in self.secret_files.values()
            if os.path.getsize(path) > 1000000
        ]
        if too_large:
            raise WorkflowError(
                "The following source files exceed the maximum "
                "file size (1MB) that can be passed from host to "
                "kubernetes. These are likely not source code "
                "files. Consider adding them to your "
                "remote storage instead or (if software) use "
                "Conda packages or container images:\n{}".format("\n".join(too_large))
            )
        secret_volume = kubernetes.client.V1Volume(name="source")
        secret_volume.secret = kubernetes.client.V1SecretVolumeSource()
        secret_volume.secret.secret_name = self.run_namespace
        secret_volume.secret.items = [
            kubernetes.client.V1KeyToPath(key=key, path=path)
            for key, path in self.secret_files.items()
        ]
        # workdir as an emptyDir volume of undefined size
        workdir_volume = kubernetes.client.V1Volume(name="workdir")
        workdir_volume.empty_dir = kubernetes.client.V1EmptyDirVolumeSource()
        body.spec.volumes = [secret_volume, workdir_volume]

        # env vars
        container.env = []
        for key, e in self.secret_envvars.items():
            envvar = kubernetes.client.V1EnvVar(name=e)
            envvar.value_from = kubernetes.client.V1EnvVarSource()
            envvar.value_from.secret_key_ref = kubernetes.client.V1SecretKeySelector(
                key=key, name=self.run_namespace
            )
            container.env.append(envvar)

        # request resources
        self.logger.debug(f"job resources:  {dict(job.resources)}")
        container.resources = kubernetes.client.V1ResourceRequirements()
        container.resources.requests = {}
        container.resources.requests["cpu"] = "{}m".format(
            int(job.resources["_cores"] * self.k8s_cpu_scalar * 1000)
        )
        if "mem_mb" in job.resources.keys():
            container.resources.requests["memory"] = "{}M".format(
                job.resources["mem_mb"]
            )
        if "disk_mb" in job.resources.keys():
            disk_mb = int(job.resources.get("disk_mb", 1024))
            container.resources.requests["ephemeral-storage"] = f"{disk_mb}M"

        self.logger.debug(f"k8s pod resources: {container.resources.requests}")

        # capabilities
        if job.needs_singularity and self.workflow.deployment_settings.use_singularity:
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

        pod = self._kubernetes_retry(
            lambda: self.kubeapi.create_namespaced_pod(self.namespace, body)
        )

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
        import kubernetes

        for j in active_jobs:
            async with self.status_rate_limiter:
                self.logger.debug(f"Checking status for pod {j.external_jobid}")
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
                except WorkflowError as e:
                    self.print_job_error(j, msg=str(e))
                    self.report_job_error(j.job)
                    continue

                if res is None:
                    msg = (
                        "Unknown pod {jobid}. " "Has the pod been deleted " "manually?"
                    ).format(jobid=j.external_jobid)
                    self.print_job_error(j, msg=msg)
                    self.report_job_error(j.job)
                elif res.status.phase == "Failed":
                    msg = (
                        "For details, please issue:\n"
                        "kubectl describe pod {jobid}\n"
                        "kubectl logs {jobid}"
                    ).format(jobid=j.external_jobid)
                    # failed
                    self.print_job_error(j, msg=msg)
                    self.report_job_error(j.job)
                elif res.status.phase == "Succeeded":
                    # finished
                    self.report_job_success(j.job)

                    self._kubernetes_retry(
                        lambda: self.safe_delete_pod(j.external_jobid, ignore_not_found=True)
                    )
                else:
                    # still active
                    yield j

    def cancel_jobs(self, active_jobs: List[SubmittedJobInfo]):
        # Cancel all active jobs.
        # This method is called when Snakemake is interrupted.

        for j in active_jobs:
            self._kubernetes_retry(
                lambda: self.safe_delete_pod(j.external_jobid, ignore_not_found=True)
            )

    def shutdown(self):
        self.unregister_secret()
        super().shutdown()

    def get_job_exec_prefix(self, job: ExecutorJobInterface):
        return "cp -rf /source/. ."

    def register_secret(self):
        import kubernetes.client

        secret = kubernetes.client.V1Secret()
        secret.metadata = kubernetes.client.V1ObjectMeta()
        # create a random uuid
        secret.metadata.name = self.run_namespace
        secret.type = "Opaque"
        secret.data = {}
        for i, f in enumerate(self.dag.get_sources()):
            if f.startswith(".."):
                self.logger.warning(
                    "Ignoring source file {}. Only files relative "
                    "to the working directory are allowed.".format(f)
                )
                continue

            # The kubernetes API can't create secret files larger than 1MB.
            source_file_size = os.path.getsize(f)
            max_file_size = 1048576
            if source_file_size > max_file_size:
                self.logger.warning(
                    "Skipping the source file {f}. Its size {source_file_size} exceeds "
                    "the maximum file size (1MB) that can be passed "
                    "from host to kubernetes.".format(
                        f=f, source_file_size=source_file_size
                    )
                )
                continue

            with open(f, "br") as content:
                key = f"f{i}"

                # Some files are smaller than 1MB, but grows larger after being
                # base64 encoded.
                # We should exclude them as well, otherwise Kubernetes APIs will
                # complain.
                encoded_contents = base64.b64encode(content.read()).decode()
                encoded_size = len(encoded_contents)
                if encoded_size > 1048576:
                    self.logger.warning(
                        "Skipping the source file {f} for secret key {key}. "
                        "Its base64 encoded size {encoded_size} exceeds "
                        "the maximum file size (1MB) that can be passed "
                        "from host to kubernetes.".format(
                            f=f,
                            key=key,
                            encoded_size=encoded_size,
                        )
                    )
                    continue

                self.secret_files[key] = f
                secret.data[key] = encoded_contents

        for e in self.envvars:
            try:
                key = e.lower()
                secret.data[key] = base64.b64encode(os.environ[e].encode()).decode()
                self.secret_envvars[key] = e
            except KeyError:
                continue

        # Test if the total size of the configMap exceeds 1MB
        config_map_size = sum(
            [len(base64.b64decode(v)) for k, v in secret.data.items()]
        )
        if config_map_size > 1048576:
            self.logger.warning(
                "The total size of the included files and other Kubernetes secrets "
                "is {}, exceeding the 1MB limit.\n".format(config_map_size)
            )
            self.logger.warning(
                "The following are the largest files. Consider removing some of them "
                "(you need remove at least {} bytes):".format(config_map_size - 1048576)
            )

            entry_sizes = {
                self.secret_files[k]: len(base64.b64decode(v))
                for k, v in secret.data.items()
                if k in self.secret_files
            }
            for k, v in sorted(entry_sizes.items(), key=lambda item: item[1])[:-6:-1]:
                self.logger.warning(f"  * File: {k}, original size: {v}")

            raise WorkflowError("ConfigMap too large")

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

    # In rare cases, deleting a pod may raise 404 NotFound error.
    def safe_delete_pod(self, jobid, ignore_not_found=True):
        import kubernetes.client

        body = kubernetes.client.V1DeleteOptions()
        try:
            self.kubeapi.delete_namespaced_pod(jobid, self.namespace, body=body)
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404 and ignore_not_found:
                # Can't find the pod. Maybe it's already been
                # destroyed. Proceed with a warning message.
                self.logger.warning(
                    "[WARNING] 404 not found when trying to delete the pod: {jobid}\n"
                    "[WARNING] Ignore this error\n".format(jobid=jobid)
                )
            else:
                raise e

    # Sometimes, certain k8s requests throw kubernetes.client.rest.ApiException
    # Solving this issue requires reauthentication, as _kubernetes_retry shows
    # However, reauthentication itself, under rare conditions, may also throw
    # errors such as:
    #   kubernetes.client.exceptions.ApiException: (409), Reason: Conflict
    #
    # This error doesn't mean anything wrong with the k8s cluster, and users can safely
    # ignore it.
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
                        " that the k8 cluster master is reachable!",
                    )


UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/snakemake/snakemake-executor-plugin-kubernetes",
)


def get_uuid(name):
    return uuid.uuid5(UUID_NAMESPACE, name)
