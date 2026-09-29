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
    imports: {
      dates: month => api(query('/api/import-dates', {month})),
      createTask: payload => api('/api/tasks', {method: 'POST', json: payload}),
    },
    queue: {
      list: (importDate, options) => api(query('/api/queue', {import_date: importDate}), options),
      control: options => api('/api/queue/control', options),
      start: ids => api('/api/jobs/start', {method: 'POST', json: {ids}}),
      cancel: ids => api('/api/jobs/cancel', {method: 'POST', json: {ids}}),
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
    },
  };
}
