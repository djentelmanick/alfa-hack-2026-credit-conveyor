import { USE_MOCKS } from "./client.js";
import { realApi } from "./real.js";
import { mockApi } from "./mock/api.js";

/** Единая точка доступа к API. VITE_USE_MOCKS=false — ходим в настоящий бэкенд. */
export const api = USE_MOCKS ? mockApi : realApi;

export { ApiError, fieldErrors, USE_MOCKS } from "./client.js";
