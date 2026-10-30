import { test } from "node:test";
import assert from "node:assert/strict";
import { OmegaClient, readSSE } from "../api-client.js";

const memoryStorage = () => {
  const data = new Map();
  return {
    getItem: (key) => data.get(key),
    setItem: (key, value) => data.set(key, value),
  };
};
const callbacks = () => ({
  onSnapshot() {},
  onEvent() {},
  onStatus() {},
  storage: memoryStorage(),
});
const response = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const snapshot = {
  api_version: "1",
  instance_id: "stable-instance",
  cursor: 3,
};

test("SSE parser preserves split UTF-8, multiple frames, comments and cursor checkpoints", async () => {
  const bytes = new TextEncoder().encode(
    ': connected\n\nid: 4\nevent: activity\ndata: {"text":"Ω"}\n\nid: 5\nevent: cursor\ndata: {}\n\n',
  );
  const stream = new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += 3)
        controller.enqueue(bytes.slice(i, i + 3));
      controller.close();
    },
  });
  const frames = [];
  for await (const frame of readSSE(stream)) frames.push(frame);
  assert.equal(frames.length, 2);
  assert.equal(JSON.parse(frames[0].data).text, "Ω");
  assert.equal(frames[1].id, 5);
});

test("uncertain command survives a tab reload and retries exactly the same request ID", async () => {
  const options = callbacks();
  const sent = [];
  const client = new OmegaClient({
    ...options,
    fetcher: async (url, init) => {
      sent.push(JSON.parse(init.body));
      throw new TypeError("network unavailable");
    },
  });
  client.token = "test-token";
  await assert.rejects(
    client.command("goal.create", { title: "Keep one goal" }, "durable-id"),
  );
  assert.equal(client.pending.size, 1);
  const recovered = new OmegaClient({
    ...options,
    fetcher: async (url, init) => {
      sent.push(JSON.parse(init.body));
      return response({ request: { id: "durable-id" }, replayed: true });
    },
  });
  recovered.token = "test-token";
  await recovered.retryPending();
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(recovered.pending.size, 0);
});

test("definitive validation error clears pending command and carries API error", async () => {
  const client = new OmegaClient({
    ...callbacks(),
    fetcher: async () =>
      response(
        { error: { code: "invalid_command", message: "title is required" } },
        400,
      ),
  });
  await assert.rejects(client.command("goal.create", {}, "invalid-id"), {
    code: "invalid_command",
    status: 400,
    message: "title is required",
  });
  assert.equal(client.pending.size, 0);
});

test("connect snapshots before subscribing, replay ignores duplicate sequences, disconnect aborts stream", async () => {
  const urls = [],
    events = [],
    statuses = [];
  let gotEvent;
  const received = new Promise((resolve) => {
    gotEvent = resolve;
  });
  const client = new OmegaClient({
    ...callbacks(),
    onStatus: (status) => statuses.push(status),
    onEvent: (event) => {
      events.push(event);
      gotEvent();
    },
    fetcher: async (url, init) => {
      urls.push(url);
      assert.equal(init.headers.Authorization, "Bearer test-token");
      if (url === "/api/v1/snapshot") return response(snapshot);
      if (url === "/api/v1/session") return response({ authenticated: true });
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'id: 3\nevent: activity\ndata: {"seq":3}\n\nid: 4\nevent: activity\ndata: {"seq":4}\n\n',
              ),
            );
            init.signal.addEventListener(
              "abort",
              () => controller.error(new DOMException("aborted", "AbortError")),
              { once: true },
            );
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    },
  });
  await client.connect("test-token");
  await received;
  assert.equal(urls[0], "/api/v1/snapshot");
  assert.equal(urls[1], "/api/v1/session");
  assert.ok(urls[2].includes("after=3&instance_id=stable-instance"));
  assert.deepEqual(events, [{ seq: 4 }]);
  assert.ok(statuses.includes("connected"));
  client.disconnect();
  assert.equal(client.token, "");
});

test("authentication rejection stops activity retries and requests reconnection", async () => {
  let unauthorized;
  const stopped = new Promise((resolve) => {
    unauthorized = resolve;
  });
  const client = new OmegaClient({
    ...callbacks(),
    onStatus: (status) => {
      if (status === "unauthorized") unauthorized();
    },
    fetcher: async (url) =>
      url === "/api/v1/snapshot"
        ? response(snapshot)
        : response(
            { error: { code: "unauthorized", message: "Token required" } },
            401,
          ),
  });
  await client.connect("");
  await stopped;
  client.disconnect();
});

test("pending commands cannot replay into a replaced database", async () => {
  const options = callbacks();
  let calls = 0;
  const original = new OmegaClient({
    ...options,
    fetcher: async () => {
      throw new Error("lost response");
    },
  });
  original.instance = "original-database";
  await assert.rejects(
    original.command("chat.send", { text: "Execute once" }, "uncertain-effect"),
  );
  const replacement = new OmegaClient({
    ...options,
    fetcher: async () => {
      calls++;
      return response({});
    },
  });
  replacement.instance = "replacement-database";
  await assert.rejects(replacement.retryPending(), {
    code: "instance_changed",
  });
  assert.equal(calls, 0);
  assert.equal(replacement.pending.size, 1);
  replacement.discardPending();
  assert.equal(replacement.pending.size, 0);
});
