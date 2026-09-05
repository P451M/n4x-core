MCP_AUTHORING_VERSION = "n4x.mcp.authoring.v9"

MCP_AUTHORING_SCHEMA = {
    "version": MCP_AUTHORING_VERSION,
    "scope": "application_and_experience_authoring_core",
    "client_guide": {
        "instructions": "mcp_initialize_instructions",
        "tool": "inspect_client_guide",
        "topics": [
            "orientation",
            "application",
            "experience",
            "mcp_app",
            "operate",
        ],
        "ui_authority": "inspect_experience_design_context",
    },
    "tool_catalog": {
        "complete": False,
        "discovery": "mcp/tools/list",
        "note": (
            "payloads documents the stable core authoring contract; "
            "runtime, administration, and inspection tools are discovered "
            "from the MCP server"
        ),
    },
    "payloads": {
        "inspect_client_guide": ["topic"],
        "create_application": ["application_id", "name"],
        "create_application_revision": [
            "application_id",
            "parent_revision_id",
        ],
        "create_experience": ["experience_id", "name"],
        "retire_experience": [
            "experience_id",
            "expected_active_revision_id",
        ],
        "create_experience_revision": [
            "experience_id",
            "parent_revision_id",
            "ui_profile",
            "application_access",
        ],
        "discard_application_revision": ["application_revision_id"],
        "discard_experience_revision": ["experience_revision_id"],
        "set_experience_application_access": [
            "experience_revision_id",
            "application_access",
        ],
        "create_experience_surface": [
            "experience_revision_id",
            "surface_id",
            "surface_type",
            "surface_type_version",
            "entrypoint",
            "source_paths",
            "config",
        ],
        "inspect_experience_surface": [
            "experience_revision_id",
            "surface_id",
        ],
        "list_experience_surfaces": ["experience_revision_id"],
        "update_experience_surface": [
            "experience_revision_id",
            "surface_id",
        ],
        "delete_experience_surface": [
            "experience_revision_id",
            "surface_id",
        ],
        "write_source_file": ["revision_id", "path", "content", "role"],
        "apply_source_patch": ["revision_id", "path", "patch"],
        "search_source_tree": ["revision_id", "pattern", "context"],
        "read_source_file": ["revision_id", "path"],
        "list_source_tree": ["revision_id"],
        "create_development_deployment": [
            "experience_revision_id",
            "application_revision_ids",
        ],
        "inspect_development_deployment": ["deployment_id"],
        "create_experience_runtime_dependency": [
            "experience_revision_id",
            "package",
            "spec",
        ],
        "create_object_type": ["application_revision_id", "object_type_id", "name"],
        "create_relation_type": [
            "application_revision_id",
            "relation_type_id",
            "name",
            "from_object_type_id",
            "to_object_type_id",
        ],
        "create_action": [
            "application_revision_id",
            "action_id",
            "kind",
            "entrypoint",
            "source_paths",
        ],
        "build_experience_surface": ["experience_revision_id", "surface_id"],
        "inspect_experience_design_context": [
            "experience_revision_id",
            "include_content",
            "last_seen_hash",
        ],
        "validate_experience_revision": ["experience_revision_id"],
        "activate_experience_revision": ["experience_revision_id"],
        "activate_application_revision": ["application_revision_id"],
        "run_draft_action": [
            "application_revision_id",
            "action_id",
            "input_value",
        ],
        "submit_draft_action": [
            "application_revision_id",
            "action_id",
            "input_value",
        ],
        "create_trigger": [
            "application_revision_id",
            "trigger_id",
            "trigger_type",
            "action_id",
        ],
        "create_test_case": [
            "application_revision_id",
            "action_id",
            "input_value",
            "expected_output",
        ],
        "run_active_action": ["application_id", "action_id", "input_value"],
        "inspect_objects": ["application_id"],
        "inspect_relations": ["application_id"],
        "list_development_objects": ["deployment_id", "application_id"],
        "list_development_relations": ["deployment_id", "application_id"],
        "inspect_secret_references": ["application_id"],
        "inspect_credential_records": ["application_id"],
    },
    "ownership": {
        "application_revision": ["python", "schema", "actions", "triggers"],
        "experience_revision": [
            "ui_profile",
            "javascript",
            "source",
            "surfaces",
            "application_access",
        ],
    },
    "action_data_access": {
        "write_path": "ctx.graph.run_cypher or ctx.graph.transaction",
        "identity": "(application_id, data_space_id, id) on ApplicationObject",
        "values": "store fields on ApplicationObject.values",
        "relations": "embed RelationTypeRevision.physical_type in Cypher",
        "commit": (
            "one run_cypher is one commit; group statements with transaction()"
        ),
    },
    "source_file_results": {
        "mutations_and_lists": "metadata_only_without_content",
        "read_source_file": "content_with_optional_line_range",
        "apply_source_patch": "context_addressed_unified_diff",
        "search_source_tree": "capped_regex_matches",
    },
    "inspection_filters": {
        "authoring_inspect": (
            "inspect_objects, inspect_relations, and list_development_* "
            "default to summary without values; limit 50, hard cap 200, offset. "
            "Full values require include_values or get-by-id. "
            "Experience-bridge list_objects is not bounded."
        ),
        "inspect_secret_references": "application_id is required",
    },
    "mutation_serialization": {
        "scope": "outer graph transaction",
        "lock": "N4XRoot",
        "client_behavior": "concurrent mutations may be submitted and are queued by N4X",
        "external_waits": "never held across builds, subprocesses, or secret backends",
    },
    "experience_application_access": {
        "secret_reference_ids": (
            "explicit secret-management allowlist; missing or empty denies access"
        ),
    },
    "experience_design_context": {
        "version": "n4x.experience.design-context.v1",
        "advisory_only": True,
        "runtime_authority": [
            "active_authoring_guide",
            "active_surface_theme",
            "component_palette",
            "experience_bridge_summary",
        ],
        "validation": "validate_experience_revision_is_separate",
    },
    "experience_retirement": {
        "effect": (
            "sets status=disabled and clears the active revision pointer while "
            "preserving revisions, source, dependencies, and build artifacts"
        ),
        "backend_ownership": (
            "never changes Application data, actions, secrets, jobs, or checkpoints"
        ),
    },
}
