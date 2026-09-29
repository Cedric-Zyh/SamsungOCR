import {workFilterValues} from './overview.mjs';

export function createWorkFilterState(progressState) {
  function activeWorkFilters() {
    // A null value means the initial/default scalar filter is still in use;
    // an empty array is an intentional "全部状态" selection.
    return workFilterValues(progressState.workFilters === null || progressState.workFilters === undefined
      ? progressState.workFilter : progressState.workFilters);
  }
  function setWorkFilters(filters) {
    const values = workFilterValues(filters);
    progressState.workFilters = values;
    progressState.workFilter = values.length === 0 ? 'all'
      : values.length === 1 ? values[0]
      : values.length === 2 && values.includes('ready') && values.includes('running') ? 'processing' : 'multi';
  }
  function setWorkFilter(filter) {
    // Programmatic queue actions select a logical filter and clear any manual
    // multi-selection that was active before the action.
    progressState.workFilters = null;
    progressState.workFilter = filter;
  }
  function hasOnlyWorkFilter(filter) {
    const values = activeWorkFilters();
    const expected = workFilterValues(filter);
    return values.length === expected.length && expected.every(value => values.includes(value));
  }
  return {activeWorkFilters, setWorkFilters, setWorkFilter, hasOnlyWorkFilter};
}
