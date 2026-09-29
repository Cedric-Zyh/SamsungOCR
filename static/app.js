import {browserEnvironment, createApplication} from './modules/shell/application.mjs';
import {ReceiptImport} from './modules/core/import_files.mjs';
import {ReceiptQueue} from './modules/core/queue.mjs';
import {ReceiptWorkbench} from './modules/core/workbench.mjs';

createApplication(browserEnvironment(window), {ReceiptImport, ReceiptQueue, ReceiptWorkbench}).initialize();
