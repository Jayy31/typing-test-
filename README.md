# News-driven options research and paper trading

Automated research and **paper trading** for NSE F&O stock options. Read `CLAUDE.md`, `docs/DESIGN.md` and
`docs/DESIGN_V2.md` (wins on conflicts) first. No real orders are ever placed.

## Local setup (worker)

```
cp .env.example worker/.env     # fill in DATABASE_URL at minimum
make setup                      # install deps (needs uv)
make migrate                    # create tables in your database
make test
make lint
make run                        # start the scheduler
```

## Docker

```
docker build -f worker/Dockerfile -t news-options-worker .
docker run --env-file worker/.env -v /data/history:/data/history news-options-worker
```
