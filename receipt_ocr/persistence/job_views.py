"""Pure public projections of stored remote jobs, independent of queue workers."""
import json


PENDING = ('queued', 'submitting', 'waiting', 'received')
LABELS = {'queued': '等待提交', 'submitting': '正在提交', 'waiting': '等待结果',
          'received': '结果已取回', 'applied': '已完成', 'failed': '处理失败',
          'unknown': '提交状态待确认', 'conflict': '结果已取回，待确认', 'cancelled': '已停止跟踪'}
PUBLIC_KEYS = ('id', 'status', 'created_at', 'updated_at', 'submitted_at', 'last_query_at',
               'query_count', 'error_message', 'req_uuid', 'result_id')
PUBLIC_COLUMNS = ','.join((*PUBLIC_KEYS, 'job_id', 'lease_until', 'seal_requests_json'))


def remote_public(row):
    if row is None:
        return None
    value = {**{key: row[key] for key in PUBLIC_KEYS},
            'label': LABELS.get(row['status'], row['status']), 'pending': row['status'] in PENDING,
            'querying': bool(row['lease_until'] and row['status'] == 'waiting'),
            'queryable': bool(row['req_uuid'] and row['status'] in {'queued', 'waiting'})}
    if 'seal_requests_json' in row.keys():
        state = json.loads(row['seal_requests_json'] or '{}')
        children = state.get('requests', [])
        page_status = (state.get('page') or {}).get('status')
        value['queryable'] = bool(row['req_uuid'] and row['status'] in {'queued', 'waiting'}
                                  and page_status in {None, 'waiting'}) or any(
            child.get('req_uuid') and child.get('status') == 'waiting' for child in children)
        value['seal_requests'] = [{key: child.get(key) for key in ('index', 'status', 'req_uuid', 'error')}
                                  for child in children]
        value['req_uuid'] = value['req_uuid'] or next((child.get('req_uuid') for child in children if child.get('req_uuid')), '')
    return value
