export class ApiError extends Error {
  constructor(message, {status = 0, code = '', details = null, url = ''} = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.url = url;
  }
}

export function createApi({fetch, onResultsChanged = () => {}}) {
  async function api(url, options = {}) {
    const config = {...options};
    if (config.json !== undefined) { config.headers = {'Content-Type': 'application/json', ...(config.headers || {})}; config.body = JSON.stringify(config.json); delete config.json; }
    const response = await fetch(url, config); const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok) {
      throw new ApiError(data?.error || data?.detail || `请求失败 (${response.status})`, {
        status: response.status, code: data?.code, details: data, url
      });
    }
    if (String(config.method || 'GET').toUpperCase() !== 'GET' && url.startsWith('/api/results')) onResultsChanged();
    return data;
  }

  return api;
}

function query(path, params = {}) {
  if (typeof params === 'string') return params ? `${path}?${params}` : path;
  if (params instanceof URLSearchParams) {
    const suffix = params.toString();
    return suffix ? `${path}?${suffix}` : path;
  }
  const search = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ''));
  const suffix = search.toString();
  return suffix ? `${path}?${suffix}` : path;
}

/** Endpoint and transport details live here; features supply domain inputs. */
export function createApiClients(api) {
  return {
    samples: {
      list: () => api('/api/samples'),
      run: filenames => api('/api/samples/run', {method: 'POST', json: {filenames}}),
      classify: scope => api('/api/samples/classify', {method: 'POST', ...(scope ? {json: scope} : {})}),
      classifyRequirements: (company, scope = {}) => api('/api/samples/classify-requirements', {method: 'POST', json: {company, ...scope}}),
      importFiles: body => api('/api/samples/import', {method: 'POST', body}),
      remove: filenames => api('/api/samples/delete', {method: 'POST', json: {filenames}}),
      history: () => api('/api/samples/history'),
      statistics: taskId => api(query('/api/samples/statistics', {task_id: taskId})),
      statisticsExportUrl: params => query('/api/samples/statistics/export', params),
      saveCompanyMark: payload => api('/api/samples/company-mark', {method: 'POST', json: payload}),
      get: id => api(`/api/samples/results/${encodeURIComponent(id)}`),
      reviewHistory: id => api(`/api/samples/results/${encodeURIComponent(id)}/review-history`),
      groundTruth: id => api(`/api/samples/results/${encodeURIComponent(id)}/ground-truth`),
      saveAnnotations: (id, payload) => api(`/api/samples/results/${encodeURIComponent(id)}/annotations`, {method: 'PATCH', json: payload}),
      saveStandard: (id, payload) => api(`/api/samples/results/${encodeURIComponent(id)}/seal-standard`, {method:'POST', json:payload}),
    },
    sealStandards: {
      list: search => api(query('/api/seal-standards', {search})),
      sources: id => api(`/api/seal-standards/${encodeURIComponent(id)}/sources`),
      save: (id, payload) => api(`/api/results/${encodeURIComponent(id)}/seal-standard`, {method:'POST', json:payload}),
    },
    imports: {
      dates: month => api(query('/api/import-dates', {month})),
      createTask: payload => api('/api/tasks', {method: 'POST', json: payload}),
    },
    queue: {
      list: (importDate, options) => api(query('/api/queue', {import_date: importDate, include_related: 1}), options),
      control: options => api('/api/queue/control', options),
      manage: (ids, action) => api('/api/jobs/manage', {method: 'POST', json: {ids, action}}),
      start: ids => api('/api/jobs/start', {method: 'POST', json: {ids}}),
      cancel: ids => api('/api/jobs/cancel', {method: 'POST', json: {ids}}),
      remove: ids => api('/api/jobs/delete', {method: 'POST', json: {ids}}),
      cancelOne: id => api(`/api/jobs/${encodeURIComponent(id)}/cancel`, {method: 'POST'}),
      retry: id => api(`/api/jobs/${encodeURIComponent(id)}/retry`, {method: 'POST'}),
      upload: (id, body) => api(`/api/jobs/${encodeURIComponent(id)}/upload`, {method: 'POST', body}),
    },
    records: {
      list: (params, options) => api(query('/api/results', params), options),
      daily: (importDate, options) => api(query('/api/daily-results', {import_date: importDate, include_queue: 1}), options),
      get: (id, options) => api(`/api/results/${encodeURIComponent(id)}`, options),
      review: (id, payload) => api(`/api/results/${encodeURIComponent(id)}/review`, {method: 'PATCH', json: payload}),
      history: (id, options) => api(`/api/results/${encodeURIComponent(id)}/review-history`, options),
      groundTruth: (id, options) => api(`/api/results/${encodeURIComponent(id)}/ground-truth`, options),
      processHistory: params => api(query('/api/process-history', params)),
      reviewQueue: ({daily, params}, options) => api(query(daily ? '/api/daily-results' : '/api/results', params), options),
      bulkReview: payload => api('/api/results/bulk-review', {method: 'POST', json: payload}),
      bulkRetry: payload => api('/api/results/bulk-retry', {method: 'POST', json: payload}),
      retry: (id, payload) => api(`/api/results/${encodeURIComponent(id)}/retry`, {method: 'POST', json: payload}),
      remove: ids => api('/api/results/delete', {method: 'POST', json: {ids}}),
    },
    report: {
      load: options => api('/api/report', options),
    },
    settings: {
      update: payload => api('/api/settings', {method: 'PATCH', json: payload}),
      recognition: options => api('/api/settings/recognition', options),
      updateRecognition: payload => api('/api/settings/recognition', {method: 'PATCH', json: payload}),
    },
  };
}
