export function createApi({fetch, onResultsChanged}) {
  async function api(url, options = {}) {
    const config = {...options};
    if (config.json !== undefined) { config.headers = {'Content-Type': 'application/json', ...(config.headers || {})}; config.body = JSON.stringify(config.json); delete config.json; }
    const response = await fetch(url, config); const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok) {
      const error = new Error(data?.error || data?.detail || `请求失败 (${response.status})`);
      error.status = response.status; error.code = data?.code;
      throw error;
    }
    if (config.method && config.method !== 'GET' && url.startsWith('/api/results')) onResultsChanged();
    return data;
  }

  return api;
}
