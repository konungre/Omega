/** Omega v1 transport. No OpenClaw Gateway frames or credentials. */
export class OmegaAPIError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function* readSSE(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > 2_000_000)
        throw new Error("Activity frame exceeded the size limit");
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const frame = { event: "message", data: "", id: null };
        for (const line of block.split("\n")) {
          if (line.startsWith("id: ")) frame.id = Number(line.slice(4));
          if (line.startsWith("event: ")) frame.event = line.slice(7);
          if (line.startsWith("data: ")) frame.data += line.slice(6) + "\n";
        }
        if (frame.data) yield frame;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export class OmegaClient {
  constructor({
    onSnapshot,
    onEvent,
    onStatus,
    onPending,
    onError,
    fetcher = globalThis.fetch.bind(globalThis),
    storage = globalThis.sessionStorage,
  }) {
    Object.assign(this, {
      onSnapshot,
      onEvent,
      onStatus,
      onPending,
      onError,
      fetcher,
      storage,
    });
    this.token = "";
    this.cursor = 0;
    this.instance = null;
    this.controller = null;
    this.pending = new Map();
    this.pendingInstances = new Map();
    this.sending = new Map();
    try {
      const saved = JSON.parse(storage?.getItem("omega.pending") || "[]");
      for (const record of saved) {
        const envelope = record.envelope;
        if (
          envelope &&
          typeof envelope.request_id === "string" &&
          typeof envelope.command === "string" &&
          envelope.payload
        ) {
          this.pending.set(envelope.request_id, envelope);
          this.pendingInstances.set(envelope.request_id, record.instance_id);
        }
      }
    } catch {
      /* A corrupt local draft must not prevent connecting. */
    }
  }
  savePending() {
    try {
      this.storage?.setItem(
        "omega.pending",
        JSON.stringify(
          [...this.pending.values()].map((envelope) => ({
            envelope,
            instance_id: this.pendingInstances.get(envelope.request_id),
          })),
        ),
      );
    } catch {
      /* In-memory retries remain available. */
    }
    this.onPending?.([...this.pending.values()]);
  }
  async request(path, options = {}) {
    const response = await this.fetcher(`/api/v1${path}`, {
      ...options,
      headers: { Authorization: `Bearer ${this.token}`, ...options.headers },
      cache: "no-store",
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new OmegaAPIError(
        response.status,
        body.error?.code,
        body.error?.message || `Omega API returned ${response.status}`,
      );
    }
    return response;
  }
  async connect(token) {
    this.disconnect();
    this.token = token;
    const controller = new AbortController();
    this.controller = controller;
    this.onStatus("connecting");
    try {
      await this.snapshot(controller.signal);
      if (!controller.signal.aborted) void this.listen(controller);
    } catch (error) {
      if (!controller.signal.aborted) {
        this.onStatus("disconnected");
        throw error;
      }
    }
  }
  disconnect() {
    this.controller?.abort();
    this.controller = null;
    this.token = "";
    this.onStatus("disconnected");
  }
  async snapshot(signal) {
    const snapshot = await (await this.request("/snapshot", { signal })).json();
    this.onSnapshot(snapshot);
    this.cursor = snapshot.cursor;
    this.instance = snapshot.instance_id;
  }
  async listen(controller) {
    let attempt = 0;
    const signal = controller.signal;
    while (!signal.aborted) {
      try {
        const response = await this.request(
          `/events?after=${this.cursor}&instance_id=${encodeURIComponent(this.instance)}`,
          { signal },
        );
        attempt = 0;
        this.onStatus("connected");
        void this.retryPending().catch((error) => this.onError?.(error));
        for await (const frame of readSSE(response.body)) {
          if (signal.aborted) return;
          if (frame.event === "activity" && frame.id > this.cursor)
            this.onEvent(JSON.parse(frame.data));
          if (frame.id !== null && Number.isSafeInteger(frame.id))
            this.cursor = Math.max(this.cursor, frame.id);
        }
        throw new Error("Activity connection closed");
      } catch (error) {
        if (signal.aborted) return;
        if (error.status === 401 || error.status === 403) {
          this.onStatus("unauthorized");
          return;
        }
        this.onStatus("reconnecting");
        if (error.status === 409) {
          try {
            await this.snapshot(signal);
          } catch (snapshotError) {
            if (snapshotError.status === 401) {
              this.onStatus("unauthorized");
              return;
            }
          }
        }
        const delay =
          Math.min(30000, 1000 * 2 ** Math.min(attempt++, 5)) *
          (1 + Math.random() * 0.2);
        await new Promise((resolve) => {
          const done = () => {
            clearTimeout(timer);
            signal.removeEventListener("abort", done);
            resolve();
          };
          const timer = setTimeout(done, delay);
          signal.addEventListener("abort", done, { once: true });
        });
      }
    }
  }
  async command(command, payload, requestId = crypto.randomUUID()) {
    const envelope = { request_id: requestId, command, payload };
    this.pending.set(requestId, envelope);
    this.pendingInstances.set(requestId, this.instance);
    this.savePending();
    return this.send(envelope);
  }
  async send(envelope) {
    const id = envelope.request_id;
    if (this.sending.has(id)) return this.sending.get(id);
    const sending = (async () => {
      try {
        const response = await this.request("/commands", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(envelope),
          signal: AbortSignal.timeout(15000),
        });
        const result = await response.json();
        this.pending.delete(id);
        this.pendingInstances.delete(id);
        this.savePending();
        return result;
      } catch (error) {
        // A transport failure is uncertain. Keep the exact envelope for idempotent retry.
        if (error instanceof OmegaAPIError && error.status < 500) {
          this.pending.delete(id);
          this.pendingInstances.delete(id);
          this.savePending();
        }
        error.requestId = id;
        throw error;
      } finally {
        this.sending.delete(id);
      }
    })();
    this.sending.set(id, sending);
    return sending;
  }
  async retryPending() {
    for (const envelope of [...this.pending.values()]) {
      if (this.pendingInstances.get(envelope.request_id) !== this.instance) {
        throw new OmegaAPIError(
          409,
          "instance_changed",
          "Pending commands belong to a different Omega database. Inspect their effects before clearing retries and submitting new work.",
        );
      }
      await this.send(envelope);
    }
  }
  discardPending() {
    this.pending.clear();
    this.pendingInstances.clear();
    this.savePending();
  }
  async evidence(id) {
    return (await this.request(`/evidence/${encodeURIComponent(id)}`)).json();
  }
}
