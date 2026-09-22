import {browserEnvironment, createApplication} from './modules/application.mjs?v=20260923-retention-days';

createApplication(browserEnvironment(window, {ReceiptImport, ReceiptQueue, ReceiptWorkbench})).initialize();
