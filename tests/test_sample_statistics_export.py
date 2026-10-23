from io import BytesIO

from openpyxl import load_workbook
import pytest

from receipt_ocr.web import sample_statistics_export as exports
from test_sample_center import samples


def sheet(response):
    assert response.status_code == 200
    assert response.mimetype == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    assert 'attachment;' in response.headers['Content-Disposition']
    return load_workbook(BytesIO(response.data)).active


def details(worksheet):
    rows = list(worksheet.values)
    header = next(index for index, row in enumerate(rows) if row[0] == '样单编号')
    return rows[header], rows[header + 1:]


def test_download_company_date_and_batch_scope_without_touching_production(samples):
    ctx, client = samples
    worksheet = sheet(client.get('/api/samples/statistics/export?company=甲公司&tab=date'))
    assert worksheet.title == '日期结果'
    header, rows = details(worksheet)
    assert {row[1] for row in rows} == {'one.png', 'two.png'}
    assert header[4] == '标注日期'
    assert all(row[5] == '未测试' for row in rows)
    assert worksheet.freeze_panes and worksheet.auto_filter.ref
    task = client.post('/api/samples/run', json={'filenames': ['one.png']}).json['task_id']
    scoped = sheet(client.get('/api/samples/statistics/export', query_string={'task_id': task}))
    assert [row[1] for row in details(scoped)[1]] == ['one.png']
    assert ctx.database.list_results() == [] and ctx.database.list_tasks() == []


def test_download_all_matching_rows_and_keep_untrusted_text_as_text(samples, monkeypatch):
    _, client = samples
    def c(state='正确', value='公司', decision='匹配'):
        return dict(state=state, value=value, decision=decision, note='')
    rows = [dict(filename=f'{7000000000 + index}.jpg', company='客户甲', job_status='succeeded',
                 requirement='=1+1', expected_seal='匹配', expected_date=None, date=c(),
                 seals={'qingtong_template': c('错误', '=HYPERLINK("bad")', '不匹配'),
                        'qingtong_ocr': c(), 'local:paddle_v6': c()}) for index in range(13)]
    data = dict(items=rows, columns=[{'key': 'qingtong_template', 'label': '清瞳模板'}], batches=[])
    monkeypatch.setattr(exports, 'sample_statistics', lambda *args: data)
    worksheet = sheet(client.get('/api/samples/statistics/export?filter=remote'))
    _, result = details(worksheet)
    assert len(result) == 13  # All pages, not just the visible five rows.
    assert result[0][0] == '7000000000'
    assert worksheet['B7'].value == 0 and worksheet['B7'].number_format == '0.0%'
    for row in worksheet:
        for item in row:
            assert item.data_type != 'f'
    assert result[0][4] == '=1+1'
    assert result[0][7] == '=HYPERLINK("bad")'
    assert len(details(sheet(client.get('/api/samples/statistics/export?filter=local')))[1]) == 13
    assert details(sheet(client.get('/api/samples/statistics/export?filter=missing')))[1] == []


@pytest.mark.parametrize('query,status', [
    ('task_id=unknown', 404), ('tab=unknown', 400), ('filter=unknown', 400),
    ('tab=date&filter=local', 400),
])
def test_invalid_export_scope_reports_error(samples, query, status):
    _, client = samples
    response = client.get('/api/samples/statistics/export?' + query)
    assert response.status_code == status
    assert response.json['error']
