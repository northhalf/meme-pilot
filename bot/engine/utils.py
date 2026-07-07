"""engine 包公共工具函数。"""

import math

__all__ = ["vector_norm"]


def vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))
