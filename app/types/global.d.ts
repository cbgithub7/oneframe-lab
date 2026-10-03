// The two functions the preload exposes on window (app/preload/preload.cjs).
export {};

declare global {
  /** A failure as the page receives it: a kind and reason oneframe/errors.py declares. */
  interface OneframeFailure {
    kind: string;
    reason?: string;
    message: string;
    next?: string;
    retry: boolean;
    problems?: unknown[];
  }
  interface OneframeApi {
    /** Rejects with a OneframeFailure. */
    request(method: string, params?: Record<string, unknown>): Promise<any>;
    onEvent(callback: (event: any) => void): () => void;
  }
  interface Window {
    oneframe: OneframeApi;
  }
}
