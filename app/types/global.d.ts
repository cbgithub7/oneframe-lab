// The two functions the preload exposes on window (app/preload/preload.cjs).
export {};

declare global {
  interface OneframeApi {
    request(method: string, params?: Record<string, unknown>): Promise<any>;
    onEvent(callback: (event: any) => void): () => void;
  }
  interface Window {
    oneframe: OneframeApi;
  }
}
