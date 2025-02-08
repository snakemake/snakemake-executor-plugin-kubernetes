# GPU Scheduling with the Snakemake Kubernetes Executor on Google Kubernetes Engine

Below are instructions on how to use the new GPU support in the Snakemake Kubernetes executor plugin. This feature allows you to specify the GPU vendor—either NVIDIA or AMD—in your Snakemake resource definitions. The plugin then configures the pod with the appropriate node selectors, tolerations, and resource requests so that GPU-accelerated jobs are scheduled on the correct nodes in GKE.

---

## Overview

The GPU support in the plugin enables you to:
- Specify the number of GPUs required for a job.
- Indicate the GPU vendor via a new key (e.g., `gpu_manufacturer="nvidia"` or `"amd"`).
- Automatically set node selectors and tolerations based on the GPU vendor.

With these changes, your job will be scheduled only on GPU-enabled nodes, and the GKE autoscaler will be able to provision GPU nodes as needed.

---

## Prerequisites

### GKE Cluster and GPU-Enabled Node Pool
- **GKE Environment:**  
  Ensure you have a functioning GKE cluster with autoscaling enabled if required.
  
- **GPU-Enabled Node Pool:**  
  - GKE automatically labels and taints these nodes via the official device plugins.
    - NVIDIA: nvidia.com/gpu
    - AMD: amd.com/gpu
  - Validate these taints match your configuration.

## Resource Definition
```
resources:
    gpu=1,
    gpu_manufacturer="nvidia",  # Allowed values: "nvidia" or "amd"
    machine_type="n1-standard-16" 
```
- `gpu`: the number of GPUs to be requested
    - note: This currently only works for multiple GPUs within a single node. The current implementation cannot handle requesting multiple nodes with GPUs.
- `gpu_manufacturer`: Specifies the GPU vendor. Use "nvidia" for NVIDIA GPUs or "amd" for AMD GPUs.
- `machine_type`: machine type for the GPU enabled node. This is NOT the GPU type.
    - https://cloud.google.com/compute/docs/general-purpose-machines
- You can define any of the other Snakemake resource types here as normal.

## Debugging Tips: 
- Failing to schedule on the GPU node
  - Inspect your GPU node and validate your tolerations match the default ones defined above.   
- Failing to see performance boost despite successful scheduling
  - Verify you are executing the Snakemake workflow in an environment with the correct GPU accelerated libraries
  - The [NVIDIA/CUDA Docker container](https://hub.docker.com/r/nvidia/cuda) is recommended.
  - You can also run `nvidia-smi` within the Snakemake rule execution to validate and monitor GPU usage. 
