"""Offline diagnostics must not acquire the adapter's HTTP mutex twice."""
from pathlib import Path
import subprocess
import sys
import textwrap


def test_probe_can_call_real_adapter_with_injected_transport_without_deadlock():
    script = textwrap.dedent('''
        from types import SimpleNamespace
        from receipt_ocr.danzhengtong import Client, Settings
        from tools.analysis.operations.probe_danzhengtong_seal import record_request
        calls = []
        def request(method, url, **kwargs):
            calls.append((method, url))
            return 'offline-response'
        client = Client(Settings(), transport=SimpleNamespace(request=request))
        report = {'requests': []}
        result = record_request(report, 'query', lambda:
            client._transport_request('GET', 'https://offline.example/result'))
        assert result == 'offline-response' and len(calls) == 1
        assert report['requests'][0]['status'] == 'succeeded'
        print('completed')
    ''')
    result = subprocess.run([sys.executable, '-c', script],
                            cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'completed'
