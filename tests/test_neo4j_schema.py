from n4x.graph.neo4j import Neo4jConfig, Neo4jGraph, _node_identity


class _Result(list):
    def consume(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def run(self, query: str, **_params):
        self.queries.append(query)
        if query.startswith("SHOW CONSTRAINTS"):
            if any(item.startswith("DROP CONSTRAINT") for item in self.queries[:-1]):
                return _Result()
            return _Result([{"name": "n4x_auth_session_id"}])
        if query.startswith("SHOW INDEXES"):
            return _Result(
                [{"name": "n4x_stale_index", "owningConstraint": None}]
            )
        return _Result()


class _Driver:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def session(self, **_kwargs) -> _Session:
        return self._session


def test_neo4j_schema_statements_are_defined() -> None:
    graph = Neo4jGraph.__new__(Neo4jGraph)
    graph.config = Neo4jConfig(uri="bolt://unused", user="neo4j", password="unused")
    statements = graph.schema_statements()
    assert any("Application" in statement for statement in statements)
    assert any("n4x_experience_id" in statement for statement in statements)
    assert any(
        "n4x_experience_revision_id" in statement for statement in statements
    )
    assert any("n4x_experience_surface_identity" in statement for statement in statements)
    assert any("SourceTree" in statement for statement in statements)
    assert any("ActionRevision" in statement for statement in statements)
    assert any("RuntimeDependency" in statement for statement in statements)
    assert any("ApplicationObject" in statement for statement in statements)
    assert any("ObjectTypeRevision" in statement for statement in statements)
    assert any("RelationTypeRevision" in statement for statement in statements)
    assert any("PythonEnvironment" in statement for statement in statements)
    assert any("BuildInvocation" in statement for statement in statements)
    assert any("BuildArtifact" in statement for statement in statements)
    assert any("N4XRoot" in statement for statement in statements)
    assert any("object_type_id" in statement for statement in statements)
    assert not any("N4X_RELATION" in statement for statement in statements)
    assert not any("N4X_KERNEL" in statement for statement in statements)


def test_surface_node_identity_excludes_non_identity_properties() -> None:
    assert _node_identity(
        "ExperienceSurface",
        {
            "experience_revision_id": "office@1",
            "surface_id": "browser",
            "source_paths": ["src/main.tsx"],
        }
    ) == {
        "experience_revision_id": "office@1",
        "surface_id": "browser",
    }
    assert _node_identity(
        "ApplicationObject",
        {
            "application_id": "notes",
            "data_space_id": "preview",
            "id": "same-id",
            "values": {},
        },
    ) == {
        "application_id": "notes",
        "data_space_id": "preview",
        "id": "same-id",
    }


def test_reset_removes_stale_n4x_schema_objects_before_bootstrap() -> None:
    session = _Session()
    graph = Neo4jGraph.__new__(Neo4jGraph)
    graph.config = Neo4jConfig(
        uri="bolt://unused",
        user="neo4j",
        password="unused",
        database="n4x-dev",
    )
    graph.driver = _Driver(session)

    graph.reset_dev_graph()

    assert "DROP CONSTRAINT `n4x_auth_session_id` IF EXISTS" in session.queries
    assert "DROP INDEX `n4x_stale_index` IF EXISTS" in session.queries
    assert any("CREATE CONSTRAINT n4x_root_id" in query for query in session.queries)
    assert any("MERGE (root:N4XRoot" in query for query in session.queries)
