// The two functions the preload exposes on window (app/preload/preload.cjs), typed from the
// contract (app/protocol.d.ts, generated from contracts/protocol.json): the page's view of it.
import type { Page } from "../protocol.js";

export {};

declare global {
  /** A failure as the page receives it: a kind and reason oneframe/errors.py declares. */
  type OneframeFailure = Page.Failure;
  interface OneframeApi {
    /** Resolves with the method's answer; rejects with a OneframeFailure. */
    request<M extends Page.MethodName>(method: M, params?: Page.Params<M>): Promise<Page.Answer<M>>;
    onEvent(callback: (event: Page.Event) => void): () => void;
  }
  interface Window {
    oneframe: OneframeApi;
  }
}
