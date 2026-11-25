"""The job queue on Redis, for workers on several machines. Same behaviour as SQLiteQueue.

Every change of state is one Lua script, so it happens whole or not at all. Times come from the
Redis server, as machines disagree about the clock.
"""
import json
from typing import Any, Dict, List, Optional

from my_scraper.jobqueue.store import DEFAULT_ATTEMPTS, STATES, Job, new_job_id

NOW = "local t = redis.call('TIME'); local now = tonumber(t[1]) + tonumber(t[2]) / 1000000\n"
# Members sort by creation within a priority: the score is the priority, the name begins with the time.
HELPERS = NOW + """
local p = ARGV[1]
local function job(id) return p .. 'job:' .. id end
local function get(id, field) return redis.call('HGET', job(id), field) end
local function move(id, from, to, score)
  local member = get(id, 'member')
  if from then redis.call('ZREM', p .. from, member) end
  if to then redis.call('ZADD', p .. to, score, member) end
  if to and to ~= 'ready' and to ~= 'delayed' then redis.call('HSET', job(id), 'state', to)
  else redis.call('HSET', job(id), 'state', 'queued') end
end
local function close(id)
  redis.call('HSET', job(id), 'finished', tostring(now))
  local key = get(id, 'dedupe_key')
  if key and key ~= '' then redis.call('DEL', p .. 'dedupe:' .. key) end
end
local function mine(id, worker) return get(id, 'state') == 'running' and get(id, 'worker') == worker end
"""

ENQUEUE = HELPERS + """
local id, payload, priority, delay, max_attempts, dedupe = ARGV[2], ARGV[3], tonumber(ARGV[4]), tonumber(ARGV[5]), ARGV[6], ARGV[7]
if dedupe ~= '' then
  local other = redis.call('GET', p .. 'dedupe:' .. dedupe)
  if other then return other end
  redis.call('SET', p .. 'dedupe:' .. dedupe, id)
end
local member = string.format('%017.6f:%s', now, id)
redis.call('HSET', job(id), 'id', id, 'payload', payload, 'priority', priority, 'state', 'queued', 'attempts', 0,
  'max_attempts', max_attempts, 'run_after', tostring(now + delay), 'created', tostring(now), 'dedupe_key', dedupe,
  'member', member)
if delay > 0 then redis.call('ZADD', p .. 'delayed', now + delay, member)
else redis.call('ZADD', p .. 'ready', -priority, member) end
return id
"""

CLAIM = HELPERS + """
local worker, lease = ARGV[2], tonumber(ARGV[3])
local function id_of(member) return string.sub(member, string.find(member, ':', 1, true) + 1) end
for _, member in ipairs(redis.call('ZRANGEBYSCORE', p .. 'delayed', '-inf', now)) do
  local id = id_of(member)
  move(id, 'delayed', 'ready', -tonumber(get(id, 'priority')))
end
for _, member in ipairs(redis.call('ZRANGEBYSCORE', p .. 'running', '-inf', now)) do
  local id = id_of(member)
  redis.call('HSET', job(id), 'error', 'worker stopped responding')
  if tonumber(get(id, 'attempts')) >= tonumber(get(id, 'max_attempts')) then
    move(id, 'running', 'dead', now); close(id)
  else move(id, 'running', 'ready', -tonumber(get(id, 'priority'))) end
end
local first = redis.call('ZRANGE', p .. 'ready', 0, 0)
if #first == 0 then return false end
local id = id_of(first[1])
move(id, 'ready', 'running', now + lease)
redis.call('HINCRBY', job(id), 'attempts', 1)
redis.call('HSET', job(id), 'worker', worker, 'lease_until', tostring(now + lease))
return id
"""

HEARTBEAT = HELPERS + """
local id, worker, lease = ARGV[2], ARGV[3], tonumber(ARGV[4])
if not mine(id, worker) then return 0 end
redis.call('ZADD', p .. 'running', now + lease, get(id, 'member'))
redis.call('HSET', job(id), 'lease_until', tostring(now + lease))
return 1
"""

COMPLETE = HELPERS + """
local id, worker, result = ARGV[2], ARGV[3], ARGV[4]
if not mine(id, worker) then return 0 end
redis.call('HSET', job(id), 'result', result, 'error', '')
move(id, 'running', 'done', now); close(id)
return 1
"""

FAIL = HELPERS + """
local id, worker, error, retry_in, result = ARGV[2], ARGV[3], ARGV[4], ARGV[5], ARGV[6]
if not mine(id, worker) then return get(id, 'state') end
redis.call('HSET', job(id), 'error', error, 'result', result)
if retry_in == '' or tonumber(get(id, 'attempts')) >= tonumber(get(id, 'max_attempts')) then
  move(id, 'running', 'dead', now); close(id)
  return 'dead'
end
redis.call('HSET', job(id), 'run_after', tostring(now + tonumber(retry_in)))
move(id, 'running', 'delayed', now + tonumber(retry_in))
return 'queued'
"""

RELEASE = HELPERS + """
local id, worker = ARGV[2], ARGV[3]
if not mine(id, worker) then return 0 end
redis.call('HINCRBY', job(id), 'attempts', -1)
move(id, 'running', 'ready', -tonumber(get(id, 'priority')))
return 1
"""

CANCEL = HELPERS + """
local id = ARGV[2]
local state = get(id, 'state')
if state ~= 'queued' and state ~= 'running' then return 0 end
for _, set in ipairs({'ready', 'delayed', 'running'}) do redis.call('ZREM', p .. set, get(id, 'member')) end
move(id, nil, 'cancelled', now); close(id)
return 1
"""

