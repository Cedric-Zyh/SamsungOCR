"""Reusable source-image thumbnails, isolated from original sample files."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from PIL import Image, ImageOps


class SampleThumbnails:
    VERSION = 'jpeg-480-square-v1'
    # Share bounded stripes across cache instances, so one process cannot
    # replace a thumbnail while another instance still has it open on Windows.
    _locks = [threading.Lock() for _ in range(32)]

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _signature(self, source):
        stat = source.stat()
        return (str(source), stat.st_mtime_ns, stat.st_size, self.VERSION)

    def get(self, source):
        source = Path(source).resolve()
        source_key = hashlib.sha256(str(source).encode()).hexdigest()
        for _ in range(3):
            signature = self._signature(source)
            key = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
            target = self.directory / f'{source_key}-{key}.jpg'
            with self._locks[int(source_key[:8], 16) % len(self._locks)]:
                if signature != self._signature(source):
                    continue
                if target.is_file():
                    try:
                        data = target.read_bytes()
                    except FileNotFoundError:
                        # A second process may have retired an older fingerprint.
                        continue
                    return data, key
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.directory, prefix=f'.{key}-',
                                                     suffix='.tmp', delete=False) as output:
                        temporary = Path(output.name)
                        with Image.open(source) as image:
                            # A square bound permits reducing JPEGs before EXIF rotation,
                            # letting Pillow decode a smaller image where supported.
                            image.thumbnail((480, 480))
                            with ImageOps.exif_transpose(image) as oriented:
                                with oriented.convert('RGB') as rgb:
                                    rgb.save(output, 'JPEG', quality=80)
                    if signature != self._signature(source):
                        continue
                    # Other processes can generate the same key safely: readers
                    # see either a complete old file or a complete replacement.
                    try:
                        os.replace(temporary, target)
                    except PermissionError:
                        # A different process may have published this same key
                        # and still be reading it. Reuse that complete file;
                        # permission failures without a winner remain errors.
                        if not target.is_file():
                            raise
                        if signature != self._signature(source):
                            continue
                    try:
                        data = target.read_bytes()
                    except FileNotFoundError:
                        continue
                    self._prune(source_key, target)
                    return data, key
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        raise FileNotFoundError('样单图片在生成缩略图时发生变化')

    def _prune(self, source_key, current):
        # Responses already hold the small JPEG bytes, so retiring an old
        # fingerprint cannot break another response that is still sending it.
        for path in self.directory.glob(f'{source_key}-*.jpg'):
            if path != current:
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    # Windows may still have another process reading this file.
                    pass
