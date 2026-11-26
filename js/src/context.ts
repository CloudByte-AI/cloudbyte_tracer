import { AsyncLocalStorage } from 'node:async_hooks';
import type { Span } from './core.js';
export const activeSpan = new AsyncLocalStorage<Span>();
