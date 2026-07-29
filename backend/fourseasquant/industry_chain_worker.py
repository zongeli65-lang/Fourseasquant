from __future__ import annotations

from fourseasquant.database import database_path, initialize_database
from fourseasquant.industry_chain.worker import run_worker_forever


def main() -> None:
    path = database_path()
    initialize_database(path)
    run_worker_forever(path)


if __name__ == "__main__":
    main()
