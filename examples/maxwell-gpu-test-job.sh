#!/usr/bin/env bash

set -eu

echo "maxwell-rest-cli GPU smoke test"
hostname
date -u

echo "--- SLURM allocation ---"
echo "SLURM_JOB_ID=${SLURM_JOB_ID:-}"
echo "SLURM_JOB_PARTITION=${SLURM_JOB_PARTITION:-}"
echo "SLURM_JOB_GPUS=${SLURM_JOB_GPUS:-}"
echo "SLURM_GPUS_ON_NODE=${SLURM_GPUS_ON_NODE:-}"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"

echo "--- nvidia-smi -L ---"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L
  echo "--- nvidia-smi ---"
  nvidia-smi
else
  echo "nvidia-smi not found on PATH; check partition/gres or module load"
fi

sleep 10
echo "done"
