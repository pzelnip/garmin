"""Import data from a CSV file into the database.
"""

import csv
import logging
import sys

from db import StepEntry, db_session

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def main():
    with open("missing.csv", newline="") as csvfile, db_session() as session:
        reader = csv.DictReader(csvfile)

        for row in reader:
            entry = StepEntry(
                day=row["day"],
                step_count=row["step_count"],
                goal_met=row["goal_met"],
            )
            logging.info(f"Adding entry: {entry}")
            session.add(entry)

        logging.info("Committing changes")
        session.commit()
    logging.info("done")


if __name__ == "__main__":
    exit(main())
