"""The journal wire format must not turn missing metadata into cache hits."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from receipt_ocr.runtime import execution


def record(major, usn):
    size, offset = {2: (64, 24), 3: (80, 40)}[major]
    data = bytearray(size)
    struct.pack_into('<IHH', data, 0, size, major, 0)
    struct.pack_into('<q', data, offset, usn)
    return bytes(data)


@pytest.mark.parametrize('major', [2, 3])
def test_windows_query_reads_journal_sequence_without_retaining_handle(tmp_path, monkeypatch, major):
    source = tmp_path / '中文回单.png'
    source.write_bytes(b'input')
    payload = record(major, 2**40 + 123)
    opened = []
    original_open = Path.open

    def tracked_open(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def query(handle, code, versions, version_size, output, capacity, returned, overlapped):
        assert code == 0x000900EB
        assert list(versions) == [2, 3] and version_size == 4
        assert capacity >= len(payload) and overlapped is None
        ctypes.memmove(output, payload, len(payload))
        ctypes.cast(returned, ctypes.POINTER(wintypes.DWORD))[0] = len(payload)
        return True

    monkeypatch.setattr(execution, 'open', lambda path, mode: tracked_open(Path(path), mode), raising=False)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(DeviceIoControl=query), raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: fd))
    assert execution._windows_file_usn(source) == 2**40 + 123
    assert len(opened) == 1 and opened[0].closed
    source.unlink()


@pytest.mark.parametrize('payload', [
    b'', b'\x00' * 8, struct.pack('<IHH', 64, 4, 0) + bytes(56),
    record(2, 1)[:31], record(3, 1)[:47], record(2, 0), record(3, -1),
])
def test_unavailable_or_unsupported_journal_records_disable_reuse(payload):
    with pytest.raises(OSError):
        execution._usn_from_record(payload)
