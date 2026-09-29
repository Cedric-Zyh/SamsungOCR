"""Elliptical stamp normalization and orientation preparation."""

def prepare_ellipse_stamp(*args, **kwargs):
    from . import orientation
    return orientation.prepare_ellipse_stamp(*args, **kwargs)

__all__ = ["prepare_ellipse_stamp"]
