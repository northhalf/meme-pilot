"""encode_embedding / decode_embedding 工具函数测试。"""

import struct
import base64

import pytest

from bot.engine.index_manager import encode_embedding, decode_embedding


class TestEncodeEmbedding:
    """encode_embedding 编码函数测试。"""

    def test_returns_string(self) -> None:
        """返回 base64 字符串。"""
        result = encode_embedding([0.1, 0.2, 0.3])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_single_value(self) -> None:
        """单元素向量编码。"""
        result = encode_embedding([1.0])
        assert isinstance(result, str)

    def test_1024_dim(self) -> None:
        """1024 维向量编码，验证输出长度。"""
        emb = [float(i) for i in range(1024)]
        result = encode_embedding(emb)
        assert len(result) == 5464


class TestDecodeEmbedding:
    """decode_embedding 解码函数测试。"""

    def test_decodes_to_list_of_floats(self) -> None:
        """解码为浮点数列表。"""
        data = base64.b64encode(struct.pack("!3f", 0.1, 0.2, 0.3)).decode("ascii")
        result = decode_embedding(data)
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    def test_roundtrip(self) -> None:
        """编码再解码后与原始值一致（float32 精度）。"""
        original = [0.1, -0.5, 3.14159, 0.0, -1.0, 999.0]
        encoded = encode_embedding(original)
        decoded = decode_embedding(encoded)
        assert decoded == pytest.approx(original, abs=5e-6)

    def test_roundtrip_1024_dim(self) -> None:
        """1024 维向量编码解码 roundtrip 精度验证。"""
        original = [float(i * 0.1 - 51.2) for i in range(1024)]
        encoded = encode_embedding(original)
        decoded = decode_embedding(encoded)
        for a, b in zip(original, decoded):
            assert abs(a - b) < 5e-6
