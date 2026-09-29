"""Round stamp orientation preparation."""

def prepare_round_stamp(*args, **kwargs):
    from . import orientation
    return orientation.prepare_round_stamp(*args, **kwargs)


def prepare_round_stamp_combined(*args, **kwargs):
    from . import orientation
    return orientation.prepare_round_stamp_combined(*args, **kwargs)


def prepare_round_stamp_doc_ori(*args, **kwargs):
    from . import orientation
    return orientation.prepare_round_stamp_doc_ori(*args, **kwargs)

__all__ = [
    "prepare_round_stamp",
    "prepare_round_stamp_combined",
    "prepare_round_stamp_doc_ori",
]
