# Database Operations — Skill

**Trigger phrases**: sqlite, postgres, postgresql, mysql, mariadb, redis,
mongodb, mongo, query, sql, psql, pg_dump, pg_restore, mysqldump, schema,
table, migration, alembic, prisma migrate, knex migrate, drizzle migrate,
flyway, liquibase, vacuum, analyze, explain, index, slow query, deadlock,
backup db, restore db, dump db, replication, replica, primary, master, slave,
replication lag, cluster, sharding, redis-cli, mongo shell, mongosh, orm,
sqlalchemy, sequelize, typeorm, prisma, drizzle, supabase, planetscale,
neon, cockroachdb, pgbouncer, pgpool, connection pool.

Use when the user asks to inspect, query, modify, or migrate databases.
Default audience: dev/admin who needs the **fastest correct command**, not a
tutorial.

---

## Operating principles

- **Read-only first.** Run a `SELECT … LIMIT 5` before any `UPDATE`/`DELETE`.
- **Always wrap mutations in a transaction**, then `COMMIT` only after
  inspecting `RETURNING` or row count.
- **Backup before destructive ops.** Even a fast `pg_dump -F c -f /tmp/foo.dump`
  is cheap insurance.
- **Use the official client.** `psql`, `mysql`, `sqlite3`, `mongosh`,
  `redis-cli`. Avoid GUI tools for ad-hoc fixes.
- **Don't echo passwords to shell history.** Use `~/.pgpass`, env vars
  (`PGPASSWORD`), or interactive prompts.
- **`EXPLAIN ANALYZE` before adding indexes.** Verify the plan changes.

---

## SQLite

```bash
sqlite3 /path/to/db.sqlite              # interactive
.tables
.schema <table>
SELECT * FROM <table> LIMIT 5;
.mode column
.headers on
.exit

# one-shot
sqlite3 db.sqlite "SELECT count(*) FROM users;"

# backup
sqlite3 db.sqlite ".backup '/tmp/db.bak'"
```

## PostgreSQL

```bash
psql -U postgres -h localhost                 # connect
psql -U postgres -h localhost -d mydb -c "SELECT version();"

# meta
\l           # list databases
\c mydb      # switch
\dt          # list tables
\d users     # describe
\du          # list roles

# fast diagnostics
SELECT pid, state, wait_event_type, wait_event, query
  FROM pg_stat_activity WHERE state != 'idle';

SELECT * FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS) <query>;

VACUUM (VERBOSE, ANALYZE) public.users;

# backup / restore
pg_dump -F c -d mydb -f /tmp/mydb.dump
pg_restore -d mydb_target -j 4 /tmp/mydb.dump

# kill a stuck query
SELECT pg_cancel_backend(<pid>);     -- polite
SELECT pg_terminate_backend(<pid>);  -- forceful
```

## MySQL / MariaDB

```bash
mysql -uroot -p
SHOW DATABASES;
USE mydb;
SHOW TABLES;
DESC users;
SHOW PROCESSLIST;
EXPLAIN <query>;

# backup
mysqldump --single-transaction --quick --routines mydb > /tmp/mydb.sql

# restore
mysql mydb < /tmp/mydb.sql
```

## Redis

```bash
redis-cli                            # interactive
PING
INFO server
DBSIZE
KEYS pattern:*    # AVOID in prod (blocks); use SCAN
SCAN 0 MATCH user:* COUNT 100
TTL <key>
MEMORY USAGE <key>
CONFIG GET maxmemory*

# save / load
redis-cli BGSAVE                     # snapshot to RDB
ls -lh /var/lib/redis/dump.rdb
```

## MongoDB

```bash
mongosh "mongodb://localhost:27017/mydb"
show dbs
use mydb
show collections
db.users.findOne()
db.users.find({email: /@gmail/}).limit(5).pretty()
db.users.createIndex({email: 1}, {unique: true})

# dump / restore
mongodump --uri mongodb://localhost:27017/mydb --out /tmp/mongo
mongorestore --uri mongodb://localhost:27017/mydb_dst /tmp/mongo/mydb
```

---

## Migration tools

- **Alembic (Python/SQLAlchemy)**:
  ```bash
  alembic revision --autogenerate -m "add users.email"
  alembic upgrade head
  alembic downgrade -1
  ```
- **Prisma**:
  ```bash
  npx prisma migrate dev --name add-users-email
  npx prisma migrate deploy        # production
  ```
- **Knex** / **Drizzle** / **TypeORM**: similar shape — `migrate:make`,
  `migrate:latest`, `migrate:rollback`.

---

## Performance triage

1. Slow query log: PG → `log_min_duration_statement = 200ms`. MySQL →
   `slow_query_log = 1`, `long_query_time = 0.2`.
2. `EXPLAIN ANALYZE` and look for `Seq Scan` on large tables → add index.
3. `pg_stat_statements` for top offenders by total time.
4. Connection limits: PG `max_connections`; use pgbouncer if app is
   spawning many short-lived connections.

## Pitfalls

- Forgot `WHERE` on `UPDATE`/`DELETE` in production. Run `BEGIN;` first
  and `SELECT count(*) FROM ... WHERE ...;` before mutating, then
  `COMMIT;`.
- Index everything → write amplification. Add indexes from query
  evidence, not hopes.
- `KEYS *` on a Redis with millions of keys blocks the event loop.
