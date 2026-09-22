/** Each controller owns one state bag. Other controllers receive only the
 * bags needed for cross-page workflows; request generations stay with their owner. */
export function createState() {
  return {
    records: {batchFilter: '', records: [], recordPage: 1, recordPageSize: 100, selected: new Set()},
    review: {reviewDeferred: new Set(), reviewDirty: false, reviewSaving: false, reviewEditVersion: 0,
      current: null, artifactTab: 'date', reviewQueue: [], reviewTask: ''},
    imports: {batchRunning: false, taskId: '', ocrBackend: '', sealRecognitionMode: 'local'},
    // `workFilter` remains for compatibility with queued actions and older
    // persisted state. `workFilters` is null until the user makes a choice,
    // then contains the selected status cards.
    progress: {workFilter: 'review', workFilters: null},
    report: {},
    navigation: {},
    results: {},
  };
}
