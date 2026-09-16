import {browserEnvironment, createApplication} from './modules/application.mjs?v=20260916-process-history';

createApplication(browserEnvironment(window, {ReceiptImport, ReceiptQueue, ReceiptWorkbench})).initialize();
