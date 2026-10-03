"""Leaf module for node exceptions (no imports, so app.secrets can use them without a cycle)."""


class NodeError(Exception):
    pass


class RetryableNodeError(NodeError):
    pass
