import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/** MSW server для unit/integration тестов в Node. */
export const server = setupServer(...handlers);
