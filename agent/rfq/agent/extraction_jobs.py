"""Background extraction of immutable, explicitly model-selected page snapshots."""
import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..log import log


@dataclass(frozen=True)
class Snapshot:
    kind: str
    text: str
    source: str
    via: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.kind, self.source, self.via, self.digest


@dataclass
class Job:
    id: str
    snapshot: Snapshot
    owners: set[str] = field(default_factory=set)
    status: str = "queued"
    result: str = ""
    task: asyncio.Task | None = None


class ExtractionJobs:
    def __init__(self, extract: Callable[..., Awaitable[str]]):
        self.extract = extract
        self.jobs: list[Job] = []
        self.by_key: dict[tuple, Job] = {}

    def enqueue(self, owner: str, snapshot: Snapshot) -> str:
        job = self.by_key.get(snapshot.key)
        if job is None or job.status in ("failed", "cancelled"):
            job = Job(f"j{len(self.jobs) + 1}", snapshot, {owner})
            self.jobs.append(job)
            self.by_key[snapshot.key] = job
            self._log(job)
            job.task = asyncio.create_task(self._run(job))
        else:
            job.owners.add(owner)
        return self.describe(job)

    async def _run(self, job: Job) -> None:
        job.status = "running"
        self._log(job)
        try:
            s = job.snapshot
            job.result = await self.extract(s.kind, s.text, s.source, via=s.via)
            job.status = "failed" if (job.result.startswith("extraction returned nothing")
                                       or "NOTHING of kind" in job.result) else "complete"
        except asyncio.CancelledError:
            job.status, job.result = "cancelled", "deadline/cancellation; no completion claimed"
            raise
        except Exception as error:
            job.status, job.result = "failed", f"{type(error).__name__}: {str(error)[:160]}"
        finally:
            self._log(job)

    @staticmethod
    def _log(job: Job) -> None:
        log("extraction_job", job=job.id, kind=job.snapshot.kind, source=job.snapshot.source[:160],
            content_hash=job.snapshot.digest, status=job.status)

    @staticmethod
    def describe(job: Job) -> str:
        # Job IDs are internal telemetry, not handles accepted by browser tools.
        return (f"snapshot {job.snapshot.kind} {job.status}: {job.snapshot.source}"
                + (f" -> {job.result[:350]}" if job.result else " (snapshot saved; continue independent navigation)"))

    def selected(self, owner: str | None = None) -> list[Job]:
        return [j for j in self.jobs if owner is None or owner in j.owners]

    def pending(self, owner: str | None = None) -> bool:
        return any(j.task is not None and not j.task.done() for j in self.selected(owner))

    def status(self, owner: str) -> str:
        return "\n".join(self.describe(j) for j in self.selected(owner)[-12:])

    async def drain(self, deadline: float, owner: str | None = None) -> None:
        """Settle every selected job before a finish/calculation; deadline cancellation is explicit."""
        tasks = [j.task for j in self.selected(owner) if j.task is not None and not j.task.done()]
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.time()))
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for job in self.selected(owner):
            if job.task and job.task.cancelled() and job.status != "cancelled":
                job.status, job.result = "cancelled", "cancelled before execution; no completion claimed"
                self._log(job)

    async def close(self) -> None:
        await self.drain(time.time())
