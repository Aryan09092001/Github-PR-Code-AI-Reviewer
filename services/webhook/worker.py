import httpx  # HTTP client. Here we use the plain one, because Celery workers are not async.
from celery import Celery  # The job queue. It runs slow work outside the request.
import os  # Reads environment variables.

redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")  # Redis holds the queue. The default is the Docker Compose service name.
app = Celery("webhook", broker=redis_url, backend=redis_url)  # broker is where jobs are sent, backend is where results are kept.
app.conf.task_routes = {  # Sends each job to its own queue, so slow learning never blocks reviews.
    "analyze_pr": {"queue": "webhook"},  # Review jobs go to the webhook queue.
    "trigger_learning": {"queue": "learning"},  # Learning jobs go to the learning queue.
}


@app.task(name="analyze_pr")  # The name main.py uses when it queues this job. Keep it fixed.
def analyze_pr(pr_id: str, pr_number: int, repo_full_name: str, head_sha: str, installation_id: int):  # WHAT THIS DOES: Asks the orchestrator to review one pull request.
    with httpx.Client() as client:  # The with block closes the connection for us, even if something fails.
        client.post(
            "http://orchestrator:8002/analyze",  # "orchestrator" is a Docker Compose service name, reachable only inside our network.
            json={  # Everything the orchestrator needs to fetch the diff and post comments back.
                "pr_id": pr_id,  # The row in pull_requests, so it can update the status.
                "pr_number": pr_number,  # The pull request number inside the repository.
                "repo_full_name": repo_full_name,  # The repository, written as "owner/repo".
                "head_sha": head_sha,  # The exact commit to review.
                "installation_id": installation_id,  # Which GitHub App install to use when calling GitHub.
            },
            timeout=120,  # Reviewing calls a model, so it is slow. Two minutes before we give up.
        )


@app.task(name="trigger_learning")  # The name main.py uses when it queues this job. Keep it fixed.
def trigger_learning(repo_full_name: str, pr_id: str):  # WHAT THIS DOES: Asks the learner to draw lessons from a merged pull request.
    with httpx.Client() as client:  # The with block closes the connection for us, even if something fails.
        client.post(
            "http://learner:8004/learn",  # "learner" is a Docker Compose service name, reachable only inside our network.
            json={"repo_full_name": repo_full_name, "pr_id": pr_id},  # Which repo to learn about, and which review to learn from.
            timeout=60,  # Learning is lighter than reviewing, so one minute is enough.
        )

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the background jobs for the webhook service. main.py must
# answer GitHub in milliseconds, so it never does slow work itself. It puts a
# job on Redis and returns straight away. A Celery worker, running as its own
# process, picks the job up and runs the code here.
# There are two jobs. analyze_pr calls the orchestrator to review a pull
# request, and trigger_learning calls the learner after a pull request is
# merged. Each one goes to its own queue, so a slow learning job can never hold
# up a review. Both are plain HTTP calls to services on the internal network.
# Because Celery gives up and retries when a job fails, treat both as work that
# may run more than once, and keep them safe to repeat.
# ---------------------------------------------------------------------------
