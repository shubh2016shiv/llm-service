# Reusable LLM output streaming

This package turns provider-neutral semantic events into Server-Sent Events
(SSE). It does not select an LLM, reserve quota, persist conversations, or own
provider credentials. Those responsibilities remain in application services.

## Files and ownership

| Module | Owns | Does not own |
|---|---|---|
| `stream_event.py` | Portable text and structured-output event contracts | HTTP or provider APIs |
| `sse_message.py` | One pre-encoded SSE wire message | JSON serialization |
| `sse_encoder.py` | WHATWG-compatible line encoding | Async scheduling |
| `sse_delivery.py` | Backpressure, heartbeats, sequence IDs, safe termination | LLM schemas |
| `stream_capacity.py` | Per-worker fail-fast capacity leases | Distributed quotas |
| `chat_chunk_adapter.py` | This application's `ChatStreamChunk` translation | Delivery mechanics |

Quota reconciliation and provider cleanup live in
`app/services/streaming_session.py`, because those business guarantees must
also apply to WebSocket, gRPC, or CLI transports.

## Delivery algorithm

```text
producer event iterator
    |
    | one pending anext() only
    v
wait up to heartbeat interval
    |-- timeout --------> emit SSE comment; keep the same read alive
    |-- event ----------> attach thread_id + sequence; emit named SSE event
    |-- source ends ----> emit one complete event
    |-- source fails ---> emit safe error, then complete(status=failed)
    `-- client leaves --> cancel read, close source, emit nothing else
```

There is no token queue. A slow client pauses the async generator and therefore
pauses new producer reads. This is natural backpressure and bounds application
memory to one pending read per connection.

## Text output

Use `text_delta_event()` or adapt a native provider model:

```python
event = text_delta_event("hello")
```

The wire data contains the stable conversation identity:

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "sequence": 1,
  "request_id": "request-123",
  "data": {"text": "hello", "index": 0}
}
```

The producer does not add transport metadata. `SSEStreamDelivery` owns that
job so every producer gets the same thread and ordering guarantees:

```python
delivery = SSEStreamDelivery(heartbeat_interval_seconds=15.0)

async for wire_message in delivery.stream(
    my_event_iterator,
    thread_id=thread_id,
    request_id=request_id,
):
    yield wire_message
```

This iterator can be passed to FastAPI's `StreamingResponse`, Starlette, or any
other server that accepts an asynchronous iterator of strings. Framework code
belongs outside this package.

## Structured output

Structured streaming uses parsed JSON-Pointer updates instead of invalid JSON
fragments. This lets clients apply each update deterministically:

```python
delta = StructuredOutputDelta(
    operation="replace",
    path="/customer/name",
    value="Ada",
)
event = structured_delta_event(delta)
```

The resulting wire event remains valid JSON at every step:

```text
event: structured_delta
data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000","sequence":1,"request_id":"request-123","data":{"operation":"replace","path":"/customer/name","value":"Ada"}}
```

A provider-specific incremental JSON parser belongs before this boundary. Once
it produces `StructuredOutputDelta`, SSE delivery is identical for OpenAI,
Anthropic, Bedrock, local models, or a non-LLM producer.

On the client, keep one in-progress document per `thread_id`, reject sequence
gaps, apply each operation at its JSON Pointer path, and publish the document
only after `complete` reports `completed`. An `error` followed by
`complete(status=failed)` means the partial document must not be treated as a
validated final result.

## Thread identity

`thread_id` identifies the conversation and is supplied by the caller. Reuse
it for later turns in that conversation. `request_id` identifies one HTTP
attempt, while `sequence` orders events only inside that attempt. These values
have different lifetimes and should not be substituted for one another.

This service currently echoes the thread identifier; it does not load thread
history. If conversation persistence is added, every read and write must scope
the thread by the authenticated tenant and user so knowing a UUID cannot grant
access to another caller's conversation.

## Production deployment

- Keep the per-worker limit no larger than the provider connection pool.
- Scale using stateless workers behind a load balancer.
- Disable proxy response buffering.
- Set proxy idle timeout above the heartbeat interval.
- Drain workers during deployment so existing streams can finish.
- Track active streams, rejected admissions, first-event latency, duration,
  disconnects, and terminal status.
- Do not put Redis or Kafka in the token hot path. Use them only for durable
  replay metadata or terminal audit events when those features are required.

The JSON envelope carries `thread_id` and `sequence`; the transport does not
emit an SSE `id` field. Browsers send `Last-Event-ID` when reconnecting to a
stream that uses SSE IDs, which implies a replay contract. Add IDs only when a
durable event store and a handler that honors `Last-Event-ID` both exist.
