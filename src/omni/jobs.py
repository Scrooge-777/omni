"""Long-running jobs (benchmarks, stress tests, cleanup) on a background thread, one at a time."""

import itertools
import threading
import time
import traceback

_ids = itertools.count(1)


class Job:
    def __init__(self, kind: str):
        self.id = next(_ids)
        self.kind = kind
        self.status = "running"
        self.progress = 0.0
        self.message = "Starting"
        self.result = None
        self.error = None
        self.started = time.time()
        self.finished = None
        self.live = None  # a live reading for the UI, e.g. current Mbps during a speed test
        self.stop = threading.Event()

    def update(self, progress: float, message: str, live=None):
        self.progress = max(0.0, min(1.0, progress))
        self.message = message
        self.live = live

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 3),
                "message": self.message, "live": self.live, "result": self.result, "error": self.error,
                "started": self.started, "finished": self.finished}


class JobBusy(Exception):
    pass


class JobManager:
    def __init__(self, on_done=None):
        self.jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._on_done = on_done

    def running(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.status == "running"), None)

    def start(self, kind: str, func) -> Job:
        """func(job) does the work and returns the result dict."""
        with self._lock:
            current = self.running()
            if current:
                raise JobBusy(f"{current.kind} is already running; stop it or wait for it to finish")
            job = Job(kind)
            self.jobs[job.id] = job
            del_ids = sorted(self.jobs)[:-20]  # keep the last 20 jobs
            for old in del_ids:
                self.jobs.pop(old, None)

        def runner():
            try:
                job.result = func(job)
                job.status = "stopped" if job.stop.is_set() else "done"
                job.progress = 1.0
                job.message = "Stopped" if job.stop.is_set() else "Done"
                if self._on_done and job.status == "done":
                    self._on_done(job)
            except Exception as exc:
                job.status, job.error, job.message = "failed", str(exc), "Failed"
                traceback.print_exc()
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, name=f"omni-job-{kind}", daemon=True).start()
        return job

    def get(self, job_id: int) -> Job | None:
        return self.jobs.get(job_id)
