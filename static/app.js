import {browserEnvironment, createApplication} from './modules/application.mjs?v=20260920-progress-detail';

createApplication(browserEnvironment(window, {ReceiptImport, ReceiptQueue, ReceiptWorkbench})).initialize();
