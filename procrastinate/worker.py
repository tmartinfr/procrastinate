import logging
import time
from typing import Iterable, Optional, Set

from procrastinate import app, exceptions, jobs, signals, store, tasks, types

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        app: app.App,
        queues: Optional[Iterable[str]] = None,
        import_paths: Optional[Iterable[str]] = None,
    ):
        self.app = app
        self.queues = queues
        self.stop_requested = False
        # Handling the info about the currently running task.
        self.log_context: types.JSONDict = {}
        self.known_missing_tasks: Set[str] = set()

        self.app.perform_import_paths()

    @property
    def job_store(self) -> store.BaseJobStore:
        return self.app.job_store

    def run(self) -> None:
        self.job_store.listen_for_jobs(queues=self.queues)

        with signals.on_stop(self.stop):
            while True:
                self.process_jobs_once()

                if self.stop_requested:
                    logger.debug(
                        "Finished running job at the end of the batch",
                        extra={"action": "stopped_end_batch"},
                    )
                    break

                logger.debug(
                    "Waiting for new jobs", extra={"action": "waiting_for_jobs"}
                )
                self.job_store.wait_for_jobs()

    def process_jobs_once(self) -> None:
        for job in self.job_store.get_jobs(self.queues):
            assert isinstance(job.id, int)

            log_context = {"job": job.get_context()}
            logger.debug(
                "Loaded job info, about to start job",
                extra={"action": "loaded_job_info", **log_context},
            )

            status = jobs.Status.ERROR
            next_attempt_scheduled_at = None
            try:
                self.run_job(job=job)
                status = jobs.Status.DONE
            except exceptions.JobRetry as e:
                status = jobs.Status.TODO
                next_attempt_scheduled_at = e.scheduled_at
            except exceptions.JobError:
                pass
            except exceptions.TaskNotFound as exc:
                logger.exception(
                    f"Task was not found: {exc}",
                    extra={"action": "task_not_found", "exception": str(exc)},
                )
            finally:
                self.job_store.finish_job(
                    job=job, status=status, scheduled_at=next_attempt_scheduled_at
                )
                logger.debug(
                    "Acknowledged job completion",
                    extra={"action": "finish_task", "status": status, **log_context},
                )

            if self.stop_requested:
                break

    def load_task(self, task_name) -> tasks.Task:
        if task_name in self.known_missing_tasks:
            raise exceptions.TaskNotFound(
                f"Cannot run job for task {task_name} previsouly not found"
            )

        try:
            # Simple case: the task is already known
            return self.app.tasks[task_name]
        except KeyError:
            pass

        # Will raise if not found or not a task
        try:
            task = tasks.load_task(task_name)
        except exceptions.ProcrastinateException:
            self.known_missing_tasks.add(task_name)
            raise

        logger.warning(
            f"Task at {task_name} was not registered, it's been loaded dynamically.",
            extra={"action": "load_dynamic_task", "task_name": task_name},
        )

        self.app.tasks[task_name] = task
        return task

    def run_job(self, job: jobs.Job) -> None:
        task_name = job.task_name

        task = self.load_task(task_name=task_name)

        # We store the log context in self. This way, when requesting
        # a stop, we can get details on the currently running task
        # in the logs.
        start_time = time.time()
        log_context = self.log_context = job.get_context()
        log_context["start_timestamp"] = time.time()

        logger.info("Starting job", extra={"action": "start_job", "job": log_context})
        try:
            task_result = task(**job.task_kwargs)
        except Exception as e:
            task_result = None
            log_title = "Job error"
            log_action = "job_error"
            log_level = logging.ERROR
            exc_info = True

            retry_exception = task.get_retry_exception(job)
            if retry_exception:
                raise retry_exception from e
            raise exceptions.JobError() from e

        else:
            log_title = "Job success"
            log_action = "job_success"
            log_level = logging.INFO
            exc_info = False
        finally:
            end_time = log_context["end_timestamp"] = time.time()
            log_context["duration_seconds"] = end_time - start_time
            extra = {"action": log_action, "job": log_context, "result": task_result}
            logger.log(log_level, log_title, extra=extra, exc_info=exc_info)

    def stop(
        self,
        signum: Optional[signals.Signals] = None,
        frame: Optional[signals.FrameType] = None,
    ) -> None:
        self.stop_requested = True
        log_context = self.log_context
        self.job_store.stop()

        logger.info(
            "Stop requested, waiting for current job to finish",
            extra={"action": "stopping_worker", "job": log_context},
        )