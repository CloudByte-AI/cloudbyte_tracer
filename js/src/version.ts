declare const __SDK_VERSION__: string;
export const version = typeof __SDK_VERSION__ === 'string' ? __SDK_VERSION__ : 'development';
