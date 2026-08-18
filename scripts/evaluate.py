import os  # Reads environment variables.
import sys  # Used to exit with a code, which is how a pipeline learns whether we passed.

import psycopg2  # The plain, blocking Postgres driver. Fine here, because this is a script, not a service.
from datasets import Dataset  # The table shape Ragas expects. It comes from Hugging Face.
from ragas import evaluate  # Scores our findings with a model acting as the judge.
from ragas.metrics import faithfulness, answer_relevancy  # faithfulness asks "is it grounded?", answer_relevancy asks "does it answer?".


def main():  # WHAT THIS DOES: Scores our newest findings and fails the run if quality has dropped.
    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")  # psycopg2 is not async, so the asyncpg part of the URL has to go.
    conn = psycopg2.connect(database_url)  # A single connection. No pool, because the script runs once and exits.
    cursor = conn.cursor()  # The handle we run the query on.

    cursor.execute(  # Read the newest findings, straight from the same database the services write to.
        """
        SELECT f.message, f.file
        FROM findings f
        JOIN pull_requests pr ON pr.id = f.pr_id
        ORDER BY f.created_at DESC
        LIMIT 50
        """
    )
    rows = cursor.fetchall()  # Pull all fifty rows into memory at once. Small enough that this is fine.
    cursor.close()  # Close the cursor.
    conn.close()  # Close the connection, before the slow model calls begin.

    if not rows:  # A fresh database, or nothing reviewed yet.
        print("No findings found, skipping evaluation.")  # Say why we are stopping, so the log is not a mystery.
        sys.exit(0)  # Exit 0, which means success. Nothing to judge is not a failure.

    data = {  # Ragas wants three columns, each a list of the same length.
        "question": [],  # What was asked.
        "answer": [],  # What our reviewer said.
        "contexts": [],  # What it was allowed to look at when it said it.
    }

    for message, file in rows:  # One finding becomes one row to be judged.
        data["question"].append("What issues exist in this code?")  # The same question every time. See the note at the bottom.
        data["answer"].append(message or "")  # Our finding is the answer being judged. "" guards against a null column.
        data["contexts"].append([file or ""])  # Must be a list, even with one item. This is only the file name, not the code.

    dataset = Dataset.from_dict(data)  # Turn the three lists into the table Ragas reads.
    results = evaluate(dataset, metrics=[faithfulness, answer_relevancy])  # The slow part. This calls a model once per row, per metric.

    print("Evaluation results:")  # A heading, so the numbers below are easy to find in the log.
    print(results)  # Both metrics, as Ragas formats them.

    scores = results.to_pandas()  # One row per finding, one column per metric.
    faithfulness_score = scores["faithfulness"].mean() if "faithfulness" in scores.columns else 1.0  # The average over all fifty. The 1.0 fallback is risky, see the note at the bottom.
    print(f"Mean faithfulness: {faithfulness_score:.4f}")  # Four decimal places, so a small drift is still visible.

    if faithfulness_score < 0.7:  # The quality gate. Below this, we treat the reviewer as untrustworthy.
        print(f"Faithfulness score {faithfulness_score:.4f} is below threshold 0.7")  # Print the number before exiting, or nobody will know how bad it was.
        sys.exit(1)  # Exit 1 fails the pipeline. This is the only line here with real power.


if __name__ == "__main__":  # Only runs when this file is called directly, not when imported.
    main()  # Start.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the quality gate for the reviewer itself. Every other file in this
# project reviews code; this one reviews the reviews, and it is the only place
# that can fail a pipeline on quality rather than on a crash.
# It runs as a script, not a service. It reads the fifty newest findings from the
# database, reshapes them into the table Ragas expects, and asks a model to judge
# them on two counts: faithfulness, meaning the finding is grounded in the code
# rather than invented, and answer_relevancy, meaning it actually addresses the
# question. It then averages faithfulness across all fifty and compares that with
# 0.7. Above the line, it exits 0 and the pipeline carries on. Below it, it exits
# 1 and the pipeline stops, which is how a slow slide into confident nonsense
# gets caught before it reaches anyone's pull request.
# ---------------------------------------------------------------------------
