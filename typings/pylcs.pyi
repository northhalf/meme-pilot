"""pylcs 类型存根文件。

为 Pylance 提供 pylcs C++ 扩展模块的类型信息。
"""

def lcs_sequence_length(s1: str, s2: str) -> int:
    """计算两个字符串的最长公共子序列长度。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        最长公共子序列的长度。
    """
    ...

def lcs_string_length(s1: str, s2: str) -> int:
    """计算两个字符串的最长公共子串长度。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        最长公共子串的长度。
    """
    ...

def lcs_sequence_of_list(s1: str, s2_list: list[str]) -> list[int]:
    """计算一个字符串与多个字符串的最长公共子序列长度。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的最长公共子序列长度列表。
    """
    ...

def lcs_string_of_list(s1: str, s2_list: list[str]) -> list[int]:
    """计算一个字符串与多个字符串的最长公共子串长度。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的最长公共子串长度列表。
    """
    ...

def lcs(s1: str, s2: str) -> int:
    """计算两个字符串的最长公共子序列长度（lcs_sequence_length 的别名）。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        最长公共子序列的长度。
    """
    ...

def lcs2(s1: str, s2: str) -> int:
    """计算两个字符串的最长公共子串长度（lcs_string_length 的别名）。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        最长公共子串的长度。
    """
    ...

def lcs_of_list(s1: str, s2_list: list[str]) -> list[int]:
    """计算一个字符串与多个字符串的最长公共子序列长度（lcs_sequence_of_list 的别名）。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的最长公共子序列长度列表。
    """
    ...

def lcs2_of_list(s1: str, s2_list: list[str]) -> list[int]:
    """计算一个字符串与多个字符串的最长公共子串长度（lcs_string_of_list 的别名）。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的最长公共子串长度列表。
    """
    ...

def edit_distance(
    s1: str, s2: str, weight: dict[str, dict[str, float]] | None = None
) -> float:
    """计算两个字符串的编辑距离。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。
        weight: 可选的权重字典，用于自定义插入、删除、替换的代价。

    Returns:
        编辑距离。
    """
    ...

def edit_distance_of_list(s1: str, s2_list: list[str]) -> list[float]:
    """计算一个字符串与多个字符串的编辑距离。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的编辑距离列表。
    """
    ...

def levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的 Levenshtein 距离。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        Levenshtein 距离。
    """
    ...

def levenshtein_distance_of_list(s1: str, s2_list: list[str]) -> list[int]:
    """计算一个字符串与多个字符串的 Levenshtein 距离。

    Args:
        s1: 第一个字符串。
        s2_list: 第二个字符串列表。

    Returns:
        每个字符串与 s1 的 Levenshtein 距离列表。
    """
    ...

def lcs_sequence_idx(s1: str, s2: str) -> list[int]:
    """计算两个字符串的最长公共子序列的索引。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        与 s1 等长的列表，每个元素是 s2 中匹配字符的索引，-1 表示不匹配。
    """
    ...

def lcs_string_idx(s1: str, s2: str) -> list[int]:
    """计算两个字符串的最长公共子串的索引。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        与 s1 等长的列表，每个元素是 s2 中匹配字符的索引，-1 表示不匹配。
    """
    ...

def edit_distance_idx(s1: str, s2: str) -> list[int]:
    """计算两个字符串的编辑距离的索引。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        与 s1 等长的列表，每个元素是 s2 中匹配字符的索引，-1 表示不匹配。
    """
    ...

def levenshtein_distance_idx(s1: str, s2: str) -> list[int]:
    """计算两个字符串的 Levenshtein 距离的索引。

    Args:
        s1: 第一个字符串。
        s2: 第二个字符串。

    Returns:
        与 s1 等长的列表，每个元素是 s2 中匹配字符的索引，-1 表示不匹配。
    """
    ...
