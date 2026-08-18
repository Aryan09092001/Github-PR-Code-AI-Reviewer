import os  # Reads environment variables.
from celery import Celery  # The job queue. It runs slow work outside the request.

redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")  # Redis holds the queue. The default is the Docker Compose service name.
app = Celery("learner", broker=redis_url, backend=redis_url)  # broker is where jobs are sent, backend is where results are kept.
app.conf.task_routes = {  # Keeps learning work on its own queue, away from the review queue.
    "trigger_learning": {"queue": "learning"},  # Matches the queue name the webhook service sends to.
}

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file sets up Celery for the learner service, and only that. It points at
# Redis and it says that trigger_learning belongs on the learning queue, kept
# apart from the review queue so slow learning can never hold up a review.
# Note what is not here: no task is defined in this file. Today the work is
# triggered over HTTP instead. The webhook service owns the trigger_learning
# task, and that task simply calls POST /learn in main.py, which does all the
# learning itself. So this file describes a queue nothing in this service is
# currently listening on. It is worth keeping if learning later moves into a
# real Celery worker here, which is the natural next step once learning grows
# slow enough that an HTTP call would time out. Until then it does nothing.
# ---------------------------------------------------------------------------
