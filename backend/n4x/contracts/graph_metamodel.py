GRAPH_METAMODEL_VERSION = "n4x.graph.metamodel.v5"
GRAPH_METAMODEL_SCHEMA_FINGERPRINT = (
    "sha256:9dc97f4767decc7d51021855c5650cbb"
    "75f8751d7f9f79b279561e81ba07ae21"
)

GRAPH_METAMODEL_SCHEMA = {
    "version": GRAPH_METAMODEL_VERSION,
    "schema_fingerprint": GRAPH_METAMODEL_SCHEMA_FINGERPRINT,
    "authority": "n4x.graph.neo4j.Neo4jGraph.schema_statements",
    "persistence": "neo4j",
    "compatibility": {
        "export_import": "n4x.package.v2",
        "definition_only_predecessor": "n4x.graph.metamodel.v4",
    },
}
