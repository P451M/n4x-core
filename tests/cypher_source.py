"""Shared Cypher action snippets for kernel tests. Not a production API."""

from __future__ import annotations

HELPERS = """
import json
import uuid

def _dump_values(values):
    if isinstance(values, str):
        return values
    return json.dumps(values or {}, sort_keys=True)

def _load_values(raw):
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return dict(raw or {})

def _with_values(row):
    if row is None:
        return None
    loaded = dict(row)
    loaded['values'] = _load_values(loaded.get('values'))
    return loaded

def upsert_object(ctx, object_type_id, values, object_id=None):
    object_id = object_id or str(uuid.uuid4())
    rows = ctx.graph.run_cypher(
        '''
        MERGE (n:ApplicationObject {application_id: $application_id, data_space_id: $data_space_id, id: $id})
        SET n.object_type_id = $object_type_id, n.values = $values,
            n.application_id = $application_id, n.data_space_id = $data_space_id
        WITH n
        MATCH (space:DataSpace {application_id: $application_id, id: $data_space_id})
        MERGE (space)-[:OWNS_OBJECT]->(n)
        WITH n
        MATCH (t:ObjectType {id: $object_type_id})
        MERGE (n)-[:INSTANCE_OF]->(t)
        RETURN n.id AS id, n.object_type_id AS object_type_id, n.values AS values
        ''',
        {
            'application_id': ctx.application_id,
            'data_space_id': ctx.data_space_id,
            'id': object_id,
            'object_type_id': object_type_id,
            'values': _dump_values(values),
        },
    )
    return _with_values(rows[0])

def list_objects(ctx, object_type_id=None):
    return [
        _with_values(row)
        for row in ctx.graph.run_cypher(
            '''
            MATCH (n:ApplicationObject {application_id: $application_id, data_space_id: $data_space_id})
            WHERE $object_type_id IS NULL OR n.object_type_id = $object_type_id
            RETURN n.id AS id, n.object_type_id AS object_type_id, n.values AS values
            ''',
            {
                'application_id': ctx.application_id,
                'data_space_id': ctx.data_space_id,
                'object_type_id': object_type_id,
            },
        )
    ]

def get_object(ctx, object_id):
    rows = ctx.graph.run_cypher(
        '''
        MATCH (n:ApplicationObject {application_id: $application_id, data_space_id: $data_space_id, id: $id})
        RETURN n.id AS id, n.object_type_id AS object_type_id, n.values AS values
        ''',
        {
            'application_id': ctx.application_id,
            'data_space_id': ctx.data_space_id,
            'id': object_id,
        },
    )
    return _with_values(rows[0]) if rows else None

def delete_object(ctx, object_id):
    ctx.graph.run_cypher(
        '''
        MATCH (n:ApplicationObject {application_id: $application_id, data_space_id: $data_space_id, id: $id})
        DETACH DELETE n
        ''',
        {
            'application_id': ctx.application_id,
            'data_space_id': ctx.data_space_id,
            'id': object_id,
        },
    )

def merge_rel(ctx, physical_type, relation_type_id, from_id, to_id, values=None, relation_id=None, relation_type_revision_id=None):
    relation_id = relation_id or str(uuid.uuid4())
    ctx.graph.run_cypher(
        f'''
        MATCH (from:ApplicationObject {{application_id: $application_id, data_space_id: $data_space_id, id: $from_id}})
        MATCH (to:ApplicationObject {{application_id: $application_id, data_space_id: $data_space_id, id: $to_id}})
        MERGE (from)-[r:{physical_type}]->(to)
        SET r.id = $relation_id, r.application_id = $application_id, r.data_space_id = $data_space_id,
            r.relation_type_id = $relation_type_id, r.relation_type_revision_id = $relation_type_revision_id,
            r.from_object_id = $from_id, r.to_object_id = $to_id,
            r.values = $values
        RETURN r.id AS id
        ''',
        {
            'application_id': ctx.application_id,
            'data_space_id': ctx.data_space_id,
            'from_id': from_id,
            'to_id': to_id,
            'relation_id': relation_id,
            'relation_type_id': relation_type_id,
            'relation_type_revision_id': relation_type_revision_id,
            'values': _dump_values(values),
        },
    )
    return {
        'id': relation_id,
        'relation_type_id': relation_type_id,
        'from_object_id': from_id,
        'to_object_id': to_id,
        'values': values or {},
    }

def list_rels(ctx, relation_type_id=None):
    return [
        _with_values(row)
        for row in ctx.graph.run_cypher(
            '''
            MATCH (from:ApplicationObject)-[r:APP_REL_PLACEHOLDER]->(to:ApplicationObject)
            RETURN r.id AS id, r.relation_type_id AS relation_type_id,
                r.from_object_id AS from_object_id, r.to_object_id AS to_object_id, r.values AS values
            ''',
            {
                'application_id': ctx.application_id,
                'data_space_id': ctx.data_space_id,
                'relation_type_id': relation_type_id,
            },
        )
    ]
"""


def action_source(*functions: str) -> str:
    return HELPERS + "\n\n" + "\n\n".join(functions)
