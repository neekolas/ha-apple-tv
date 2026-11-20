// Pages can handle updates with tv.on or listen for ordinary message events.
(() => {
  if (window.tv) return;
  let state = null;
  const listeners = new Set();
  const report = (type) => {
    if (window.parent !== window) parent.postMessage({atvBridge: type, state}, "*");
  };
  window.tv = {
    on(type, handler) {
      if (type === "update") listeners.add(handler);
      return () => listeners.delete(handler);
    },
    ready() { report("ready"); },
    get state() { return state; },
    set state(value) { state = value; report("state"); }
  };
  addEventListener("message", event => {
    if (event.source !== window.parent && event.source !== window) return;
    for (const listener of listeners) listener(event.data);
    queueMicrotask(() => event.ports[0]?.postMessage({state}));
  });
})();
