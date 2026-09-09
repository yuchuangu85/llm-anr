"""Local immutable evidence snapshots and group-scoped byte references.

The legacy API accepts complete byte strings. The streaming API stores bounded
chunks without constructing a complete source value in Python.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
import uuid


class EvidenceStoreError(ValueError):
    """An invalid, changed, or out-of-scope evidence reference."""


class EvidenceStoreBudgetExceeded(EvidenceStoreError):
    """A finite evidence ingestion or metadata budget was exceeded."""


_COMMITMENT_UNSET = object()


def _identity(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


class EvidenceStore:
    """Context-managed snapshot store. No network, shell, or model execution."""

    C2_SCHEMA_VERSION = 2
    ARTIFACT_COMMITMENT_SCHEME = "anr-evidence-c2-merkle-v2"
    _LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
    DEFAULT_MAX_STREAM_ARTIFACTS = 10_000
    DEFAULT_MAX_LINE_INDEX_ROWS = 1_000_000
    DEFAULT_MAX_CHUNK_ROWS = 1_000_000
    DEFAULT_MAX_INTEGRITY_ROWS = 4_000_000
    DEFAULT_MAX_MIGRATION_ARTIFACTS = 10_000
    DEFAULT_MAX_MIGRATION_BYTES = 8 * 1024 * 1024 * 1024
    DEFAULT_MAX_READ_CHUNK_ROWS = 65_536
    DEFAULT_MAX_READ_LINE_ROWS = 100_000
    DEFAULT_MAX_PROOF_ROWS = 262_144
    DEFAULT_MAX_REFERENCE_READ_BYTES = 256 * 1024

    _BASE_TABLES = ("artifacts", "anr_groups", "group_artifacts", "spans")
    _C2_TABLES = (
        "artifact_chunks", "artifact_chunk_integrity", "artifact_lines",
        "artifact_integrity_nodes", "artifact_commitments",
    )
    _BASE_SCHEMA_OBJECT_ORDER = (
        ("table", "artifacts"),
        ("table", "anr_groups"),
        ("table", "group_artifacts"),
        ("table", "spans"),
    )
    _C2_SCHEMA_OBJECT_ORDER = (
        ("table", "artifact_chunks"),
        ("table", "artifact_chunk_integrity"),
        ("table", "artifact_lines"),
        ("table", "artifact_integrity_nodes"),
        ("index", "artifact_chunks_range_idx"),
        ("index", "artifact_lines_byte_idx"),
        ("table", "artifact_commitments"),
    )
    _SCHEMA_SQL_TOKEN = re.compile(
        r"""'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\]
        |[A-Za-z_][A-Za-z0-9_]*|\d+|>=|<=|<>|!=|==|\|\|
        |[-+*/%<>=(),.]""",
        re.VERBOSE,
    )
    _BASE_INDEXES = {
        "artifacts": (("id", "BINARY"),),
        "anr_groups": (("id", "BINARY"),),
        "group_artifacts": (("group_id", "BINARY"), ("artifact_id", "BINARY")),
        "spans": (("id", "BINARY"),),
    }
    _C2_INDEXES = {
        "artifact_chunks_range_idx": ("artifact_chunks", ("artifact_id", "start_byte"), False),
        "artifact_lines_byte_idx": (
            "artifact_lines", ("artifact_id", "start_byte", "end_byte"), False,
        ),
    }
    _BASE_COLUMNS = {
        "artifacts": (
            ("id", "TEXT", 0, 1), ("source_kind", "TEXT", 1, 0),
            ("logical_path", "TEXT", 1, 0), ("origin_json", "TEXT", 1, 0),
            ("sha256", "TEXT", 1, 0), ("byte_count", "INTEGER", 1, 0),
            ("content", "BLOB", 1, 0),
        ),
        "anr_groups": (("id", "TEXT", 0, 1),),
        "group_artifacts": (
            ("group_id", "TEXT", 1, 1), ("artifact_id", "TEXT", 1, 2),
        ),
        "spans": (
            ("id", "TEXT", 0, 1), ("group_id", "TEXT", 1, 0),
            ("artifact_id", "TEXT", 1, 0), ("start_byte", "INTEGER", 1, 0),
            ("end_byte", "INTEGER", 1, 0),
        ),
    }
    _C2_COLUMNS = {
        "artifact_chunks": (
            ("artifact_id", "TEXT", 1, 1), ("chunk_index", "INTEGER", 1, 2),
            ("start_byte", "INTEGER", 1, 0), ("content", "BLOB", 1, 0),
            ("sha256", "TEXT", 1, 0),
        ),
        "artifact_chunk_integrity": (
            ("artifact_id", "TEXT", 1, 1), ("chunk_count", "INTEGER", 1, 0),
            ("line_count", "INTEGER", 1, 0), ("byte_count", "INTEGER", 1, 0),
            ("sha256", "TEXT", 1, 0), ("chunk_root", "TEXT", 1, 0),
            ("line_root", "TEXT", 1, 0), ("authenticated_root", "TEXT", 1, 0),
        ),
        "artifact_lines": (
            ("artifact_id", "TEXT", 1, 1), ("line_number", "INTEGER", 1, 2),
            ("start_byte", "INTEGER", 1, 0), ("end_byte", "INTEGER", 1, 0),
        ),
        "artifact_integrity_nodes": (
            ("artifact_id", "TEXT", 1, 1), ("tree_kind", "TEXT", 1, 2),
            ("level", "INTEGER", 1, 3), ("node_index", "INTEGER", 1, 4),
            ("hash", "TEXT", 1, 0),
        ),
        "artifact_commitments": (
            ("artifact_id", "TEXT", 0, 1), ("authenticated_root", "TEXT", 1, 0),
        ),
    }
    _BASE_FOREIGN_KEYS = {
        "artifacts": (), "anr_groups": (),
        "group_artifacts": (
            ("artifacts", "artifact_id", "id", "NO ACTION", "NO ACTION"),
            ("anr_groups", "group_id", "id", "NO ACTION", "NO ACTION"),
        ),
        "spans": (
            ("group_artifacts", "group_id", "group_id", "NO ACTION", "NO ACTION"),
            ("group_artifacts", "artifact_id", "artifact_id", "NO ACTION", "NO ACTION"),
        ),
    }
    _C2_FOREIGN_KEYS = {
        table: (("artifacts", "artifact_id", "id", "CASCADE", "CASCADE"),)
        for table in _C2_TABLES
    }
    _LEGACY_CHUNKED_MARKER = b"\x01"
    _MIGRATION_CHUNK_SIZE = 64 * 1024

    _CHUNK_PREDECESSOR_SQL = (
        "SELECT start_byte FROM artifact_chunks INDEXED BY artifact_chunks_range_idx "
        "WHERE artifact_id=? AND start_byte<=? ORDER BY start_byte DESC LIMIT 1"
    )
    _CHUNK_RANGE_SQL = (
        "SELECT chunk_index, start_byte, length(content), sha256 "
        "FROM artifact_chunks INDEXED BY artifact_chunks_range_idx "
        "WHERE artifact_id=? AND start_byte>=? AND start_byte<? ORDER BY start_byte"
    )

    _BASE_SCHEMA = (
        """CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, logical_path TEXT NOT NULL,
            origin_json TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
            content BLOB NOT NULL
        )""",
        "CREATE TABLE anr_groups (id TEXT PRIMARY KEY)",
        """CREATE TABLE group_artifacts (
            group_id TEXT NOT NULL REFERENCES anr_groups(id),
            artifact_id TEXT NOT NULL REFERENCES artifacts(id),
            PRIMARY KEY (group_id, artifact_id)
        )""",
        """CREATE TABLE spans (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
            end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
            FOREIGN KEY (group_id, artifact_id)
                REFERENCES group_artifacts(group_id, artifact_id)
        )""",
    )
    _C2_SCHEMA = (
        """CREATE TABLE artifact_chunks (
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
            start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
            content BLOB NOT NULL CHECK (length(content) > 0),
            sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
            PRIMARY KEY (artifact_id, chunk_index)
        )""",
        """CREATE TABLE artifact_chunk_integrity (
            artifact_id TEXT NOT NULL PRIMARY KEY REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
            line_count INTEGER NOT NULL CHECK (line_count >= 0),
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
            chunk_root TEXT NOT NULL CHECK (length(chunk_root) = 64 AND chunk_root NOT GLOB '*[^0-9a-f]*'),
            line_root TEXT NOT NULL CHECK (length(line_root) = 64 AND line_root NOT GLOB '*[^0-9a-f]*'),
            authenticated_root TEXT NOT NULL CHECK (length(authenticated_root) = 64 AND authenticated_root NOT GLOB '*[^0-9a-f]*')
        )""",
        """CREATE TABLE artifact_lines (
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            line_number INTEGER NOT NULL CHECK (line_number >= 0),
            start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
            end_byte INTEGER NOT NULL CHECK (end_byte > start_byte),
            PRIMARY KEY (artifact_id, line_number)
        )""",
        """CREATE TABLE artifact_integrity_nodes (
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            tree_kind TEXT NOT NULL CHECK (tree_kind IN ('chunk', 'line')),
            level INTEGER NOT NULL CHECK (level >= 0),
            node_index INTEGER NOT NULL CHECK (node_index >= 0),
            hash TEXT NOT NULL CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
            PRIMARY KEY (artifact_id, tree_kind, level, node_index)
        )""",
        "CREATE INDEX artifact_chunks_range_idx ON artifact_chunks(artifact_id, start_byte)",
        "CREATE INDEX artifact_lines_byte_idx ON artifact_lines(artifact_id, start_byte, end_byte)",
        """CREATE TABLE artifact_commitments (
            artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            authenticated_root TEXT NOT NULL
                CHECK (length(authenticated_root) = 64 AND authenticated_root NOT GLOB '*[^0-9a-f]*')
        )""",
    )

    def __init__(
        self, path: str | Path, *, read_only: bool = False,
        max_migration_artifacts: int = DEFAULT_MAX_MIGRATION_ARTIFACTS,
        max_migration_bytes: int = DEFAULT_MAX_MIGRATION_BYTES,
        max_migration_chunk_rows: int = DEFAULT_MAX_CHUNK_ROWS,
        max_migration_line_rows: int = DEFAULT_MAX_LINE_INDEX_ROWS,
        max_migration_integrity_rows: int = DEFAULT_MAX_INTEGRITY_ROWS,
    ):
        self._read_only = read_only
        migration_limits = {
            "artifacts": max_migration_artifacts,
            "bytes": max_migration_bytes,
            "chunks": max_migration_chunk_rows,
            "lines": max_migration_line_rows,
            "integrity": max_migration_integrity_rows,
        }
        for label, value in migration_limits.items():
            self._validate_limit(value, f"Maximum migration {label}")
        self._migration_limits = migration_limits
        if read_only:
            if str(path) == ":memory:":
                raise EvidenceStoreError("Read-only evidence store requires a persistent path")
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            self.connection = sqlite3.connect(path)
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            if read_only:
                self.connection.execute("PRAGMA query_only=ON")
                self._validate_read_only_schema()
            else:
                self._initialize_schema()
        except Exception:
            self.connection.close()
            raise

    @classmethod
    def open_read_only(cls, path: str | Path) -> "EvidenceStore":
        """Open an already-ingested store without migration or mutating probes."""
        return cls(path, read_only=True)

    @classmethod
    def _expected_c2_objects(cls) -> set[tuple[str, str]]:
        return (
            {("table", table) for table in (*cls._BASE_TABLES, *cls._C2_TABLES)}
            | {("index", index) for index in cls._C2_INDEXES}
        )

    def _schema_objects(self) -> set[tuple[str, str]]:
        return {
            (row[0], row[1]) for row in self.connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'view', 'trigger')"
            )
            if not row[1].startswith("sqlite_")
        }

    def _validate_c2_object_manifest(self, objects: set[tuple[str, str]]) -> None:
        if objects != self._expected_c2_objects():
            raise EvidenceStoreError("Unexpected C2 evidence-store schema object")

    def _initialize_schema(self) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.C2_SCHEMA_VERSION):
                raise EvidenceStoreError("Unknown evidence-store schema version")
            objects = self._schema_objects()
            tables = {name for object_type, name in objects if object_type == "table"}
            base_present = tables & set(self._BASE_TABLES)
            c2_present = tables & set(self._C2_TABLES)
            if version == self.C2_SCHEMA_VERSION:
                if base_present != set(self._BASE_TABLES) or c2_present != set(self._C2_TABLES):
                    raise EvidenceStoreError("Incomplete C2 evidence-store schema")
                self._validate_c2_object_manifest(objects)
                self._validate_base_schema()
                self._validate_c2_schema()
            else:
                expected_legacy_objects = {("table", table) for table in self._BASE_TABLES}
                if not objects:
                    for statement in self._BASE_SCHEMA:
                        self.connection.execute(statement)
                    self._validate_base_schema()
                elif objects == expected_legacy_objects:
                    self._validate_base_schema()
                else:
                    raise EvidenceStoreError(
                        "Unrecognized nonempty version-0 evidence-store schema"
                    )
                for statement in self._C2_SCHEMA:
                    self.connection.execute(statement)
                self.connection.execute(f"PRAGMA user_version={self.C2_SCHEMA_VERSION}")
                self._validate_c2_schema()
            self._migrate_inline_artifacts()
            self._validate_foreign_keys()
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()


    def _validate_read_only_schema(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version != self.C2_SCHEMA_VERSION:
            raise EvidenceStoreError("Unknown evidence-store schema version")
        objects = self._schema_objects()
        tables = {name for object_type, name in objects if object_type == "table"}
        if tables & set(self._BASE_TABLES) != set(self._BASE_TABLES) or tables & set(
            self._C2_TABLES
        ) != set(self._C2_TABLES):
            raise EvidenceStoreError("Incomplete C2 evidence-store schema")
        self._validate_c2_object_manifest(objects)
        self._validate_base_schema(probe_constraints=False)
        self._validate_c2_schema(probe_constraints=False)
        self._validate_foreign_keys()
        self._validate_no_inline_artifacts()

    @classmethod
    def _normalize_schema_sql(cls, sql: str) -> tuple[str, ...]:
        tokens: list[str] = []
        cursor = 0
        for match in cls._SCHEMA_SQL_TOKEN.finditer(sql):
            if sql[cursor:match.start()].strip():
                raise EvidenceStoreError("Unrecognized evidence-store schema SQL")
            token = match.group(0)
            if token[0].isalpha() or token[0] == "_":
                token = token.lower()
            tokens.append(token)
            cursor = match.end()
        if sql[cursor:].strip():
            raise EvidenceStoreError("Unrecognized evidence-store schema SQL")
        return tuple(tokens)

    def _validate_canonical_sql(self) -> None:
        schema = (*self._BASE_SCHEMA, *self._C2_SCHEMA)
        object_order = (*self._BASE_SCHEMA_OBJECT_ORDER, *self._C2_SCHEMA_OBJECT_ORDER)
        expected = {
            key: self._normalize_schema_sql(statement)
            for key, statement in zip(object_order, schema)
        }
        observed = {
            (row[0], row[1]): self._normalize_schema_sql(row[2])
            for row in self.connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE (type='table' OR type='index') AND name IN ("
                + ",".join("?" for _ in expected)
                + ")",
                tuple(name for _, name in expected),
            )
        }
        if observed != expected:
            raise EvidenceStoreError(
                "Incompatible canonical C2 schema SQL, including canonical base schema"
            )

    def _observed_columns(self, table: str) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        )

    def _observed_foreign_keys(self, table: str) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (row[2], row[3], row[4], row[5].upper(), row[6].upper())
            for row in self.connection.execute(f"PRAGMA foreign_key_list({table})")
        )

    def _validate_base_schema(self, *, probe_constraints: bool = True) -> None:
        for table in self._BASE_TABLES:
            if (self._observed_columns(table) != self._BASE_COLUMNS[table]
                    or self._observed_foreign_keys(table) != self._BASE_FOREIGN_KEYS[table]):
                raise EvidenceStoreError(f"Incompatible legacy base table {table}")
            indexes = self.connection.execute(f"PRAGMA index_list({table})").fetchall()
            primary_indexes = [row for row in indexes if row[3] == "pk"]
            if len(primary_indexes) != 1 or not bool(primary_indexes[0][2]):
                raise EvidenceStoreError(f"Incompatible legacy base index for {table}")
            key_columns = tuple(
                (row[2], row[4])
                for row in self.connection.execute(
                    f"PRAGMA index_xinfo({primary_indexes[0][1]})"
                )
                if row[5]
            )
            if key_columns != self._BASE_INDEXES[table]:
                raise EvidenceStoreError(f"Incompatible legacy base index for {table}")
        if probe_constraints:
            try:
                self._probe_constraints(base=True)
            except sqlite3.IntegrityError as exc:
                raise EvidenceStoreError(
                    "Evidence-store constraint probe hit an unexpected constraint"
                ) from exc

    def _validate_c2_schema(self, *, probe_constraints: bool = True) -> None:
        for table in self._C2_TABLES:
            if (self._observed_columns(table) != self._C2_COLUMNS[table]
                    or self._observed_foreign_keys(table) != self._C2_FOREIGN_KEYS[table]):
                raise EvidenceStoreError(f"Incompatible {table} schema")

        for index_name, (table, columns, unique) in self._C2_INDEXES.items():
            row = self.connection.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
            ).fetchone()
            index_rows = self.connection.execute(f"PRAGMA index_list({table})").fetchall()
            matching = [item for item in index_rows if item[1] == index_name]
            key_columns = tuple(
                (item[2], item[3], item[4], item[5])
                for item in self.connection.execute(f"PRAGMA index_xinfo({index_name})")
                if item[5]
            )
            expected_columns = tuple((column, 0, "BINARY", 1) for column in columns)
            if (
                row is None or row[0] != table or len(matching) != 1
                or bool(matching[0][2]) != unique
                or matching[0][3] != "c" or bool(matching[0][4])
                or key_columns != expected_columns
            ):
                raise EvidenceStoreError(f"Incompatible {index_name} schema")
        self._validate_canonical_sql()
        if probe_constraints:
            try:
                self._probe_constraints(base=False)
            except sqlite3.IntegrityError as exc:
                raise EvidenceStoreError(
                    "Evidence-store constraint probe hit an unexpected constraint"
                ) from exc

    def _expect_constraint_rejection(self, statement: str, parameters: tuple[Any, ...]) -> None:
        savepoint = "constraint_probe_" + uuid.uuid4().hex
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            try:
                self.connection.execute(statement, parameters)
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_CONSTRAINT_CHECK:
                    raise EvidenceStoreError(
                        "Evidence-store constraint probe hit an unexpected constraint"
                    ) from exc
            else:
                raise EvidenceStoreError("Evidence-store constraint behavior is incompatible")
        finally:
            self.connection.execute(f"ROLLBACK TO {savepoint}")
            self.connection.execute(f"RELEASE {savepoint}")

    def _probe_constraints(self, *, base: bool) -> None:
        probe_savepoint = "constraint_probe_scope_" + uuid.uuid4().hex
        self.connection.execute(f"SAVEPOINT {probe_savepoint}")
        try:
            probe_token = uuid.uuid4().hex
            artifact_id = "constraint-probe-" + probe_token
            group_id = "constraint-probe-group-" + probe_token
            span_id = "constraint-probe-span-" + probe_token
            if artifact_id.startswith(("artifact-", "pending-")):
                raise AssertionError("Constraint probe ID overlaps managed artifact namespace")
            while self.connection.execute(
                "SELECT 1 FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone() is not None or self.connection.execute(
                "SELECT 1 FROM anr_groups WHERE id=?", (group_id,)
            ).fetchone() is not None:
                probe_token = uuid.uuid4().hex
                artifact_id = "constraint-probe-" + probe_token
                group_id = "constraint-probe-group-" + probe_token
                span_id = "constraint-probe-span-" + probe_token
            self.connection.execute(
                "INSERT INTO artifacts VALUES (?, 'probe', 'probe', '{}', ?, 0, X'')",
                (artifact_id, hashlib.sha256(b"").hexdigest()),
            )
            self.connection.execute("INSERT INTO anr_groups VALUES (?)", (group_id,))
            self.connection.execute(
                "INSERT INTO group_artifacts VALUES (?, ?)", (group_id, artifact_id)
            )
            if base:
                rejections = (
                    ("INSERT INTO spans VALUES (?, ?, ?, -1, 0)",
                     (span_id, group_id, artifact_id)),
                    ("INSERT INTO spans VALUES (?, ?, ?, 2, 1)",
                     (span_id, group_id, artifact_id)),
                )
            else:
                good_hash = "0" * 64
                bad_hash = "g" * 64
                rejections = (
                    ("INSERT INTO artifact_chunks VALUES (?, -1, 0, X'01', ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_chunks VALUES (?, 0, -1, X'01', ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_chunks VALUES (?, 0, 0, X'', ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_chunks VALUES (?, 0, 0, X'01', ?)",
                     (artifact_id, bad_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, -1, 0, 0, ?, ?, ?, ?)",
                     (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, -1, 0, ?, ?, ?, ?)",
                     (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, -1, ?, ?, ?, ?)",
                     (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                     (artifact_id, bad_hash, good_hash, good_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                     (artifact_id, good_hash, bad_hash, good_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                     (artifact_id, good_hash, good_hash, bad_hash, good_hash)),
                    ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                     (artifact_id, good_hash, good_hash, good_hash, bad_hash)),
                    ("INSERT INTO artifact_lines VALUES (?, -1, 0, 1)", (artifact_id,)),
                    ("INSERT INTO artifact_lines VALUES (?, 0, -1, 1)", (artifact_id,)),
                    ("INSERT INTO artifact_lines VALUES (?, 0, 0, -1)", (artifact_id,)),
                    ("INSERT INTO artifact_integrity_nodes VALUES (?, 'invalid', 0, 0, ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', -1, 0, ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', 0, -1, ?)",
                     (artifact_id, good_hash)),
                    ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', 0, 0, ?)",
                     (artifact_id, bad_hash)),
                    ("INSERT INTO artifact_commitments VALUES (?, ?)",
                     (artifact_id, bad_hash)),
                )
            for statement, parameters in rejections:
                self._expect_constraint_rejection(statement, parameters)
        finally:
            self.connection.execute(f"ROLLBACK TO {probe_savepoint}")
            self.connection.execute(f"RELEASE {probe_savepoint}")

    def _validate_foreign_keys(self) -> None:
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise EvidenceStoreError(f"Evidence-store foreign key check failed: {violations[0]}")

    def _validate_no_inline_artifacts(self) -> None:
        row = self.connection.execute(
            "SELECT id FROM artifacts WHERE id GLOB 'artifact-*' AND NOT EXISTS ("
            "SELECT 1 FROM artifact_chunk_integrity WHERE artifact_id=artifacts.id"
            ") LIMIT 1"
        ).fetchone()
        if row is not None:
            raise EvidenceStoreError("Inline artifact storage requires writable migration")

    @staticmethod
    def _charge_migration_budget(
        counters: dict[str, int], limits: dict[str, int], kind: str, amount: int,
    ) -> None:
        if counters[kind] + amount > limits[kind]:
            raise EvidenceStoreBudgetExceeded(
                f"Migrated artifact aggregate {kind} exceeds limit {limits[kind]}"
            )
        counters[kind] += amount

    def _migrate_inline_artifacts(self) -> None:
        counters = {kind: 0 for kind in self._migration_limits}
        cursor = self.connection.execute(
            "SELECT id FROM artifacts WHERE id GLOB 'artifact-*' AND NOT EXISTS ("
            "SELECT 1 FROM artifact_chunk_integrity WHERE artifact_id=artifacts.id"
            ") ORDER BY id"
        )
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            self._migrate_inline_artifact(
                row[0], counters=counters, limits=self._migration_limits,
                charge_artifact=True, charge_bytes=True,
            )

    def _migrate_inline_artifact(
        self, artifact_id: str, *, counters: dict[str, int] | None = None,
        limits: dict[str, int] | None = None, charge_artifact: bool = False,
        charge_bytes: bool = False,
    ) -> bool:
        row = self.connection.execute(
            "SELECT rowid, source_kind, logical_path, origin_json, sha256, byte_count, "
            "length(content) FROM artifacts WHERE id=?", (artifact_id,),
        ).fetchone()
        if row is None:
            raise EvidenceStoreError("Stored artifact is missing")
        if self.connection.execute(
            "SELECT 1 FROM artifact_chunk_integrity WHERE artifact_id=?", (artifact_id,),
        ).fetchone() is not None:
            return False
        rowid, source_kind, logical_path, origin_json, stored_sha256, byte_count, content_size = row
        try:
            origin = json.loads(origin_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvidenceStoreError("Stored artifact origin is invalid") from exc
        if artifact_id != self._artifact_id(source_kind, logical_path, origin, stored_sha256):
            raise EvidenceStoreError("Stored artifact identity changed")
        if content_size != byte_count:
            raise EvidenceStoreError("Stored artifact content changed")

        if counters is None:
            counters = {
                "artifacts": 0, "bytes": 0, "chunks": 0, "lines": 0, "integrity": 0,
            }
        if limits is None:
            limits = {
                "artifacts": self.DEFAULT_MAX_MIGRATION_ARTIFACTS,
                "bytes": self.DEFAULT_MAX_MIGRATION_BYTES,
                "chunks": self.DEFAULT_MAX_CHUNK_ROWS,
                "lines": self.DEFAULT_MAX_LINE_INDEX_ROWS,
                "integrity": self.DEFAULT_MAX_INTEGRITY_ROWS,
            }
        if charge_artifact:
            self._charge_migration_budget(counters, limits, "artifacts", 1)
        if charge_bytes:
            self._charge_migration_budget(counters, limits, "bytes", byte_count)

        digest = hashlib.sha256()
        chunk_index = 0
        line_number = 0
        line_start = 0
        byte_cursor = 0
        with self.connection.blobopen("artifacts", "content", rowid, readonly=True) as blob:
            while byte_cursor < byte_count:
                self._charge_migration_budget(counters, limits, "chunks", 1)
                data = blob.read(min(self._MIGRATION_CHUNK_SIZE, byte_count - byte_cursor))
                if not isinstance(data, bytes) or not data:
                    raise EvidenceStoreError("Stored artifact content changed")
                self.connection.execute(
                    "INSERT INTO artifact_chunks VALUES (?, ?, ?, ?, ?)",
                    (artifact_id, chunk_index, byte_cursor, data, hashlib.sha256(data).hexdigest()),
                )
                for offset, value in enumerate(data):
                    if value != 0x0A:
                        continue
                    self._charge_migration_budget(counters, limits, "lines", 1)
                    line_end = byte_cursor + offset + 1
                    self.connection.execute(
                        "INSERT INTO artifact_lines VALUES (?, ?, ?, ?)",
                        (artifact_id, line_number, line_start, line_end),
                    )
                    line_number += 1
                    line_start = line_end
                digest.update(data)
                byte_cursor += len(data)
                chunk_index += 1
        if byte_cursor != byte_count or digest.hexdigest() != stored_sha256:
            raise EvidenceStoreError("Stored artifact content changed")
        if line_start < byte_count:
            self._charge_migration_budget(counters, limits, "lines", 1)
            self.connection.execute(
                "INSERT INTO artifact_lines VALUES (?, ?, ?, ?)",
                (artifact_id, line_number, line_start, byte_count),
            )
            line_number += 1

        integrity_rows = [counters["integrity"]]
        chunk_count, chunk_root = self._build_integrity_tree(
            artifact_id, stored_sha256, "chunk", integrity_rows=integrity_rows,
            max_integrity_rows=limits["integrity"],
        )
        line_count, line_root = self._build_integrity_tree(
            artifact_id, stored_sha256, "line", integrity_rows=integrity_rows,
            max_integrity_rows=limits["integrity"],
        )
        counters["integrity"] = integrity_rows[0]
        if chunk_count != chunk_index or line_count != line_number:
            raise EvidenceStoreError("Migrated artifact index is incomplete")
        self._charge_migration_budget(counters, limits, "integrity", 2)
        authenticated_root = self._authenticated_root(
            artifact_id, stored_sha256, byte_count, chunk_count, line_count,
            chunk_root, line_root,
        )
        self.connection.execute(
            "INSERT INTO artifact_chunk_integrity VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, chunk_count, line_count, byte_count, stored_sha256,
             chunk_root, line_root, authenticated_root),
        )
        self.connection.execute(
            "INSERT INTO artifact_commitments VALUES (?, ?)",
            (artifact_id, authenticated_root),
        )
        self.connection.execute(
            "UPDATE artifacts SET content=? WHERE id=?",
            (self._LEGACY_CHUNKED_MARKER, artifact_id),
        )
        return True

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _validated_origin(origin: dict[str, str]) -> str:
        if not isinstance(origin, dict):
            raise EvidenceStoreError("Explicit artifact origin required")
        origin_type = origin.get("type")
        if origin_type == "file":
            required = allowed = {"type", "path"}
        elif origin_type == "archive_member":
            required = {"type", "container", "member"}
            allowed = required | {"memberOffset"}
        elif origin_type == "directory_fd_snapshot":
            required = allowed = {
                "type", "scheme", "lexicalRoot", "relativePath",
                "rootDevice", "rootInode", "fileDevice", "fileInode",
            }
            if origin.get("scheme") != "anr-directory-fd-snapshot-v1":
                raise EvidenceStoreError("Unsupported directory snapshot origin scheme")
        else:
            raise EvidenceStoreError("Explicit supported artifact origin required")
        if not required <= set(origin) <= allowed or any(
            not isinstance(value, str) or not value for value in origin.values()
        ):
            raise EvidenceStoreError("Invalid artifact origin fields")
        return json.dumps(origin, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _validate_limit(value: int, label: str, *, allow_zero: bool = True) -> None:
        minimum = 0 if allow_zero else 1
        if type(value) is not int or value < minimum:
            qualifier = "nonnegative" if allow_zero else "positive"
            raise EvidenceStoreError(f"{label} must be a {qualifier} integer")

    @classmethod
    def validate_artifact_commitment(cls, value: object) -> dict[str, str]:
        """Return the exact supported external commitment or fail closed."""
        if not isinstance(value, dict) or set(value) != {"scheme", "root"}:
            raise EvidenceStoreError("A strict artifact commitment is required")
        scheme = value.get("scheme")
        root = value.get("root")
        if scheme != cls.ARTIFACT_COMMITMENT_SCHEME:
            raise EvidenceStoreError("Unsupported artifact commitment scheme")
        if not isinstance(root, str) or cls._LOWER_SHA256.fullmatch(root) is None:
            raise EvidenceStoreError("Artifact commitment root must be lowercase SHA-256")
        return {"scheme": scheme, "root": root}

    @classmethod
    def _commitment(cls, root: str) -> dict[str, str]:
        return {"scheme": cls.ARTIFACT_COMMITMENT_SCHEME, "root": root}

    def artifact_commitment(self, group_id: str, artifact_id: str) -> dict[str, str]:
        """Capture the store root for separate retention in a ref or descriptor."""
        metadata = self._artifact_metadata(group_id, artifact_id)
        return self._commitment(metadata[13])

    def _validate_external_commitment(
        self, metadata: tuple[Any, ...], expected: object,
    ) -> dict[str, str]:
        commitment = self.validate_artifact_commitment(expected)
        if commitment["root"] != metadata[13]:
            raise EvidenceStoreError("Artifact commitment does not match stored metadata")
        return commitment

    @staticmethod
    def _tree_leaf(kind: str, artifact_id: str, artifact_sha256: str, *values: Any) -> str:
        del artifact_id
        return _identity("c2-auth-leaf-v2", kind, artifact_sha256, *values)

    @staticmethod
    def _tree_parent(kind: str, left: str, right: str) -> str:
        return _identity("c2-auth-node-v1", kind, left, right)

    @staticmethod
    def _empty_tree_root(kind: str, artifact_id: str, artifact_sha256: str) -> str:
        del artifact_id
        return _identity("c2-auth-empty-v2", kind, artifact_sha256)

    @staticmethod
    def _authenticated_root(
        artifact_id: str, artifact_sha256: str, byte_count: int, chunk_count: int,
        line_count: int, chunk_root: str, line_root: str,
    ) -> str:
        del artifact_id
        return _identity(
            "c2-authenticated-artifact-v2", artifact_sha256, byte_count,
            chunk_count, line_count, chunk_root, line_root,
        )

    def _build_integrity_tree(
        self, artifact_id: str, artifact_sha256: str, kind: str,
        *, integrity_rows: list[int], max_integrity_rows: int,
    ) -> tuple[int, str]:
        if kind == "chunk":
            source = self.connection.execute(
                "SELECT chunk_index, start_byte, length(content), sha256 "
                "FROM artifact_chunks WHERE artifact_id=? ORDER BY chunk_index",
                (artifact_id,),
            )
        else:
            source = self.connection.execute(
                "SELECT line_number, start_byte, end_byte "
                "FROM artifact_lines WHERE artifact_id=? ORDER BY line_number",
                (artifact_id,),
            )
        count = 0
        for row in source:
            expected_index = count
            if row[0] != expected_index:
                raise EvidenceStoreError(f"Stored artifact {kind} sequence is incomplete")
            leaf = self._tree_leaf(kind, artifact_id, artifact_sha256, *row)
            if integrity_rows[0] >= max_integrity_rows:
                raise EvidenceStoreBudgetExceeded(
                    f"Integrity row count exceeds limit {max_integrity_rows}"
                )
            self.connection.execute(
                "INSERT INTO artifact_integrity_nodes VALUES (?, ?, 0, ?, ?)",
                (artifact_id, kind, expected_index, leaf),
            )
            integrity_rows[0] += 1
            count += 1
        if count == 0:
            return 0, self._empty_tree_root(kind, artifact_id, artifact_sha256)

        level = 0
        width = count
        while width > 1:
            cursor = self.connection.execute(
                "SELECT node_index, hash FROM artifact_integrity_nodes "
                "WHERE artifact_id=? AND tree_kind=? AND level=? ORDER BY node_index",
                (artifact_id, kind, level),
            )
            next_index = 0
            while True:
                left = cursor.fetchone()
                if left is None:
                    break
                right = cursor.fetchone()
                if left[0] != next_index * 2:
                    raise EvidenceStoreError("Integrity tree construction is incomplete")
                if right is not None and right[0] != left[0] + 1:
                    raise EvidenceStoreError("Integrity tree construction is incomplete")
                right_hash = right[1] if right is not None else left[1]
                if integrity_rows[0] >= max_integrity_rows:
                    raise EvidenceStoreBudgetExceeded(
                        f"Integrity row count exceeds limit {max_integrity_rows}"
                    )
                self.connection.execute(
                    "INSERT INTO artifact_integrity_nodes VALUES (?, ?, ?, ?, ?)",
                    (artifact_id, kind, level + 1, next_index,
                     self._tree_parent(kind, left[1], right_hash)),
                )
                integrity_rows[0] += 1
                next_index += 1
            width = (width + 1) // 2
            level += 1
        root = self.connection.execute(
            "SELECT hash FROM artifact_integrity_nodes "
            "WHERE artifact_id=? AND tree_kind=? AND level=? AND node_index=0",
            (artifact_id, kind, level),
        ).fetchone()
        if root is None:
            raise EvidenceStoreError("Integrity tree root is missing")
        return count, root[0]

    def _verify_tree_leaves(
        self, artifact_id: str, artifact_sha256: str, kind: str, count: int,
        expected_root: str, indexed_values: list[tuple[int, tuple[Any, ...]]],
    ) -> None:
        if not indexed_values:
            return
        indexes = [item[0] for item in indexed_values]
        if indexes != list(range(indexes[0], indexes[-1] + 1)) or not 0 <= indexes[0] <= indexes[-1] < count:
            raise EvidenceStoreError(f"Stored artifact {kind} integrity proof is invalid")
        current = {
            index: self._tree_leaf(kind, artifact_id, artifact_sha256, *values)
            for index, values in indexed_values
        }
        level = 0
        width = count
        proof_rows = 0
        while True:
            first = min(current)
            last = max(current)
            proof_first = first - 1 if first % 2 else first
            proof_last = last + 1 if last % 2 == 0 and last + 1 < width else last
            stored: dict[int, str] = {}
            for node_index, node_hash in self.connection.execute(
                "SELECT node_index, hash FROM artifact_integrity_nodes "
                "WHERE artifact_id=? AND tree_kind=? AND level=? "
                "AND node_index>=? AND node_index<=? ORDER BY node_index",
                (artifact_id, kind, level, proof_first, proof_last),
            ):
                proof_rows += 1
                if proof_rows > self.DEFAULT_MAX_PROOF_ROWS:
                    raise EvidenceStoreError(
                        f"Stored artifact {kind} integrity proof exceeds row limit"
                    )
                stored[node_index] = node_hash
            if any(stored.get(index) != value for index, value in current.items()):
                raise EvidenceStoreError(f"Stored artifact {kind} integrity check failed")
            if width == 1:
                if current.get(0) != expected_root:
                    raise EvidenceStoreError(f"Stored artifact {kind} integrity root changed")
                return
            proof_values = dict(stored)
            next_values: dict[int, str] = {}
            for left_index in range(proof_first, proof_last + 1, 2):
                left = proof_values.get(left_index)
                if left is None:
                    raise EvidenceStoreError(f"Stored artifact {kind} integrity proof is incomplete")
                right_index = left_index + 1
                right = proof_values.get(right_index, left) if right_index >= width else proof_values.get(right_index)
                if right is None:
                    raise EvidenceStoreError(f"Stored artifact {kind} integrity proof is incomplete")
                next_values[left_index // 2] = self._tree_parent(kind, left, right)
            current = next_values
            width = (width + 1) // 2
            level += 1

    @staticmethod
    def _artifact_id(
        source_kind: str, logical_path: str, origin: dict[str, str], sha256: str,
    ) -> str:
        return "artifact-" + _identity(source_kind, logical_path, origin, sha256)

    @staticmethod
    def _tree_row_count(leaf_count: int) -> int:
        rows = 0
        width = leaf_count
        while width > 0:
            rows += width
            if width == 1:
                break
            width = (width + 1) // 2
        return rows

    def _matching_artifact_id(
        self, source_kind: str, logical_path: str, origin_json: str, sha256: str,
        *, exclude_id: str | None = None,
    ) -> str | None:
        cursor = self.connection.execute(
            "SELECT id FROM artifacts WHERE source_kind=? AND logical_path=? "
            "AND origin_json=? AND sha256=? AND id<>? ORDER BY id LIMIT 2",
            (source_kind, logical_path, origin_json, sha256, exclude_id or ""),
        )
        first = cursor.fetchone()
        second = cursor.fetchone()
        if second is not None:
            raise EvidenceStoreError("Canonical artifact identity collision")
        return first[0] if first is not None else None

    def _validate_existing_artifact(
        self, artifact_id: str, *, source_kind: str, logical_path: str,
        origin_json: str, sha256: str, byte_count: int,
        max_chunk_rows: int, max_line_rows: int, max_integrity_rows: int,
    ) -> tuple[int, int, int]:
        metadata = self._artifact_metadata("", artifact_id, require_attachment=False)
        if metadata[:5] != (source_kind, logical_path, origin_json, sha256, byte_count):
            raise EvidenceStoreError("Canonical artifact identity collision")
        if metadata[6] is None:
            return (0, 0, 0)

        chunk_count, line_count = metadata[6], metadata[7]
        if chunk_count > max_chunk_rows or line_count > max_line_rows:
            raise EvidenceStoreBudgetExceeded("Existing artifact exceeds validation row limits")
        expected_integrity_rows = (
            self._tree_row_count(chunk_count) + self._tree_row_count(line_count)
        )
        if expected_integrity_rows + 2 > max_integrity_rows:
            raise EvidenceStoreBudgetExceeded("Existing artifact exceeds integrity row limit")

        line_cursor = self.connection.execute(
            "SELECT line_number, start_byte, end_byte FROM artifact_lines "
            "WHERE artifact_id=? ORDER BY line_number", (artifact_id,),
        )
        next_line = line_cursor.fetchone()
        line_number = 0
        line_start = 0
        observed_lines = 0
        observed_chunks = 0
        expected_start = 0
        digest = hashlib.sha256()
        chunk_batch: list[tuple[int, tuple[Any, ...]]] = []
        line_batch: list[tuple[int, tuple[Any, ...]]] = []

        chunk_cursor = self.connection.execute(
            "SELECT chunk_index, start_byte, content, sha256 FROM artifact_chunks "
            "WHERE artifact_id=? ORDER BY chunk_index", (artifact_id,),
        )
        for chunk_index, chunk_start, content, stored_sha256 in chunk_cursor:
            if observed_chunks >= max_chunk_rows:
                raise EvidenceStoreBudgetExceeded("Existing artifact exceeds chunk row limit")
            if (chunk_index != observed_chunks or chunk_start != expected_start
                    or not isinstance(content, bytes) or not content
                    or hashlib.sha256(content).hexdigest() != stored_sha256):
                raise EvidenceStoreError("Stored artifact chunk integrity check failed")
            chunk_values = (chunk_index, chunk_start, len(content), stored_sha256)
            chunk_batch.append((chunk_index, chunk_values))
            if len(chunk_batch) == 128:
                self._verify_tree_leaves(
                    artifact_id, sha256, "chunk", chunk_count, metadata[10], chunk_batch
                )
                chunk_batch.clear()
            for offset, value in enumerate(content):
                if value != 0x0A:
                    continue
                line_end = chunk_start + offset + 1
                expected_line = (line_number, line_start, line_end)
                if next_line != expected_line:
                    raise EvidenceStoreError("Stored line index does not match artifact bytes")
                line_batch.append((line_number, expected_line))
                if len(line_batch) == 128:
                    self._verify_tree_leaves(
                        artifact_id, sha256, "line", line_count, metadata[11], line_batch
                    )
                    line_batch.clear()
                observed_lines += 1
                if observed_lines > max_line_rows:
                    raise EvidenceStoreBudgetExceeded("Existing artifact exceeds line row limit")
                line_number += 1
                line_start = line_end
                next_line = line_cursor.fetchone()
            digest.update(content)
            expected_start += len(content)
            observed_chunks += 1

        if line_start < expected_start:
            expected_line = (line_number, line_start, expected_start)
            if next_line != expected_line:
                raise EvidenceStoreError("Stored line index does not match artifact bytes")
            line_batch.append((line_number, expected_line))
            observed_lines += 1
            next_line = line_cursor.fetchone()
        if next_line is not None or observed_lines != line_count:
            raise EvidenceStoreError("Stored line index is incomplete")
        if chunk_batch:
            self._verify_tree_leaves(
                artifact_id, sha256, "chunk", chunk_count, metadata[10], chunk_batch
            )
        elif chunk_count == 0 and metadata[10] != self._empty_tree_root("chunk", artifact_id, sha256):
            raise EvidenceStoreError("Stored artifact chunk integrity root changed")
        if line_batch:
            self._verify_tree_leaves(
                artifact_id, sha256, "line", line_count, metadata[11], line_batch
            )
        elif line_count == 0 and metadata[11] != self._empty_tree_root("line", artifact_id, sha256):
            raise EvidenceStoreError("Stored artifact line integrity root changed")
        if (observed_chunks != chunk_count or expected_start != byte_count
                or digest.hexdigest() != sha256):
            raise EvidenceStoreError("Stored artifact content changed")

        visited_integrity_rows = 0
        for _ in self.connection.execute(
            "SELECT 1 FROM artifact_integrity_nodes WHERE artifact_id=?", (artifact_id,),
        ):
            visited_integrity_rows += 1
            if visited_integrity_rows > max_integrity_rows:
                raise EvidenceStoreBudgetExceeded("Existing artifact exceeds integrity row limit")
        if visited_integrity_rows != expected_integrity_rows:
            raise EvidenceStoreError("Stored artifact integrity tree is incomplete")
        return (chunk_count, line_count, expected_integrity_rows + 2)

    def add_streamed_group(
        self,
        group_id: str,
        artifacts: Iterable[dict[str, Any]],
        *,
        shared_byte_budget: int,
        chunk_size: int,
        max_artifacts: int = DEFAULT_MAX_STREAM_ARTIFACTS,
        max_line_index_rows: int = DEFAULT_MAX_LINE_INDEX_ROWS,
        max_chunk_rows: int = DEFAULT_MAX_CHUNK_ROWS,
        max_integrity_rows: int = DEFAULT_MAX_INTEGRITY_ROWS,
    ) -> list[dict[str, Any]]:
        """Atomically stream artifacts under shared byte and finite row limits."""
        if not isinstance(group_id, str) or not group_id:
            raise EvidenceStoreError("A nonempty group ID is required")
        self._validate_limit(shared_byte_budget, "Shared byte budget")
        self._validate_limit(chunk_size, "Chunk size", allow_zero=False)
        self._validate_limit(max_artifacts, "Maximum artifact count")
        self._validate_limit(max_line_index_rows, "Maximum line-index row count")
        self._validate_limit(max_chunk_rows, "Maximum chunk row count")
        self._validate_limit(max_integrity_rows, "Maximum integrity row count")

        consumed = 0
        artifact_count = 0
        line_row_count = 0
        chunk_row_count = 0
        integrity_rows = [0]
        completed: list[dict[str, Any]] = []
        validated_existing_ids: set[str] = set()
        migrated_in_operation_ids: set[str] = set()
        savepoint = "streamed_group_" + uuid.uuid4().hex
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            self.connection.execute("INSERT OR IGNORE INTO anr_groups VALUES (?)", (group_id,))
            for spec in artifacts:
                if artifact_count >= max_artifacts:
                    raise EvidenceStoreBudgetExceeded(
                        f"Streamed artifact count exceeds limit {max_artifacts}"
                    )
                artifact_count += 1
                reader = spec.get("reader")
                source_kind = spec.get("source_kind")
                logical_path = spec.get("logical_path")
                origin = spec.get("origin")
                if not hasattr(reader, "read"):
                    raise EvidenceStoreError("A readable binary artifact stream is required")
                if (not isinstance(source_kind, str) or not source_kind
                        or not isinstance(logical_path, str) or not logical_path):
                    raise EvidenceStoreError("Source kind and logical path are required")
                origin_json = self._validated_origin(origin)
                pending_id = "pending-" + uuid.uuid4().hex
                self.connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pending_id, source_kind, logical_path, origin_json,
                     hashlib.sha256(b"").hexdigest(), 0, b""),
                )
                digest = hashlib.sha256()
                byte_count = 0
                chunk_index = 0
                line_number = 0
                line_start = 0
                while True:
                    remaining = shared_byte_budget - consumed
                    request_size = min(chunk_size, remaining + 1)
                    data = reader.read(request_size)
                    if not isinstance(data, bytes):
                        raise EvidenceStoreError("Artifact stream must return bytes")
                    if len(data) > request_size:
                        raise EvidenceStoreError("Artifact stream exceeded requested read size")
                    if not data:
                        break
                    if len(data) > remaining:
                        raise EvidenceStoreBudgetExceeded(
                            f"Shared evidence byte budget exceeded after {consumed} bytes"
                        )
                    if chunk_row_count >= max_chunk_rows:
                        raise EvidenceStoreBudgetExceeded(
                            f"Chunk row count exceeds limit {max_chunk_rows}"
                        )
                    self.connection.execute(
                        "INSERT INTO artifact_chunks VALUES (?, ?, ?, ?, ?)",
                        (pending_id, chunk_index, byte_count, data, hashlib.sha256(data).hexdigest()),
                    )
                    chunk_row_count += 1
                    for offset, value in enumerate(data):
                        if value == 0x0A:
                            if line_row_count >= max_line_index_rows:
                                raise EvidenceStoreBudgetExceeded(
                                    f"Line-index row count exceeds limit {max_line_index_rows}"
                                )
                            line_end = byte_count + offset + 1
                            self.connection.execute(
                                "INSERT INTO artifact_lines VALUES (?, ?, ?, ?)",
                                (pending_id, line_number, line_start, line_end),
                            )
                            line_row_count += 1
                            line_number += 1
                            line_start = line_end
                    digest.update(data)
                    byte_count += len(data)
                    consumed += len(data)
                    chunk_index += 1

                if line_start < byte_count:
                    if line_row_count >= max_line_index_rows:
                        raise EvidenceStoreBudgetExceeded(
                            f"Line-index row count exceeds limit {max_line_index_rows}"
                        )
                    self.connection.execute(
                        "INSERT INTO artifact_lines VALUES (?, ?, ?, ?)",
                        (pending_id, line_number, line_start, byte_count),
                    )
                    line_row_count += 1
                sha256 = digest.hexdigest()
                self.connection.execute(
                    "UPDATE artifacts SET sha256=?, byte_count=? WHERE id=?",
                    (sha256, byte_count, pending_id),
                )
                built_chunk_count, chunk_root = self._build_integrity_tree(
                    pending_id, sha256, "chunk", integrity_rows=integrity_rows,
                    max_integrity_rows=max_integrity_rows,
                )
                built_line_count, line_root = self._build_integrity_tree(
                    pending_id, sha256, "line", integrity_rows=integrity_rows,
                    max_integrity_rows=max_integrity_rows,
                )
                if built_chunk_count != chunk_index:
                    raise EvidenceStoreError("Stored artifact chunk sequence is incomplete")
                authenticated_root = self._authenticated_root(
                    pending_id, sha256, byte_count, built_chunk_count, built_line_count,
                    chunk_root, line_root,
                )
                stream_id = self._artifact_id(source_kind, logical_path, origin, sha256)
                existing_id = self._matching_artifact_id(
                    source_kind, logical_path, origin_json, sha256, exclude_id=pending_id
                )
                if existing_id is not None:
                    migration_counters = {
                        "artifacts": 0, "bytes": 0,
                        "chunks": chunk_row_count, "lines": line_row_count,
                        "integrity": integrity_rows[0],
                    }
                    migration_limits = {
                        "artifacts": max_artifacts, "bytes": shared_byte_budget,
                        "chunks": max_chunk_rows, "lines": max_line_index_rows,
                        "integrity": max_integrity_rows,
                    }
                    migrated = self._migrate_inline_artifact(
                        existing_id, counters=migration_counters, limits=migration_limits,
                    )
                    chunk_row_count = migration_counters["chunks"]
                    line_row_count = migration_counters["lines"]
                    integrity_rows[0] = migration_counters["integrity"]
                    if migrated:
                        migrated_in_operation_ids.add(existing_id)
                    elif (existing_id not in validated_existing_ids
                          and existing_id not in migrated_in_operation_ids):
                        counts = self._validate_existing_artifact(
                            existing_id, source_kind=source_kind, logical_path=logical_path,
                            origin_json=origin_json, sha256=sha256, byte_count=byte_count,
                            max_chunk_rows=max_chunk_rows - chunk_row_count,
                            max_line_rows=max_line_index_rows - line_row_count,
                            max_integrity_rows=max_integrity_rows - integrity_rows[0],
                        )
                        chunk_row_count += counts[0]
                        line_row_count += counts[1]
                        integrity_rows[0] += counts[2]
                        validated_existing_ids.add(existing_id)
                    self.connection.execute("DELETE FROM artifacts WHERE id=?", (pending_id,))
                    artifact_id = existing_id
                else:
                    if integrity_rows[0] + 2 > max_integrity_rows:
                        raise EvidenceStoreBudgetExceeded(
                            f"Integrity row count exceeds limit {max_integrity_rows}"
                        )
                    self.connection.execute(
                        "UPDATE artifacts SET id=? WHERE id=?", (stream_id, pending_id)
                    )
                    artifact_id = stream_id
                    self.connection.execute(
                        "INSERT INTO artifact_chunk_integrity VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (artifact_id, built_chunk_count, built_line_count, byte_count, sha256,
                         chunk_root, line_root, authenticated_root),
                    )
                    self.connection.execute(
                        "INSERT INTO artifact_commitments VALUES (?, ?)",
                        (artifact_id, authenticated_root),
                    )
                    integrity_rows[0] += 2
                    # The canonical artifact was constructed and finalized in
                    # this operation. A later same-call duplicate still pays
                    # its own ingestion/build cost, but must not be rescanned.
                    validated_existing_ids.add(artifact_id)
                self.connection.execute(
                    "INSERT OR IGNORE INTO group_artifacts VALUES (?, ?)", (group_id, artifact_id)
                )
                completed.append({
                    "artifactId": artifact_id, "sourceKind": source_kind,
                    "path": logical_path, "origin": origin, "sha256": sha256,
                    "byteCount": byte_count,
                    "artifactCommitment": self._commitment(
                        self._artifact_metadata(group_id, artifact_id)[13]
                    ),
                })
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {savepoint}")
            self.connection.execute(f"RELEASE {savepoint}")
            raise
        else:
            self.connection.execute(f"RELEASE {savepoint}")
        return completed

    def add_group(self, group_id: str) -> None:
        if not isinstance(group_id, str) or not group_id:
            raise EvidenceStoreError("A nonempty group ID is required")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO anr_groups VALUES (?)", (group_id,))

    def add_artifact(self, content: bytes, *, source_kind: str, logical_path: str,
                     origin: dict[str, str]) -> str:
        """Store exact original bytes with legacy API semantics and committed storage."""
        if not isinstance(content, bytes):
            raise EvidenceStoreError("Artifact content must be original bytes")
        if not isinstance(source_kind, str) or not source_kind or not isinstance(logical_path, str) or not logical_path:
            raise EvidenceStoreError("Source kind and logical path are required")
        origin_json = self._validated_origin(origin)
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = self._artifact_id(source_kind, logical_path, origin, digest)
        savepoint = "inline_artifact_" + uuid.uuid4().hex
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            existing_id = self._matching_artifact_id(
                source_kind, logical_path, origin_json, digest
            )
            if existing_id is None:
                self.connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, source_kind, logical_path, origin_json, digest,
                     len(content), content),
                )
                self._migrate_inline_artifact(artifact_id)
            else:
                self._migrate_inline_artifact(existing_id)
                self._validate_existing_artifact(
                    existing_id, source_kind=source_kind, logical_path=logical_path,
                    origin_json=origin_json, sha256=digest, byte_count=len(content),
                    max_chunk_rows=self.DEFAULT_MAX_CHUNK_ROWS,
                    max_line_rows=self.DEFAULT_MAX_LINE_INDEX_ROWS,
                    max_integrity_rows=self.DEFAULT_MAX_INTEGRITY_ROWS,
                )
                artifact_id = existing_id
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {savepoint}")
            self.connection.execute(f"RELEASE {savepoint}")
            raise
        else:
            self.connection.execute(f"RELEASE {savepoint}")
        return artifact_id

    def attach_artifact(self, group_id: str, artifact_id: str) -> None:
        """Explicit attribution, never inferred from overlapping timestamps."""
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO group_artifacts VALUES (?, ?)", (group_id, artifact_id)
                )
        except sqlite3.IntegrityError as exc:
            raise EvidenceStoreError("Unknown group or artifact") from exc

    def _artifact_metadata(
        self, group_id: str, artifact_id: str, *, require_attachment: bool = True,
    ) -> tuple[Any, ...]:
        attachment_join = "JOIN group_artifacts g ON g.artifact_id=a.id " if require_attachment else ""
        group_clause = "g.group_id=? AND " if require_attachment else ""
        parameters = (group_id, artifact_id) if require_attachment else (artifact_id,)
        row = self.connection.execute(
            "SELECT a.source_kind, a.logical_path, a.origin_json, a.sha256, a.byte_count, "
            "length(a.content), i.chunk_count, i.line_count, i.byte_count, i.sha256, "
            "i.chunk_root, i.line_root, i.authenticated_root, c.authenticated_root "
            "FROM artifacts a " + attachment_join +
            "LEFT JOIN artifact_chunk_integrity i ON i.artifact_id=a.id "
            "LEFT JOIN artifact_commitments c ON c.artifact_id=a.id "
            "WHERE " + group_clause + "a.id=?",
            parameters,
        ).fetchone()
        if row is None:
            raise EvidenceStoreError("Artifact does not belong to requested group")
        origin = json.loads(row[2])
        expected = self._artifact_id(row[0], row[1], origin, row[3])
        if artifact_id != expected:
            raise EvidenceStoreError("Stored artifact identity changed")
        if row[6] is None:
            valid_storage = False
        else:
            expected_root = self._authenticated_root(
                artifact_id, row[3], row[4], row[6], row[7], row[10], row[11],
            )
            valid_storage = (
                row[5] in (0, len(self._LEGACY_CHUNKED_MARKER))
                and 0 <= row[6] <= self.DEFAULT_MAX_CHUNK_ROWS
                and 0 <= row[7] <= self.DEFAULT_MAX_LINE_INDEX_ROWS
                and row[8] == row[4] and row[9] == row[3]
                and row[12] == expected_root and row[13] == expected_root
            )
        if not valid_storage:
            raise EvidenceStoreError("Stored artifact content changed")
        return row

    def add_span(self, group_id: str, artifact_id: str, start_byte: int, end_byte: int) -> str:
        row = self._artifact_metadata(group_id, artifact_id)
        if (type(start_byte) is not int or type(end_byte) is not int
                or not 0 <= start_byte <= end_byte <= row[4]):
            raise EvidenceStoreError("Byte range outside original artifact")
        span_id = "span-" + _identity(group_id, artifact_id, start_byte, end_byte)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO spans VALUES (?, ?, ?, ?, ?)",
                (span_id, group_id, artifact_id, start_byte, end_byte),
            )
        return span_id

    def read_span(
        self, span_id: str, *, group_id: str, source_kind: str, max_bytes: int | None = None,
        expected_artifact_commitment: object = _COMMITMENT_UNSET,
    ) -> dict[str, Any]:
        """Externally authenticate and read a stored span.

        The caller-retained commitment, not mutable SQLite storage markers,
        selects the authenticated path. Chunk-backed records also require a
        positive finite ``max_bytes`` independent of the mutable content marker.
        Legacy callers that only need a local consistency check must opt in to
        ``read_span_internal_integrity_only``.
        """
        if expected_artifact_commitment is _COMMITMENT_UNSET:
            raise EvidenceStoreError("An external artifact commitment is required")
        return self._read_span(
            span_id, group_id=group_id, source_kind=source_kind, max_bytes=max_bytes,
            expected_artifact_commitment=expected_artifact_commitment,
        )

    def read_span_internal_integrity_only(
        self, span_id: str, *, group_id: str, source_kind: str,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Legacy local readback with no external-authentication claim.

        This path verifies the store's internally committed structure, but a
        coherent database rewrite can replace both bytes and internal roots.
        Its result intentionally omits ``artifactCommitment``. Chunk-backed
        records still require a positive finite ``max_bytes``.
        """
        return self._read_span(
            span_id, group_id=group_id, source_kind=source_kind, max_bytes=max_bytes,
            expected_artifact_commitment=_COMMITMENT_UNSET,
        )

    def _read_span(
        self, span_id: str, *, group_id: str, source_kind: str,
        max_bytes: int | None, expected_artifact_commitment: object,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT group_id, artifact_id, start_byte, end_byte FROM spans WHERE id=?", (span_id,)
        ).fetchone()
        if row is None or row[0] != group_id:
            raise EvidenceStoreError("Unknown or cross-group span")
        artifact = self._artifact_metadata(group_id, row[1])
        if artifact[0] != source_kind:
            raise EvidenceStoreError("Cross-source span")
        commitment = None
        if expected_artifact_commitment is not _COMMITMENT_UNSET:
            commitment = self._validate_external_commitment(
                artifact, expected_artifact_commitment
            )
        if (not 0 <= row[2] <= row[3] <= artifact[4]
                or span_id != "span-" + _identity(*row)):
            raise EvidenceStoreError("Stored span identity or range changed")
        streamed = artifact[6] is not None
        preflight_metadata = None
        if streamed:
            if max_bytes is None:
                raise EvidenceStoreError(
                    "Chunk-backed artifacts require an explicit finite read limit"
                )
            effective_max_bytes = max_bytes
            self._validate_limit(effective_max_bytes, "Maximum read size", allow_zero=False)
            if row[3] - row[2] > effective_max_bytes:
                raise EvidenceStoreError("Requested span exceeds bounded read size")
        result = {
            "spanId": span_id, "groupId": group_id, "artifactId": row[1],
            "sourceKind": source_kind, "path": artifact[1], "origin": json.loads(artifact[2]),
            "sha256": artifact[3], "startByte": row[2], "endByte": row[3],
            "coordinateSpace": "original_artifact_bytes",
            "content": self._read_artifact_range(
                row[1], row[2], row[3], streamed=streamed,
                max_chunk_bytes=effective_max_bytes if streamed else None,
                metadata=preflight_metadata,
                integrity=(artifact[6], artifact[9], artifact[10]),
            ),
        }
        if commitment is not None:
            result["artifactCommitment"] = commitment
        return result

    def _intersecting_chunk_metadata(
        self, artifact_id: str, start_byte: int, end_byte: int,
        *, max_rows: int,
    ) -> list[tuple[int, int, int, str]]:
        predecessor = self.connection.execute(
            self._CHUNK_PREDECESSOR_SQL, (artifact_id, start_byte),
        ).fetchone()
        if predecessor is None:
            return []
        rows: list[tuple[int, int, int, str]] = []
        for row in self.connection.execute(
            self._CHUNK_RANGE_SQL, (artifact_id, predecessor[0], end_byte),
        ):
            if len(rows) >= max_rows:
                raise EvidenceStoreError("Artifact chunk metadata row limit exceeded")
            rows.append(row)
        return rows

    def _read_artifact_range(
        self, artifact_id: str, start_byte: int, end_byte: int, *, streamed: bool,
        max_chunk_bytes: int | None = None,
        metadata: list[tuple[int, int, int, str]] | None = None,
        integrity: tuple[int, str, str],
    ) -> bytes:
        if start_byte == end_byte:
            return b""
        requested_bytes = end_byte - start_byte
        if max_chunk_bytes is not None and requested_bytes > max_chunk_bytes:
            raise EvidenceStoreError("Requested span exceeds bounded read size")
        if not streamed:
            raise EvidenceStoreError("Artifact storage is not committed")

        if metadata is None:
            metadata = self._intersecting_chunk_metadata(
                artifact_id, start_byte, end_byte, max_rows=self.DEFAULT_MAX_READ_CHUNK_ROWS
            )
        chunk_bytes = sum(length for _, _, length, _ in metadata)
        if max_chunk_bytes is not None and chunk_bytes > max_chunk_bytes:
            raise EvidenceStoreError("Artifact chunk read budget exceeded")
        if not metadata:
            raise EvidenceStoreError("Stored artifact range is incomplete")
        expected_cursor = start_byte
        previous_chunk_end: int | None = None
        previous_chunk_index: int | None = None
        parts: list[bytes] = []
        for chunk_index, chunk_start, expected_length, expected_sha256 in metadata:
            if (
                expected_length <= 0
                or (previous_chunk_end is not None and chunk_start != previous_chunk_end)
                or (previous_chunk_index is not None and chunk_index != previous_chunk_index + 1)
            ):
                raise EvidenceStoreError("Stored artifact range is incomplete")
            row = self.connection.execute(
                "SELECT content, sha256 FROM artifact_chunks WHERE artifact_id=? AND chunk_index=?",
                (artifact_id, chunk_index),
            ).fetchone()
            chunk = row[0] if row is not None else None
            stored_sha256 = row[1] if row is not None else None
            if (
                not isinstance(chunk, bytes) or len(chunk) != expected_length
                or stored_sha256 != expected_sha256
                or hashlib.sha256(chunk).hexdigest() != expected_sha256
            ):
                raise EvidenceStoreError("Stored artifact chunk integrity check failed")
            local_start = max(start_byte - chunk_start, 0)
            local_end = min(end_byte - chunk_start, len(chunk))
            absolute_start = chunk_start + local_start
            if absolute_start != expected_cursor or local_end < local_start:
                raise EvidenceStoreError("Stored artifact range is incomplete")
            part = chunk[local_start:local_end]
            parts.append(part)
            expected_cursor += len(part)
            previous_chunk_end = chunk_start + len(chunk)
            previous_chunk_index = chunk_index
        self._verify_tree_leaves(
            artifact_id, integrity[1], "chunk", integrity[0], integrity[2],
            [(row[0], row) for row in metadata],
        )
        data = b"".join(parts)
        if expected_cursor != end_byte or len(data) != end_byte - start_byte:
            raise EvidenceStoreError("Stored artifact range is incomplete")
        return data

    def read_artifact_span(
        self, *, group_id: str, artifact_id: str, source_kind: str,
        start_byte: int, end_byte: int, max_bytes: int,
        expected_artifact_commitment: object,
    ) -> dict[str, Any]:
        """Read one range after metadata authorization and bounded chunk preflight."""
        artifact = self._artifact_metadata(group_id, artifact_id)
        commitment = self._validate_external_commitment(
            artifact, expected_artifact_commitment
        )
        if artifact[0] != source_kind:
            raise EvidenceStoreError("Cross-source span")
        self._validate_limit(max_bytes, "Maximum read size")
        if (type(start_byte) is not int or type(end_byte) is not int
                or not 0 <= start_byte <= end_byte <= artifact[4]):
            raise EvidenceStoreError("Byte range outside original artifact")
        if end_byte - start_byte > max_bytes:
            raise EvidenceStoreError("Requested span exceeds bounded read size")
        return {
            "groupId": group_id, "artifactId": artifact_id, "sourceKind": source_kind,
            "path": artifact[1], "origin": json.loads(artifact[2]), "sha256": artifact[3],
            "startByte": start_byte, "endByte": end_byte,
            "coordinateSpace": "original_artifact_bytes",
            "artifactCommitment": commitment,
            "content": self._read_artifact_range(
                artifact_id, start_byte, end_byte, streamed=artifact[6] is not None,
                max_chunk_bytes=max_bytes,
                integrity=(artifact[6], artifact[9], artifact[10]),
            ),
        }

    def _indexed_lines(
        self, artifact_id: str, start_line: int, line_count: int,
    ) -> list[tuple[int, int, int]]:
        rows: list[tuple[int, int, int]] = []
        for row in self.connection.execute(
            "SELECT line_number, start_byte, end_byte FROM artifact_lines "
            "WHERE artifact_id=? AND line_number>=? ORDER BY line_number LIMIT ?",
            (artifact_id, start_line, line_count),
        ):
            if len(rows) >= self.DEFAULT_MAX_READ_LINE_ROWS:
                raise EvidenceStoreError("Line metadata row limit exceeded")
            rows.append(row)
        return rows

    def _validate_indexed_lines(
        self, artifact_id: str, byte_count: int, start_line: int,
        indexed: list[tuple[int, int, int]], data: bytes, range_start: int,
        integrity: tuple[int, str, str],
    ) -> list[tuple[int, int, int, bytes]]:
        if not indexed:
            return []
        if indexed[0][0] != start_line:
            raise EvidenceStoreError("Stored line index is incomplete")
        prior = None
        if start_line > 0:
            prior = self.connection.execute(
                "SELECT start_byte, end_byte FROM artifact_lines "
                "WHERE artifact_id=? AND line_number=?",
                (artifact_id, start_line - 1),
            ).fetchone()
        rows: list[tuple[int, int, int, bytes]] = []
        previous_end = prior[1] if prior is not None else 0
        self._verify_tree_leaves(
            artifact_id, integrity[1], "line", integrity[0], integrity[2],
            [(row[0], row) for row in indexed],
        )
        for offset, (number, start, end) in enumerate(indexed):
            if (
                number != start_line + offset or start != previous_end
                or not 0 <= start < end <= byte_count
            ):
                raise EvidenceStoreError("Stored line index is invalid")
            content = data[start - range_start:end - range_start]
            if len(content) != end - start or b"\n" in content[:-1]:
                raise EvidenceStoreError("Stored line index does not match artifact bytes")
            if end < byte_count and not content.endswith(b"\n"):
                raise EvidenceStoreError("Stored line index does not match artifact bytes")
            rows.append((number, start, end, content))
            previous_end = end
        successor = self.connection.execute(
            "SELECT start_byte FROM artifact_lines WHERE artifact_id=? AND line_number=?",
            (artifact_id, indexed[-1][0] + 1),
        ).fetchone()
        if previous_end < byte_count and (successor is None or successor[0] != previous_end):
            raise EvidenceStoreError("Stored line index is incomplete")
        return rows

    def read_artifact_lines(
        self, *, group_id: str, artifact_id: str, source_kind: str,
        start_line: int, line_count: int, max_bytes: int,
        expected_artifact_commitment: object,
    ) -> list[dict[str, Any]]:
        """Return exact lines through the committed line index and bounded chunks."""
        artifact = self._artifact_metadata(group_id, artifact_id)
        commitment = self._validate_external_commitment(
            artifact, expected_artifact_commitment
        )
        if artifact[0] != source_kind:
            raise EvidenceStoreError("Cross-source span")
        if type(start_line) is not int or start_line < 0 or type(line_count) is not int or line_count <= 0:
            raise EvidenceStoreError("Line offset and count must be bounded positive integers")
        self._validate_limit(max_bytes, "Maximum read size")
        if line_count > self.DEFAULT_MAX_READ_LINE_ROWS or line_count > max_bytes + 1:
            raise EvidenceStoreError("Requested line range exceeds bounded metadata size")

        indexed = self._indexed_lines(artifact_id, start_line, line_count)
        streamed_line_count = artifact[7]
        expected_count = min(line_count, max(0, streamed_line_count - start_line))
        if len(indexed) != expected_count:
            raise EvidenceStoreError("Stored line index is incomplete")
        if expected_count == 0:
            return []
        range_start = indexed[0][1]
        validation_start = range_start - 1 if range_start > 0 else range_start
        range_end = indexed[-1][2]
        if range_end - validation_start > max_bytes:
            raise EvidenceStoreError("Requested line range exceeds bounded read size")
        data = self._read_artifact_range(
            artifact_id, validation_start, range_end, streamed=True,
            max_chunk_bytes=max_bytes,
            integrity=(artifact[6], artifact[9], artifact[10]),
        )
        if validation_start < range_start and data[:1] != b"\n":
            raise EvidenceStoreError("Stored line index does not match artifact bytes")
        scanned = self._validate_indexed_lines(
            artifact_id, artifact[4], start_line, indexed, data, validation_start,
            (artifact[7], artifact[9], artifact[11]),
        )
        return [{
            "groupId": group_id, "artifactId": artifact_id,
            "sourceKind": source_kind, "sha256": artifact[3],
            "artifactCommitment": commitment,
            "lineNumber": number, "startByte": start, "endByte": end, "content": content,
        } for number, start, end, content in scanned]