RETRY = HELPERS + """
local id = ARGV[2]
local state = get(id, 'state')
if state ~= 'dead' and state ~= 'cancelled' then return 0 end
redis.call('HSET', job(id), 'attempts', 0, 'error', '', 'finished', '')
move(id, state, 'ready', -tonumber(get(id, 'priority')))
return 1
"""

RESERVE = NOW + """
local slot = math.max(now, tonumber(redis.call('GET', KEYS[1]) or '0'))
local interval = tonumber(ARGV[1])
redis.call('SET', KEYS[1], string.format('%.6f', slot + interval), 'EX', math.ceil(slot - now + interval) + 1)
return tostring(slot - now)
"""

SETS = {"queued": ("ready", "delayed"), "running": ("running",), "done": ("done",), "dead": ("dead",),
        "cancelled": ("cancelled",)}


class RedisQueue:
    def __init__(self, url: str = "", client=None, prefix: str = "scraper:"):
        if client is None:
            import redis  # optional: pip install redis

            client = redis.Redis.from_url(url, decode_responses=True)
        self.client, self.prefix = client, prefix
        self._scripts = {name: client.register_script(source) for name, source in (
            ("enqueue", ENQUEUE), ("claim", CLAIM), ("heartbeat", HEARTBEAT), ("complete", COMPLETE), ("fail", FAIL),
            ("release", RELEASE), ("cancel", CANCEL), ("retry", RETRY))}
        self._reserve = client.register_script(RESERVE)

    def _call(self, script: str, *args):
        return self._scripts[script](args=[self.prefix, *args])

    # --- Producers --------------------------------------------------------------

    def enqueue(self, payload: Dict[str, Any], priority: int = 0, delay: float = 0,
                max_attempts: int = DEFAULT_ATTEMPTS, dedupe_key: Optional[str] = None) -> str:
        return self._call("enqueue", new_job_id(), json.dumps(payload), priority, delay, max_attempts, dedupe_key or "")

    def cancel(self, job_id: str) -> bool:
        return bool(self._call("cancel", job_id))

    def retry(self, job_id: str) -> bool:
        return bool(self._call("retry", job_id))

    # --- Workers ----------------------------------------------------------------

    def claim(self, worker: str, lease: float) -> Optional[Job]:
        job_id = self._call("claim", worker, lease)
        return self.get(job_id) if job_id else None

    def heartbeat(self, job: Job, lease: float) -> bool:
        return bool(self._call("heartbeat", job.id, job.worker, lease))

    def complete(self, job: Job, result: Dict[str, Any]) -> bool:
        return bool(self._call("complete", job.id, job.worker, json.dumps(result, default=str)))

    def fail(self, job: Job, error: str, retry_in: Optional[float], result: Optional[Dict[str, Any]] = None) -> str:
        return self._call("fail", job.id, job.worker, error[:2000], "" if retry_in is None else retry_in,
                          json.dumps(result, default=str) if result else "")

    def release(self, job: Job) -> bool:
        return bool(self._call("release", job.id, job.worker))

    # --- Shared between workers ---------------------------------------------------

    def reserve(self, domain: str, interval: float) -> float:
        return float(self._reserve(keys=[f"{self.prefix}slot:{domain}"], args=[interval]))

    def seen_add(self, key: str, ttl: float = 86400 * 7) -> bool:
        return bool(self.client.set(f"{self.prefix}seen:{key}", 1, nx=True, ex=max(int(ttl), 1)))

    # --- Reading ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        row = self.client.hgetall(f"{self.prefix}job:{job_id}")
        if not row:
            return None

        def number(name: str) -> Optional[float]:
            return float(row[name]) if row.get(name) else None

        return Job(id=row["id"], payload=json.loads(row["payload"]), priority=int(row["priority"]), state=row["state"],
                   attempts=int(row["attempts"]), max_attempts=int(row["max_attempts"]),
                   run_after=number("run_after") or 0.0, created=number("created") or 0.0, finished=number("finished"),
                   worker=row.get("worker"), lease_until=number("lease_until"), error=row.get("error") or None,
                   result=json.loads(row["result"]) if row.get("result") else None,
                   dedupe_key=row.get("dedupe_key") or None)

    def _ids(self, state: str) -> List[str]:
        members = [member for name in SETS[state] for member in self.client.zrange(f"{self.prefix}{name}", 0, -1)]
        return sorted(members, reverse=True)

    def list(self, state: Optional[str] = None, limit: int = 50) -> List[Job]:
        members = sorted((member for name in ([state] if state else STATES) for member in self._ids(name)), reverse=True)
        jobs = (self.get(member.split(":", 1)[1]) for member in members[:limit])
        return [job for job in jobs if job]

    def counts(self) -> Dict[str, int]:
        return {state: sum(self.client.zcard(f"{self.prefix}{name}") for name in SETS[state]) for state in STATES}

    def workers(self) -> List[str]:
        return sorted({job.worker for job in self.list("running") if job.worker})

    def prune(self, older_than_days: float) -> int:
        seconds, micros = self.client.time()
        cutoff = seconds + micros / 1e6 - older_than_days * 86400
        removed = 0
        for state in ("done", "cancelled"):
            for member in self.client.zrangebyscore(f"{self.prefix}{state}", "-inf", cutoff):
                self.client.zrem(f"{self.prefix}{state}", member)
                self.client.delete(f"{self.prefix}job:{member.split(':', 1)[1]}")
                removed += 1
        return removed
