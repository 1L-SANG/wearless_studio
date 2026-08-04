/**
 * Keep one request key while the client cannot know whether the server accepted it.
 * A terminal job error is definitive: replaying that key can only return the same failed
 * job, so the next provider attempt must use a new key.
 */
export function nextRegenerateIdempotencyKey(currentKey, error, makeKey) {
  return error?.terminalJobFailure === true ? makeKey() : currentKey;
}
