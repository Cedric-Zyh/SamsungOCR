"""Excel download for the sample statistics view, including every filtered row."""
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .sample_statistics import sample_statistics


BAD = {'错误', '未识别', '失败'}
SCORED = BAD | {'正确'}
FILTERS = {'': '全部', 'errors': '错误', 'remote': '清瞳两路不一致',
           'local': '清瞳与本地不一致', 'missing': '未识别 / 失败'}
NOTE = '未识别、失败计入正确率分母；未测试、未运行、测试中、待标注不计入。印章按人工标注的应匹配 / 应不匹配评估判定，不代表逐字转写准确率。'


def cell(row, key):
    return row['date'] if key == 'date' else row['seals'].get(key, {
        'state': '未运行' if row['job_status'] else '未测试'})


def disagrees(cells):
    return len({c.get('decision') for c in cells if c['state'] in SCORED
                and c['state'] not in {'未识别', '失败'}
                and c.get('decision') in {'匹配', '不匹配', '部分匹配'}}) > 1


def matches(row, tab, selected):
    cells = [row['date']] if tab == 'date' else list(row['seals'].values())
    if selected == 'errors':
        return any(c['state'] in BAD for c in cells)
    if selected == 'missing':
        return any(c['state'] in {'未识别', '失败'} for c in cells)
    if selected == 'remote':
        return disagrees([cell(row, 'qingtong_template'), cell(row, 'qingtong_ocr')])
    if selected == 'local':
        return any(disagrees([cell(row, key), c])
                   for key in ('qingtong_template', 'qingtong_ocr')
                   for method, c in row['seals'].items() if method.startswith('local:'))
    return True


def export_statistics(center, *, company='', task_id='', tab='seal', selected=''):
    if tab not in {'seal', 'date'} or selected not in FILTERS or (tab == 'date' and selected in {'remote', 'local'}):
        raise ValueError('统计筛选条件无效')
    data = sample_statistics(center, task_id)
    rows = [row for row in data['items'] if (not company or row['company'] == company)
            and matches(row, tab, selected)]
    columns = data['columns'] if tab == 'seal' else [{'key': 'date', 'label': '识别日期'}]
    batch = next((batch for batch in data['batches'] if batch['id'] == task_id), None)
    scope = f"{batch['created_at']} · {batch['total']} 张" if batch else '每张样单最近一次测试'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '印章结果' if tab == 'seal' else '日期结果'

    def append(values):
        # OCR and file names are text even when they start with an Excel formula marker.
        sheet.append(values)
        for item in sheet[sheet.max_row]:
            if isinstance(item.value, str):
                item.value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', item.value)
                item.data_type = 's'
            item.alignment = Alignment(vertical='top', wrap_text=True)

    append(['样单统计结果 · ' + sheet.title])
    append(['客户公司', company or '全部公司', '统计范围', scope])
    append(['筛选条件', FILTERS[selected], '导出样单数', len(rows)])
    append([NOTE])
    sheet.merge_cells(start_row=4, start_column=1, end_row=4, end_column=8)
    sheet.row_dimensions[4].height = 42
    append([])
    append(['识别通道', '正确率', '正确', '可评测', '错误', '未识别', '失败', '待标注', '未运行', '未测试', '测试中'])
    for column in columns:
        cells = [cell(row, column['key']) for row in rows]
        counts = {state: sum(c['state'] == state for c in cells)
                  for state in SCORED | {'待标注', '未运行', '未测试', '测试中'}}
        total = sum(counts[state] for state in SCORED)
        append([column['label'], counts['正确'] / total if total else None,
                counts['正确'], total, *[counts[state] for state in
                ('错误', '未识别', '失败', '待标注', '未运行', '未测试', '测试中')]])
        sheet.cell(sheet.max_row, 2).number_format = '0.0%'
    append([])
    header = ['样单编号', '文件名', '客户公司', '测试状态',
              *(['签章要求', '标注结论'] if tab == 'seal' else ['标注日期'])]
    for column in columns:
        header.extend(f"{column['label']} · {label}" for label in ('评估结果', '识别内容', '判定', '说明'))
    append(header)
    header_row = sheet.max_row
    states = {'succeeded': '已完成', 'failed': '运行失败', 'queued': '测试中',
              'running': '测试中', 'awaiting_upload': '测试中'}
    from .sample_library import sample_number
    for row in rows:
        values = [sample_number(row['filename']), row['filename'], row['company'],
                  states.get(row['job_status'], '未测试')]
        values.extend([row['requirement'], f"应{row['expected_seal']}" if row['expected_seal'] else '待标注']
                      if tab == 'seal' else ['待标注' if row['expected_date'] is None else row['expected_date'] or '空白'])
        for column in columns:
            c = cell(row, column['key'])
            values.extend(c.get(key, '') for key in ('state', 'value', 'decision', 'note'))
        append(values)
    for index in (6, header_row):
        for item in sheet[index]:
            item.fill = PatternFill('solid', fgColor='343D2E')
            item.font = Font(color='FFFFFF', bold=True)
        sheet.row_dimensions[index].height = 32
    sheet['A1'].font = Font(size=18, bold=True, color='343D2E')
    for index in range(1, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 24 if index < 3 else 34
    sheet.freeze_panes = f'D{header_row + 1}'
    sheet.auto_filter.ref = f'A{header_row}:{get_column_letter(len(header))}{sheet.max_row}'
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
