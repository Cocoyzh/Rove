"""Rove 基准评测适配层。

当前支持 SWE-Bench（docs/swe-bench.md）：把数据集实例转换为 Rove 任务、
运行后提取 patch、聚合为官方 predictions JSONL。设计为离线可测——
依赖 HF 数据集与真实 LLM 的联网路径由 run_bench 在评测机上执行。
"""
