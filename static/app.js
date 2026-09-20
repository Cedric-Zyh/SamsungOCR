import {browserEnvironment, createApplication} from './modules/application.mjs?v=20260920-view-review-mode';

createApplication(browserEnvironment(window, {ReceiptImport, ReceiptQueue, ReceiptWorkbench})).initialize();
